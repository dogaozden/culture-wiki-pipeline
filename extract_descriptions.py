#!/usr/bin/env python3
"""
Extract visual descriptions for entities from source text.

Usage:
  # Test single entity
  python extract_descriptions.py --entity "Sleeper Service" --entity-type ships_and_minds
  
  # Process all major entities (>10 citations) for a book
  python extract_descriptions.py --book excession
  
  # Dry run (no LLM calls)
  python extract_descriptions.py --entity "Dajeil Gelian" --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config
from src.utils import read_json, write_json, slugify

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Prompt for extracting visual descriptions
DESCRIPTION_PROMPT = """You are analyzing text from Iain M. Banks' Culture series.

Find the BEST physical/visual description of "{entity_name}" in this text.

Look for:
- Physical appearance (height, build, features, skin, hair, eyes)
- Clothing or distinctive attire
- For ships/drones: shape, size, color, distinctive features
- For locations: architecture, landscape, atmosphere

Return a JSON object:
```json
{{
  "has_description": true/false,
  "description_quote": "exact quote from text describing appearance (or null)",
  "visual_details": ["list", "of", "key", "visual", "elements"]
}}
```

If there is NO physical description, return:
```json
{{"has_description": false, "description_quote": null, "visual_details": []}}
```

TEXT TO ANALYZE:
{text}
"""


def load_entity(entity_name: str, entity_type: str) -> dict | None:
    """Load entity data from merged entities file."""
    # Try book-specific files first
    for entity_file in config.ENTITIES_DIR.glob(f"{entity_type}_*.json"):
        data = read_json(entity_file)
        for entity in data.get("entities", []):
            if entity.get("canonical_name", "").lower() == entity_name.lower():
                return entity
            if entity_name.lower() in [a.lower() for a in entity.get("aliases", [])]:
                return entity
    
    # Try global file
    global_file = config.ENTITIES_DIR / f"{entity_type}.json"
    if global_file.exists():
        data = read_json(global_file)
        for entity in data.get("entities", []):
            if entity.get("canonical_name", "").lower() == entity_name.lower():
                return entity
    
    return None


def load_source_chunks(extraction_refs: list[str], book_slug: str = None) -> dict[str, str]:
    """Load source text for each chunk reference."""
    chunks = {}
    
    for ref in extraction_refs:
        # Ref format: "Excession_split_006" or "surface_detail_split_005"
        # Extract book name (everything before _split_ or last underscore before number)
        
        if "_split_" in ref:
            book_part = ref.split("_split_")[0]
        else:
            # Try to extract book from ref like "excession_ch001"
            parts = ref.rsplit("_", 1)
            book_part = parts[0] if len(parts) > 1 else ref
        
        # Find matching book directory
        for book_dir in config.BOOKS_DIR.iterdir():
            if not book_dir.is_dir():
                continue
            
            # Match by slugified name or direct match
            dir_slug = slugify(book_dir.name).replace("-", "_").lower()
            ref_slug = book_part.replace("-", "_").lower()
            
            if dir_slug == ref_slug or book_dir.name.lower().replace(" ", "_") == ref_slug:
                # Try to find the exact file
                for ext in [".md", ".html"]:
                    chapter_file = book_dir / f"{ref}{ext}"
                    if chapter_file.exists():
                        chunks[ref] = chapter_file.read_text(encoding="utf-8")
                        break
                break
    
    return chunks


def extract_description_llm(client, entity_name: str, text: str) -> dict:
    """Use LLM to extract visual description from text."""
    prompt = DESCRIPTION_PROMPT.format(entity_name=entity_name, text=text[:30000])  # Limit context
    
    try:
        response = client.client.models.generate_content(
            model=client.model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
            }
        )
        result = json.loads(response.text)
        return result
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return {"has_description": False, "description_quote": None, "visual_details": []}


def extract_description_for_entity(
    entity_name: str,
    entity_type: str,
    client=None,
    dry_run: bool = False
) -> dict:
    """Extract best visual description for an entity."""
    
    # Load entity data
    entity = load_entity(entity_name, entity_type)
    if not entity:
        logger.error(f"Entity not found: {entity_name} ({entity_type})")
        return None
    
    logger.info(f"Found entity: {entity.get('canonical_name')}")
    logger.info(f"  Citations: {len(entity.get('extraction_refs', []))}")
    logger.info(f"  Books: {entity.get('appears_in', [])}")
    
    # Load source chunks
    refs = entity.get("extraction_refs", [])
    if not refs:
        logger.warning("No extraction refs found")
        return None
    
    chunks = load_source_chunks(refs)
    logger.info(f"  Loaded {len(chunks)} source chunks")
    
    if dry_run:
        logger.info("  [DRY RUN] Would analyze chunks for descriptions")
        return {"entity": entity_name, "dry_run": True, "chunks_found": len(chunks)}
    
    # Analyze each chunk for descriptions
    best_description = None
    best_details = []
    
    for chunk_id, text in chunks.items():
        logger.info(f"  Analyzing {chunk_id}...")
        result = extract_description_llm(client, entity_name, text)
        
        if result.get("has_description") and result.get("description_quote"):
            # Keep the one with most visual details
            if len(result.get("visual_details", [])) > len(best_details):
                best_description = result["description_quote"]
                best_details = result["visual_details"]
                logger.info(f"    Found description with {len(best_details)} visual elements")
    
    return {
        "entity": entity_name,
        "entity_type": entity_type,
        "description_quote": best_description,
        "visual_details": best_details,
        "has_description": best_description is not None
    }


def main():
    parser = argparse.ArgumentParser(description="Extract visual descriptions for entities")
    parser.add_argument("--entity", help="Single entity name to process")
    parser.add_argument("--entity-type", default="characters",
                        choices=config.ENTITY_TYPES,
                        help="Entity type")
    parser.add_argument("--book", help="Process all major entities from a book")
    parser.add_argument("--min-citations", type=int, default=10,
                        help="Minimum citations for 'major' entity (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't make LLM calls")
    parser.add_argument("--output", help="Output JSON file")
    args = parser.parse_args()
    
    if not args.entity and not args.book:
        parser.error("Must specify --entity or --book")
    
    # Initialize client
    client = None
    if not args.dry_run:
        from src.gemini_client import GeminiClient
        client = GeminiClient()
        client.preflight()
    
    results = []
    
    if args.entity:
        # Single entity mode
        result = extract_description_for_entity(
            args.entity,
            args.entity_type,
            client=client,
            dry_run=args.dry_run
        )
        if result:
            results.append(result)
            print(json.dumps(result, indent=2))
    
    elif args.book:
        # Book mode - find all major entities
        book_slug = slugify(args.book)
        logger.info(f"Finding major entities for {book_slug}...")
        
        for entity_type in config.ENTITY_TYPES:
            entity_file = config.ENTITIES_DIR / f"{entity_type}_{book_slug}.json"
            if not entity_file.exists():
                continue
            
            data = read_json(entity_file)
            for entity in data.get("entities", []):
                citation_count = len(entity.get("extraction_refs", []))
                if citation_count >= args.min_citations:
                    name = entity.get("canonical_name")
                    logger.info(f"Processing {name} ({citation_count} citations)")
                    result = extract_description_for_entity(
                        name,
                        entity_type,
                        client=client,
                        dry_run=args.dry_run
                    )
                    if result:
                        results.append(result)
    
    # Save results
    if args.output and results:
        write_json(results, Path(args.output))
        logger.info(f"Saved {len(results)} results to {args.output}")
    
    # Summary
    with_desc = sum(1 for r in results if r.get("has_description"))
    logger.info(f"\nSummary: {with_desc}/{len(results)} entities have descriptions")


if __name__ == "__main__":
    main()
