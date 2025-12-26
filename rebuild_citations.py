#!/usr/bin/env python3
"""
Rebuild citations array from inline fact IDs in an article.

Usage:
    python3 rebuild_citations.py sleeper-service.json --entity-type ships_and_minds

This script:
1. Reads an article JSON that has summary/sections but empty or missing citations
2. Extracts all [fact_id] references from the text
3. Looks up each fact in the extraction files to get the quote
4. Rebuilds the citations array and saves the updated article
"""

import argparse
import re
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src import config
from src.synthesizer import _load_extractions, FACT_ID_PATTERN
from src.utils import read_json, write_json, utc_now, slugify
from src.models import MergedEntity


def extract_fact_ids_from_text(text: str) -> set:
    """Extract all fact IDs like [excession_split_001#003] from text."""
    fact_ids = set()
    # Match [anything_with#number] pattern
    pattern = re.compile(r'\[([a-zA-Z0-9_]+#\d{3})\]', re.IGNORECASE)
    for match in pattern.finditer(text):
        fact_ids.add(match.group(1).lower())
    return fact_ids


def build_fact_lookup(entity_type: str) -> dict:
    """Build a lookup from fact_id -> fact info from all extractions."""
    lookup = {}
    extractions = _load_extractions(entity_type)
    
    for extraction in extractions:
        for entity in extraction.entities:
            for info in entity.information:
                fact_id = info.id.lower() if info.id else None
                if fact_id:
                    # Infer book title from chunk_id (e.g. excession_split_001 -> Excession)
                    chunk_id = extraction.chunk_id
                    book_title = chunk_id.split("_")[0].title() if "_" in chunk_id else "Unknown"
                    
                    lookup[fact_id] = {
                        "id": info.id,
                        "book": book_title,
                        "chapter": extraction.chapter_title,
                        "quote": info.quote,
                        "chunk_id": extraction.chunk_id,
                    }
    return lookup


def main():
    parser = argparse.ArgumentParser(
        description="Rebuild citations array from inline fact IDs"
    )
    parser.add_argument("article_path", help="Path to article JSON file")
    parser.add_argument(
        "--entity-type",
        default="ships_and_minds",
        help="Entity type (default: ships_and_minds)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file (default: overwrite input)",
    )
    args = parser.parse_args()

    # Load article
    article_path = Path(args.article_path)
    if not article_path.exists():
        # Try in articles dir
        article_path = config.ARTICLES_DIR / args.entity_type / args.article_path
    
    if not article_path.exists():
        print(f"Error: Article not found at {article_path}", file=sys.stderr)
        sys.exit(1)

    article = read_json(article_path)
    
    # -------------------------------------------------------------------------
    # 1. METADATA FILL (Repair missing fields from merged_entities)
    # -------------------------------------------------------------------------
    entities_path = config.ENTITIES_DIR / f"{args.entity_type}.json"
    if entities_path.exists():
        entities_data = read_json(entities_path)
        merged_entity = None
        
        # Try to find matching entity by filename (slug)
        file_slug = article_path.stem
        
        for e in entities_data.get("entities", []):
            if slugify(e["canonical_name"]) == file_slug:
                merged_entity = MergedEntity(**e)
                break
        
        if merged_entity:
            print(f"Found metadata for '{merged_entity.canonical_name}'", file=sys.stderr)
            
            # Fill missing fields
            if "entity_name" not in article:
                article["entity_name"] = merged_entity.canonical_name
            if "entity_type" not in article:
                article["entity_type"] = args.entity_type
            if "slug" not in article:
                article["slug"] = file_slug
            if "aliases" not in article:
                article["aliases"] = merged_entity.aliases
            if "appears_in" not in article:
                article["appears_in"] = merged_entity.appears_in
            if "prominence_score" not in article:
                # Default score if missing
                article["prominence_score"] = 100 
            if "generated_at" not in article:
                article["generated_at"] = utc_now()
            if "related_entities" not in article:
                article["related_entities"] = []
                
        else:
            print(f"WARNING: Could not find merged entity for slug '{file_slug}'", file=sys.stderr)

    # -------------------------------------------------------------------------
    # 2. CITATION REBUILD
    # -------------------------------------------------------------------------
    
    # Extract all fact IDs from summary and sections
    all_fact_ids = set()
    
    if article.get("summary"):
        all_fact_ids.update(extract_fact_ids_from_text(article["summary"]))
    
    for section in article.get("sections", []):
        content = section.get("content", "")
        all_fact_ids.update(extract_fact_ids_from_text(content))
    
    print(f"Found {len(all_fact_ids)} unique fact IDs in article", file=sys.stderr)
    
    # Build lookup from extractions
    fact_lookup = build_fact_lookup(args.entity_type)
    print(f"Built lookup with {len(fact_lookup)} facts from extractions", file=sys.stderr)
    
    # Rebuild citations
    citations = []
    missing = []
    
    for fact_id in sorted(all_fact_ids):
        if fact_id in fact_lookup:
            citations.append(fact_lookup[fact_id])
        else:
            missing.append(fact_id)
    
    if missing:
        print(f"WARNING: {len(missing)} fact IDs not found in extractions:", file=sys.stderr)
        for fid in missing[:10]:
            print(f"  - {fid}", file=sys.stderr)
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more", file=sys.stderr)
    
    # Update article
    article["citations"] = citations
    
    # Save
    output_path = Path(args.output) if args.output else article_path
    write_json(article, output_path)
    
    print(f"\nRebuilt {len(citations)} citations", file=sys.stderr)
    print(f"Saved to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
