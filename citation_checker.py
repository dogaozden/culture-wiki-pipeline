#!/usr/bin/env python3
"""
Citation Checker — Verify fact-quote alignment using LLM.

This is an optional verification stage that checks whether the claims
made in synthesized articles are actually supported by their citations.

Usage:
  python3 citation_checker.py                    # Check all articles
  python3 citation_checker.py --entity-type characters
  python3 citation_checker.py --article dajeil-gelian
  python3 citation_checker.py --dry-run          # Show what would be checked
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src import config
from src.gemini_client import GeminiClient
from src.models import Article
from src.utils import read_json, write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

VERIFICATION_PROMPT = """You are a fact-checker verifying citations in a wiki article.

For each claim-citation pair below, determine if the QUOTE actually supports the CLAIM.

Rate each as:
- SUPPORTED: The quote directly states or clearly implies the claim
- PARTIAL: The quote partially supports the claim but some details are inferred
- UNSUPPORTED: The quote does not support this claim (hallucination)
- UNCLEAR: Cannot determine from the quote alone

Be strict. If the claim adds details not in the quote, mark it PARTIAL or UNSUPPORTED.

CLAIMS TO VERIFY:
{claims}

Return JSON array:
[
  {{"claim_id": 1, "verdict": "SUPPORTED|PARTIAL|UNSUPPORTED|UNCLEAR", "reason": "brief explanation"}},
  ...
]
"""


def extract_claims_with_citations(article: Article) -> List[Dict]:
    """Extract sentences with their citation quotes from an article."""
    import re
    claims = []
    claim_id = 0
    
    # Build citation lookup
    citation_lookup = {c.id: c.quote for c in article.citations}
    
    # Pattern to find sentences with citations
    # Matches text ending with [N] or [N,M] etc
    pattern = re.compile(r'([^.!?]*\[[0-9,\s]+\][^.!?]*[.!?])')
    
    def extract_from_text(text: str):
        nonlocal claim_id
        for match in pattern.finditer(text):
            sentence = match.group(1).strip()
            # Find citation IDs in this sentence
            cite_matches = re.findall(r'\[(\d+(?:\s*,\s*\d+)*)\]', sentence)
            cite_ids = []
            for cm in cite_matches:
                cite_ids.extend([int(x.strip()) for x in cm.split(',')])
            
            # Get quotes for these citations
            quotes = [citation_lookup.get(cid, "NOT FOUND") for cid in cite_ids]
            
            if quotes and any(q != "NOT FOUND" for q in quotes):
                claim_id += 1
                claims.append({
                    "claim_id": claim_id,
                    "claim": sentence,
                    "citation_ids": cite_ids,
                    "quotes": quotes,
                })
    
    extract_from_text(article.summary)
    for section in article.sections:
        extract_from_text(section.content)
    
    return claims


def format_claims_for_prompt(claims: List[Dict], max_claims: int = 20) -> str:
    """Format claims for the verification prompt."""
    lines = []
    for claim in claims[:max_claims]:
        lines.append(f"Claim {claim['claim_id']}:")
        lines.append(f"  CLAIM: \"{claim['claim']}\"")
        for i, quote in enumerate(claim['quotes']):
            lines.append(f"  QUOTE [{claim['citation_ids'][i]}]: \"{quote}\"")
        lines.append("")
    return "\n".join(lines)


def verify_article(article: Article, client: GeminiClient) -> Dict:
    """Verify citations in a single article."""
    claims = extract_claims_with_citations(article)
    
    if not claims:
        return {
            "entity": article.entity_name,
            "status": "NO_CLAIMS",
            "message": "No claims with citations found",
            "results": [],
        }
    
    # Format prompt
    formatted = format_claims_for_prompt(claims)
    prompt = VERIFICATION_PROMPT.format(claims=formatted)
    
    try:
        response = client.synthesize(prompt)  # Reuse synthesize for JSON output
        
        # Parse results
        if isinstance(response, list):
            results = response
        else:
            results = response.get("results", response)
        
        # Analyze results
        verdicts = {"SUPPORTED": 0, "PARTIAL": 0, "UNSUPPORTED": 0, "UNCLEAR": 0}
        for r in results:
            verdict = r.get("verdict", "UNCLEAR").upper()
            if verdict in verdicts:
                verdicts[verdict] += 1
        
        # Determine overall status
        total = len(results)
        unsupported_rate = verdicts["UNSUPPORTED"] / total if total > 0 else 0
        
        if unsupported_rate > 0.2:
            status = "FAIL"
        elif unsupported_rate > 0:
            status = "WARN"
        else:
            status = "PASS"
        
        return {
            "entity": article.entity_name,
            "slug": article.slug,
            "status": status,
            "total_claims": len(claims),
            "verified_claims": len(results),
            "verdicts": verdicts,
            "details": results,
        }
        
    except Exception as e:
        logger.error("Verification failed for %s: %s", article.entity_name, e)
        return {
            "entity": article.entity_name,
            "status": "ERROR",
            "message": str(e),
            "results": [],
        }


def load_articles(entity_type: Optional[str] = None, article_slug: Optional[str] = None) -> List[Article]:
    """Load articles to check."""
    articles = []
    
    if article_slug:
        # Find specific article
        for et in config.ENTITY_TYPES:
            path = config.ARTICLES_DIR / et / f"{article_slug}.json"
            if path.exists():
                articles.append(Article(**read_json(path)))
                break
    elif entity_type:
        # Load all articles of a type
        articles_dir = config.ARTICLES_DIR / entity_type
        if articles_dir.exists():
            for path in articles_dir.glob("*.json"):
                articles.append(Article(**read_json(path)))
    else:
        # Load all articles
        for et in config.ENTITY_TYPES:
            articles_dir = config.ARTICLES_DIR / et
            if articles_dir.exists():
                for path in articles_dir.glob("*.json"):
                    articles.append(Article(**read_json(path)))
    
    return articles


def main():
    parser = argparse.ArgumentParser(description="Verify fact-quote alignment in wiki articles")
    parser.add_argument("--entity-type", help="Check only this entity type")
    parser.add_argument("--article", help="Check only this article (by slug)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be checked")
    parser.add_argument("--output", help="Save results to JSON file")
    parser.add_argument("--limit", type=int, help="Max articles to check")
    args = parser.parse_args()
    
    articles = load_articles(args.entity_type, args.article)
    
    if args.limit:
        articles = articles[:args.limit]
    
    if not articles:
        print("No articles found to check")
        return
    
    print(f"Found {len(articles)} articles to verify")
    
    if args.dry_run:
        print("\nArticles that would be checked:")
        for a in articles:
            claims = extract_claims_with_citations(a)
            print(f"  - {a.entity_name}: {len(claims)} claims with citations")
        return
    
    # Initialize client
    client = GeminiClient()
    client.preflight()
    
    results = []
    for i, article in enumerate(articles):
        print(f"\n[{i+1}/{len(articles)}] Checking {article.entity_name}...")
        result = verify_article(article, client)
        results.append(result)
        
        # Print summary
        if result["status"] == "PASS":
            print(f"  ✅ PASS - {result.get('verdicts', {})}")
        elif result["status"] == "WARN":
            print(f"  ⚠️  WARN - {result.get('verdicts', {})}")
        elif result["status"] == "FAIL":
            print(f"  ❌ FAIL - {result.get('verdicts', {})}")
        else:
            print(f"  ℹ️  {result['status']} - {result.get('message', '')}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for r in results if r["status"] == "PASS")
    warned = sum(1 for r in results if r["status"] == "WARN")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    print(f"  ✅ Passed: {passed}")
    print(f"  ⚠️  Warnings: {warned}")
    print(f"  ❌ Failed: {failed}")
    
    # Save results if requested
    if args.output:
        write_json(results, Path(args.output))
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
