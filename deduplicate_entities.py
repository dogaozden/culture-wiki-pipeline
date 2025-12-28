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
from tqdm import tqdm

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

# Garbage filter prompt - identifies generic descriptors that shouldn't be entities
GARBAGE_FILTER_PROMPT = """You are filtering extracted entities from an Iain M. Banks Culture series novel.

IMPORTANT: BE EXTREMELY CONSERVATIVE. Only mark entities as garbage if you are 100% certain they are NOT proper names. When in doubt, KEEP the entity.

Your task: Identify ONLY the most obvious garbage — pronouns, numbered speakers, and clearly generic descriptors that could never be proper names.

DEFINITE GARBAGE (mark DELETE only if EXACTLY like these):
- Pronouns/references: "I", "we", "they", "the man", "the woman", "the girl"
- Numbered speakers: "Speaker 1", "Speaker 2", "Speaker 3"
- Vague descriptors: "our lass", "the other one", "someone"

KEEP ALL OF THESE (do NOT mark as garbage):
- Any capitalized proper name (even if short like "Byr", "Tier", "Aist")
- Ship names (even weird ones like "Jaundiced Outlook", "Meatfucker")
- Species/faction names: "Affront", "Affronters", "Culture", "Elench", "Homomda"
- Location names: "Pittance", "God'shole", "Tier"
- Technology terms: "effector", "displacer", "Gelfield Suit"
- Military ranks WITH names: "Fleet Captain Treveler"
- Group names: "Interesting Times Gang", "Peace faction"
- ANY name you recognize from the Culture series

CRITICAL: If you're unsure, output {{"garbage": []}} — it's better to keep garbage than delete legitimate entities.

Output JSON:
```json
{{
  "garbage": [
    {{"name": "Speaker 1", "reason": "Numbered speaker, not a proper name"}},
    {{"name": "the woman", "reason": "Generic descriptor"}}
  ]
}}
```

---
{entity_type} ENTITIES TO CHECK:

"""

ALIAS_BATCH_SIZE = 10  # Entities per LLM call for alias filtering
GARBAGE_BATCH_SIZE = 50  # Entities per LLM call for garbage filtering


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


def find_garbage_entities(client: GeminiClient, entity_type: str, entities: list[dict]) -> list[dict]:
    """
    Use LLM to identify garbage entities (generic descriptors, not proper names).
    Returns list of {"name": str, "reason": str} for entities to remove.
    """
    if not entities:
        return []
    
    garbage_found = []
    
    # Process in batches with progress bar
    total_batches = (len(entities) + GARBAGE_BATCH_SIZE - 1) // GARBAGE_BATCH_SIZE
    for i in tqdm(range(0, len(entities), GARBAGE_BATCH_SIZE), desc=f"Garbage filter ({entity_type})", total=total_batches):
        batch = entities[i:i + GARBAGE_BATCH_SIZE]
        
        # Build entity list for prompt
        entity_list = "\n".join(f"- {e['canonical_name']}" for e in batch)
        prompt = GARBAGE_FILTER_PROMPT.format(entity_type=entity_type) + entity_list
        
        try:
            response = client.client.models.generate_content(
                model=client.model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                }
            )
            result = json.loads(response.text)
            garbage_found.extend(result.get("garbage", []))
        except Exception as e:
            logger.error("Garbage filter batch %d failed: %s", batch_num, e)
    
    return garbage_found


def apply_garbage_filter(entity_type: str, garbage: list[dict]) -> int:
    """Remove identified garbage entities from the merged JSON."""
    if not garbage:
        return 0
    
    path = config.ENTITIES_DIR / f"{entity_type}.json"
    data = read_json(path)
    entities = data.get("entities", [])
    
    # Build set of names to delete (case-insensitive)
    delete_names = {g["name"].lower() for g in garbage}
    
    # Filter out garbage
    original_count = len(entities)
    filtered = [
        e for e in entities 
        if e.get("canonical_name", "").lower() not in delete_names
    ]
    
    removed = original_count - len(filtered)
    if removed > 0:
        logger.info("Removed %d garbage entities from %s", removed, entity_type)
        for g in garbage:
            if g["name"].lower() in delete_names:
                logger.info("  - %s (%s)", g["name"], g.get("reason", ""))
        data["entities"] = filtered
        write_json(data, path)
    
    return removed


