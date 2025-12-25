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


# Prompt for LLM to identify alias-related facts
ALIAS_FILTER_PROMPT = """You are analyzing facts about entities from a novel within Iain M. Banks's Culture series.

For each entity below, identify facts that mention:
- Former/previous names ("once called", "formerly known as")
- Nicknames or aliases ("also known as", "nicknamed")
- Name changes ("renamed", "now called", "later became")

Return ONLY the facts that mention alternative names. If no such facts exist for an entity, omit it from output.

Output JSON format:
```json
{
  "entities": [
    {
      "name": "Entity Name",
      "alias_facts": ["Fact about former name...", "Fact about nickname..."]
    }
  ]
}
```

If no entities have alias-related facts, return: {"entities": []}

---
ENTITIES AND THEIR FACTS:

"""

ALIAS_BATCH_SIZE = 10  # Entities per LLM call for alias filtering


def _ref_to_path(ref: str, entity_type: str) -> Path:
    """Convert extraction ref like 'excession_ch011' to file path."""
    parts = ref.split("_")
    if len(parts) >= 2:
        book = parts[0]
        chapter = "_".join(parts[:2])
        return config.EXTRACTIONS_DIR / entity_type / book / f"{chapter}_{entity_type}.json"
    return Path()


def load_all_facts_for_entity(entity_type: str, entity_name: str, aliases: list[str], extraction_refs: list[str]) -> list[str]:
    """Load ALL facts for an entity from its extractions (searching by name AND aliases)."""
    facts = []
    names_to_search = {entity_name.lower()} | {a.lower() for a in aliases}
    
    for ref in extraction_refs:
        path = _ref_to_path(ref, entity_type)
        if not path.exists():
            continue
        try:
            data = read_json(path)
            for ent in data.get("entities", []):
                if ent.get("name", "").lower() in names_to_search:
                    for info in ent.get("information", []):
                        facts.append(info.get("fact", ""))
        except Exception:
            pass
    
    return facts


def find_alias_facts_llm(client: GeminiClient, entity_type: str, entities: list[dict]) -> dict[str, list[str]]:
    """Use LLM to identify alias-related facts for entities. Returns {entity_name: [alias_facts]}."""
    if not entities:
        return {}
    
    alias_facts_map = {}
    
    # Process in batches
    for i in range(0, len(entities), ALIAS_BATCH_SIZE):
        batch = entities[i:i + ALIAS_BATCH_SIZE]
        batch_num = (i // ALIAS_BATCH_SIZE) + 1
        total_batches = (len(entities) + ALIAS_BATCH_SIZE - 1) // ALIAS_BATCH_SIZE
        
        if total_batches > 1:
            logger.info("Alias pre-filter: batch %d/%d...", batch_num, total_batches)
        
        # Build prompt with all facts for each entity
        prompt_parts = []
        for e in batch:
            name = e.get("canonical_name", "Unknown")
            aliases = e.get("aliases", [])
            refs = e.get("extraction_refs", [])
            
            all_facts = load_all_facts_for_entity(entity_type, name, aliases, refs)
            if not all_facts:
                continue
            
            facts_str = "\n".join(f"  - {f}" for f in all_facts[:20])  # Cap at 20 facts per entity
            prompt_parts.append(f"**{name}**\n{facts_str}")
        
        if not prompt_parts:
            continue
        
        prompt = ALIAS_FILTER_PROMPT + "\n\n".join(prompt_parts)
        
        # Retry up to 3 times on failure
        for attempt in range(3):
            try:
                response = client.client.models.generate_content(
                    model=client.model_name,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                    }
                )
                
                # Parse JSON from response
                result = json.loads(response.text)
                for entity in result.get("entities", []):
                    name = entity.get("name", "")
                    facts = entity.get("alias_facts", [])
                    if name and facts:
                        alias_facts_map[name.lower()] = facts
                break  # Success, exit retry loop
            except Exception as e:
                if attempt < 2:
                    logger.warning("Alias filter batch %d failed (attempt %d/3): %s. Retrying...", batch_num, attempt + 1, e)
                else:
                    logger.warning("Alias filter batch %d failed after 3 attempts: %s", batch_num, e)
    
    return alias_facts_map


