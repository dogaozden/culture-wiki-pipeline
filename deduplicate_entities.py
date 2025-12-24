#!/usr/bin/env python3
"""
LLM-Powered Entity Deduplication Script

Uses Gemini to intelligently identify duplicate entities that Python string
matching can't catch (e.g., "the Sleeper" = "Sleeper Service").

Usage:
    python3 deduplicate_entities.py --entity-type ships_and_minds --dry-run
    python3 deduplicate_entities.py --entity-type ships_and_minds --apply
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src import config
from src.gemini_client import GeminiClient
from src.utils import read_json, write_json, slugify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Prompt for LLM deduplication
DEDUPE_PROMPT = """You are an expert on Iain M. Banks' Culture series. I will show you a list of entities extracted from a novel.

Your task: Identify DUPLICATE entities — same thing/person/ship, extracted under different names.

For each duplicate pair, output:
- "keep": the more complete/official/famous name
- "delete": the name to remove (will be added as alias to "keep")
- "reason": brief evidence-based explanation

GUIDELINES:
1. Use the extracted FACTS and ALIASES shown below as primary evidence
2. If facts mention "X, also known as Y" or "X (nicknamed Y)", they're duplicates
3. You may use your knowledge of the Culture series, but be CONSERVATIVE
4. When uncertain, do NOT flag as duplicate
5. Ships with different class prefixes (GCU X vs GSV Y) are usually DIFFERENT ships

EXAMPLES of duplicates:
- "Grey Area" and "Meatfucker" → Same GCU, the book explicitly calls it both names
- "the Sleeper" and "Sleeper Service" → Same GSV, informal vs full name
- "Quietly Confident" and "Sleeper Service" → Same ship, old name vs new name

EXAMPLES of NON-duplicates:
- "Jaundiced Outlook" and "Sleeper Service" → DIFFERENT ships (GCU vs GSV)
- "Pittance Mind" and "Pittance" → Different entities (Mind vs location)

Output format:
```json
{
  "duplicates": [
    {"keep": "Sleeper Service", "delete": "the Sleeper", "reason": "Same GSV, informal name variant"},
    {"keep": "Grey Area", "delete": "GCU Grey Area", "reason": "Same ship, one has class prefix"}
  ]
}
```

If no duplicates found:
```json
{"duplicates": []}
```

---
ENTITIES TO ANALYZE:

"""


def load_entities(entity_type: str) -> list[dict]:
    """Load merged entities from JSON."""
    path = config.ENTITIES_DIR / f"{entity_type}.json"
    if not path.exists():
        logger.error("No merged entities found at %s", path)
        return []
    data = read_json(path)
    return data.get("entities", [])


def load_entity_facts(entity_type: str, entity_name: str, extraction_refs: list[str]) -> list[str]:
    """Load the first few facts for an entity from its extractions."""
    facts = []
    for ref in extraction_refs[:3]:  # Only check first 3 chapters
        # Parse ref like "excession_ch011" -> "excession/excession_ch011_<type>.json"
        parts = ref.split("_")
        if len(parts) >= 2:
            book = parts[0]
            chapter = "_".join(parts[:2])
            path = config.EXTRACTIONS_DIR / entity_type / book / f"{chapter}_{entity_type}.json"
            if path.exists():
                try:
                    data = read_json(path)
                    for ent in data.get("entities", []):
                        if ent.get("name", "").lower() == entity_name.lower():
                            for info in ent.get("information", [])[:2]:
                                facts.append(info.get("fact", ""))
                except Exception:
                    pass
        if len(facts) >= 3:
            break
    return facts[:3]


def format_entity_list(entities: list[dict], entity_type: str) -> str:
    """Format entities for the LLM prompt, including key facts."""
    lines = []
    for i, e in enumerate(entities, 1):
        name = e.get("canonical_name", "Unknown")
        aliases = e.get("aliases", [])
        refs = e.get("extraction_refs", [])
        
        # Build entity line
        line = f"{i}. **{name}**"
        if aliases:
            line += f"\n   Aliases: {', '.join(aliases[:5])}"
        
        # Load and include facts
        facts = load_entity_facts(entity_type, name, refs)
        if facts:
            line += f"\n   Facts: {'; '.join(facts[:2])}"
        
        lines.append(line)
    return "\n\n".join(lines)


def find_duplicates(client: GeminiClient, entity_type: str, entities: list[dict]) -> list[dict]:
    """Use LLM to find semantic duplicates."""
    entity_list = format_entity_list(entities, entity_type)
    prompt = DEDUPE_PROMPT + entity_list
    
    logger.info("Sending %d entities to LLM for deduplication analysis...", len(entities))
    
    try:
        response = client.client.models.generate_content(
            model=client.model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
            }
        )
        result = json.loads(response.text)
        return result.get("duplicates", [])
    except Exception as e:
        logger.error("LLM deduplication failed: %s", e)
        return []


def apply_deduplication(entity_type: str, duplicates: list[dict]) -> None:
    """Remove duplicate entities from the merged JSON."""
    path = config.ENTITIES_DIR / f"{entity_type}.json"
    data = read_json(path)
    entities = data.get("entities", [])
    
    # Build set of names to delete
    delete_names = {d["delete"].lower() for d in duplicates}
    
    # Filter out duplicates
    original_count = len(entities)
    filtered = [
        e for e in entities 
        if e.get("canonical_name", "").lower() not in delete_names
    ]
    
    # Optionally add deleted names as aliases to the kept entity
    for dup in duplicates:
        keep_name = dup["keep"].lower()
        delete_name = dup["delete"]
        for e in filtered:
            if e.get("canonical_name", "").lower() == keep_name:
                if delete_name not in e.get("aliases", []):
                    e.setdefault("aliases", []).append(delete_name)
                break
    
    removed = original_count - len(filtered)
    logger.info("Removed %d duplicate entities", removed)
    
    data["entities"] = filtered
    write_json(data, path)
    logger.info("Updated %s", path)


def main():
    parser = argparse.ArgumentParser(description="LLM-powered entity deduplication")
    parser.add_argument("--entity-type", required=True, 
                        choices=config.ENTITY_TYPES,
                        help="Entity type to deduplicate")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show duplicates without applying changes")
    parser.add_argument("--apply", action="store_true",
                        help="Apply deduplication changes")
    args = parser.parse_args()
    
    if not args.dry_run and not args.apply:
        print("Specify --dry-run or --apply")
        sys.exit(1)
    
    # Load entities
    entities = load_entities(args.entity_type)
    if not entities:
        print("No entities to process")
        sys.exit(1)
    
    logger.info("Loaded %d %s entities", len(entities), args.entity_type)
    
    # Initialize client
    client = GeminiClient()
    
    # Find duplicates
    duplicates = find_duplicates(client, args.entity_type, entities)
    
    if not duplicates:
        print("\n✅ No duplicates found!")
        return
    
    # Display results
    print(f"\n🔍 Found {len(duplicates)} potential duplicate(s):\n")
    for i, dup in enumerate(duplicates, 1):
        print(f"  {i}. KEEP: \"{dup['keep']}\"")
        print(f"     DELETE: \"{dup['delete']}\"")
        print(f"     Reason: {dup['reason']}")
        print()
    
    if args.dry_run:
        print("Dry run - no changes made. Use --apply to remove duplicates.")
    elif args.apply:
        confirm = input("Apply these changes? [y/N]: ").strip().lower()
        if confirm == 'y':
            apply_deduplication(args.entity_type, duplicates)
            print("\n✅ Deduplication applied!")
            print("Re-run synthesis and render to update articles.")
        else:
            print("Cancelled.")


if __name__ == "__main__":
    main()