def run_garbage_filter(entity_type: str, client: GeminiClient = None) -> int:
    """
    Run LLM garbage filter to remove generic descriptors from entity list.
    Returns the number of entities removed.
    """
    if client is None:
        client = GeminiClient()
    
    entities = load_entities(entity_type)
    if not entities:
        return 0
    
    logger.info("Running garbage filter on %d %s entities...", len(entities), entity_type)
    
    garbage = find_garbage_entities(client, entity_type, entities)
    
    if not garbage:
        logger.info("No garbage found in %s", entity_type)
        return 0
    
    logger.info("Found %d garbage entities in %s:", len(garbage), entity_type)
    
    return apply_garbage_filter(entity_type, garbage)




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
    
    # Process in batches for large entity lists with progress bar
    all_duplicates = []
    total_batches = (len(entities) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in tqdm(range(0, len(entities), BATCH_SIZE), desc=f"Dedup ({entity_type})", total=total_batches):
        batch = entities[i:i + BATCH_SIZE]
        
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


# Cross-entity deduplication prompt
CROSS_ENTITY_PROMPT = """You are an expert on Iain M. Banks' Culture series.

I will show you entities from '{source_type}' that might actually be SHIPS (which belong in 'ships_and_minds').

For reference, here are known ships from ships_and_minds:
{ship_names}

Your task: Identify which {source_type} entities are ACTUALLY ships/vessels.

For each ship found, output:
- "entity": the name from {source_type}
- "matching_ship": the ship from ships_and_minds it matches (or null if it's a new ship)
- "reason": brief explanation

GUIDELINES:
1. Ships include: GSVs, GCUs, ROUs, warships, vessels, any named Culture/Affront ships
2. Things that CONTAIN ships (like "warship hangars") are NOT ships
3. "The Sleeper" is the same as "Sleeper Service" - a GSV
4. Be AGGRESSIVE - if it sounds like a ship name, flag it

Output format:
```json
{{
  "ships_found": [
    {{"entity": "The Grey Area", "matching_ship": "Grey Area", "reason": "Same GCU with 'The' prefix"}},
    {{"entity": "xGSV Something", "matching_ship": null, "reason": "Ship prefix xGSV"}}
  ]
}}
```

If no ships found:
```json
{{"ships_found": []}}
```

---
{source_type} ENTITIES TO CHECK:

"""


def find_ships_in_entity_type(client: GeminiClient, source_type: str) -> list[dict]:
    """
    Use LLM to identify ships that leaked into another entity type.
    Returns list of {"entity": name_to_remove, "matching_ship": ship_name_or_null}.
    """
    # Load source entities
    source_entities = load_entities(source_type)
    if not source_entities:
        return []
    
    # Load ship names for reference
    ships = load_entities("ships_and_minds")
    ship_names = [s["canonical_name"] for s in ships[:50]]  # Top 50 for prompt
    ship_names_str = ", ".join(ship_names)
    
    # Build prompt
    entity_list = "\n".join(f"- {e['canonical_name']}" for e in source_entities)
    prompt = CROSS_ENTITY_PROMPT.format(
        source_type=source_type,
        ship_names=ship_names_str
    ) + entity_list
    
    logger.info("Checking %d %s entities for leaked ships...", len(source_entities), source_type)
    
    try:
        response = client.client.models.generate_content(
            model=client.model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
            }
        )
        result = json.loads(response.text)
        return result.get("ships_found", [])
    except Exception as e:
        logger.error("Cross-entity dedup failed for %s: %s", source_type, e)
        return []


def apply_cross_entity_dedup(source_type: str, ships_found: list[dict]) -> int:
    """Remove identified ships from a non-ship entity type."""
    if not ships_found:
        return 0
    
    path = config.ENTITIES_DIR / f"{source_type}.json"
    data = read_json(path)
    entities = data.get("entities", [])
    
    # Build set of names to delete
    delete_names = {s["entity"].lower() for s in ships_found}
    
    # Filter out ships
    original_count = len(entities)
    filtered = [
        e for e in entities 
        if e.get("canonical_name", "").lower() not in delete_names
    ]
    
    removed = original_count - len(filtered)
    if removed > 0:
        logger.info("Removed %d ships from %s", removed, source_type)
        data["entities"] = filtered
        write_json(data, path)
    
    return removed


def run_cross_entity_dedup(source_type: str, client: GeminiClient = None) -> int:
    """
    Run cross-entity LLM deduplication to remove ships from non-ship entity types.
    Returns the number of entities removed.
    """
    if source_type == "ships_and_minds":
        return 0  # Don't remove ships from ships!
    
    if client is None:
        client = GeminiClient()
    
    ships_found = find_ships_in_entity_type(client, source_type)
    
    if not ships_found:
        logger.info("No ships found in %s", source_type)
        return 0
    
    logger.info("Found %d ships in %s:", len(ships_found), source_type)
    for s in ships_found:
        logger.info("  - %s -> %s (%s)", s["entity"], s.get("matching_ship", "new ship"), s["reason"])
    
    return apply_cross_entity_dedup(source_type, ships_found)


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