def load_entities(entity_type: str) -> list[dict]:
    """Load merged entities from JSON."""
    path = config.ENTITIES_DIR / f"{entity_type}.json"
    if not path.exists():
        logger.error("No merged entities found at %s", path)
        return []
    data = read_json(path)
    return data.get("entities", [])


def format_entity_list(entities: list[dict], entity_type: str, alias_facts_map: dict[str, list[str]] = None) -> str:
    """Format entities for the LLM prompt, including alias-related facts."""
    if alias_facts_map is None:
        alias_facts_map = {}
    
    lines = []
    for i, e in enumerate(entities, 1):
        name = e.get("canonical_name", "Unknown")
        aliases = e.get("aliases", [])
        
        # Build entity line
        line = f"{i}. **{name}**"
        if aliases:
            line += f"\n   Aliases: {', '.join(aliases[:5])}"
        
        # Include alias-related facts from pre-filter (if available)
        alias_facts = alias_facts_map.get(name.lower(), [])
        if alias_facts:
            line += f"\n   Alias-related facts: {'; '.join(alias_facts[:3])}"
        
        lines.append(line)
    return "\n\n".join(lines)


BATCH_SIZE = 100  # Process entities in batches to avoid API limits


def find_duplicates(client: GeminiClient, entity_type: str, entities: list[dict]) -> list[dict]:
    """Use LLM to find semantic duplicates, processing in batches if needed."""
    
    # Step 1: Run alias pre-filter to find relevant facts
    logger.info("Running alias pre-filter on %d entities...", len(entities))
    alias_facts_map = find_alias_facts_llm(client, entity_type, entities)
    logger.info("Found alias-related facts for %d entities", len(alias_facts_map))
    
    # Step 2: Run deduplication with alias facts
    if len(entities) <= BATCH_SIZE:
        return _find_duplicates_single_batch(client, entity_type, entities, alias_facts_map)
    
    # Process in batches for large entity lists
    all_duplicates = []
    for i in range(0, len(entities), BATCH_SIZE):
        batch = entities[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(entities) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info("Dedup batch %d/%d (%d entities)...", batch_num, total_batches, len(batch))
        
        batch_duplicates = _find_duplicates_single_batch(client, entity_type, batch, alias_facts_map)
        all_duplicates.extend(batch_duplicates)
    
    return all_duplicates


def _find_duplicates_single_batch(client: GeminiClient, entity_type: str, entities: list[dict], alias_facts_map: dict[str, list[str]] = None) -> list[dict]:
    """Use LLM to find semantic duplicates in a single batch."""
    entity_list = format_entity_list(entities, entity_type, alias_facts_map)
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
    return removed


def run_deduplication(entity_type: str, client: GeminiClient = None) -> int:
    """
    Run LLM deduplication on merged entities.
    Called automatically from the merger to deduplicate after consolidation.
    
    Returns the number of duplicates removed.
    """
    entities = load_entities(entity_type)
    if not entities:
        logger.info("No entities to deduplicate for %s", entity_type)
        return 0
    
    if len(entities) < 5:
        # Too few entities to bother with LLM deduplication
        logger.info("Skipping deduplication for %s (%d entities, too few)", entity_type, len(entities))
        return 0
    
    # Initialize client if not provided
    if client is None:
        client = GeminiClient()
    
    logger.info("Running LLM deduplication on %d %s entities...", len(entities), entity_type)
    
    # Find duplicates
    duplicates = find_duplicates(client, entity_type, entities)
    
    if not duplicates:
        logger.info("No duplicates found for %s", entity_type)
        return 0
    
    logger.info("Found %d duplicate(s) for %s, applying...", len(duplicates), entity_type)
    for dup in duplicates:
        logger.debug("  - KEEP %s, DELETE %s: %s", dup['keep'], dup['delete'], dup['reason'])
    
    # Apply deduplication
    removed = apply_deduplication(entity_type, duplicates)
    return removed


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
