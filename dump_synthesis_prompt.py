#!/usr/bin/env python3
"""
Dump the synthesis prompt for a specific entity.
Use this to manually run synthesis in Claude app or another LLM.

Usage:
    python3 dump_synthesis_prompt.py "Sleeper Service" --entity-type ships_and_minds
    python3 dump_synthesis_prompt.py "Grey Area" --entity-type ships_and_minds
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src import config
from src.models import MergedEntity
from src.synthesizer import (
    _load_extractions,
    _build_chunk_context_map,
    _matching_extractions,
    _format_extractions_for_prompt,
    build_synthesis_prompt,
)
from src.utils import read_json, slugify


def main():
    parser = argparse.ArgumentParser(
        description="Dump the synthesis prompt for a specific entity"
    )
    parser.add_argument("entity_name", help="Name of the entity (e.g., 'Sleeper Service')")
    parser.add_argument(
        "--entity-type",
        default="ships_and_minds",
        help="Entity type (default: ships_and_minds)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file (default: stdout)",
    )
    args = parser.parse_args()

    # Load merged entities
    entities_path = config.ENTITIES_DIR / f"{args.entity_type}.json"
    if not entities_path.exists():
        print(f"Error: {entities_path} not found. Run merge stage first.", file=sys.stderr)
        sys.exit(1)

    entities_data = read_json(entities_path)
    merged_entity = None
    search_name = args.entity_name.lower().strip()
    
    for e in entities_data.get("entities", []):
        if e["canonical_name"].lower() == search_name:
            merged_entity = MergedEntity(**e)
            break
        # Also check aliases
        for alias in e.get("aliases", []):
            if alias.lower() == search_name:
                merged_entity = MergedEntity(**e)
                break
        if merged_entity:
            break

    if not merged_entity:
        print(f"Error: Entity '{args.entity_name}' not found in {args.entity_type}", file=sys.stderr)
        print("\nAvailable entities:", file=sys.stderr)
        for e in entities_data.get("entities", [])[:20]:
            print(f"  - {e['canonical_name']}", file=sys.stderr)
        if len(entities_data.get("entities", [])) > 20:
            print(f"  ... and {len(entities_data['entities']) - 20} more", file=sys.stderr)
        sys.exit(1)

    # Load extractions and chunk map
    extractions = _load_extractions(args.entity_type)
    chunk_map = _build_chunk_context_map()

    # Get matching extractions for this entity
    matches = _matching_extractions(merged_entity, extractions)
    if not matches:
        print(f"Error: No extractions found for '{merged_entity.canonical_name}'", file=sys.stderr)
        sys.exit(1)

    # Format extractions and build prompt
    formatted = _format_extractions_for_prompt(matches, chunk_map)
    if not formatted.strip():
        print(f"Error: No valid quotes found for '{merged_entity.canonical_name}'", file=sys.stderr)
        sys.exit(1)

    prompt = build_synthesis_prompt(merged_entity, args.entity_type, formatted)

    # Output
    if args.output:
        Path(args.output).write_text(prompt)
        print(f"Prompt saved to {args.output}", file=sys.stderr)
    else:
        print(prompt)

    # Stats
    fact_count = formatted.count("] Fact:")
    print(f"\n--- STATS ---", file=sys.stderr)
    print(f"Entity: {merged_entity.canonical_name}", file=sys.stderr)
    print(f"Aliases: {', '.join(merged_entity.aliases) or 'None'}", file=sys.stderr)
    print(f"Facts: {fact_count}", file=sys.stderr)
    print(f"Prompt length: {len(prompt):,} characters", file=sys.stderr)


if __name__ == "__main__":
    main()
