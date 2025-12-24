#!/usr/bin/env python3
"""
Print the full compiled data for any entity.
Usage: python3 show_entity_data.py <entity_type> <entity_name>

Examples:
  python3 show_entity_data.py characters "Dajeil Gelian"
  python3 show_entity_data.py characters Dajeil
  python3 show_entity_data.py ships_and_minds "Sleeper Service"
"""

import sys
from src.synthesizer import (
    _matching_extractions,
    _format_extractions_for_prompt,
    _load_extractions,
    _load_merged_entities,
    _build_chunk_context_map,
    build_synthesis_prompt,
)


def find_entity(merged_entities, search_term):
    """Find entity by name or partial match."""
    search_lower = search_term.lower()
    
    # Exact match first
    for m in merged_entities:
        if m.canonical_name.lower() == search_lower:
            return m
    
    # Partial match in name or aliases
    for m in merged_entities:
        if search_lower in m.canonical_name.lower():
            return m
        for alias in m.aliases:
            if search_lower in alias.lower():
                return m
    
    return None


def main():
    # Parse args manually to handle flags
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    
    if len(args) < 2:
        print(__doc__)
        print("\nAvailable entity types: characters, ships_and_minds, locations, technology, factions, events")
        sys.exit(1)
    
    entity_type = args[0]
    search_term = " ".join(args[1:])
    
    print(f"Loading {entity_type}...")
    merged_entities = _load_merged_entities(entity_type)
    extractions = _load_extractions(entity_type)
    chunk_map = _build_chunk_context_map()
    
    entity = find_entity(merged_entities, search_term)
    if not entity:
        print(f"Entity '{search_term}' not found in {entity_type}")
        print("\nAvailable entities (first 20):")
        for m in merged_entities[:20]:
            print(f"  - {m.canonical_name}")
        sys.exit(1)
    
    print("=" * 80)
    print(f"ENTITY: {entity.canonical_name}")
    print(f"Aliases: {entity.aliases}")
    print(f"Appears in: {entity.appears_in}")
    print(f"Extraction refs: {len(entity.extraction_refs)} chapters")
    print("=" * 80)
    
    matches = _matching_extractions(entity, extractions)
    print(f"\nMatched {len(matches)} extraction entries\n")
    
    formatted = _format_extractions_for_prompt(matches, chunk_map)
    
    print("=" * 80)
    print("COMPILED DATA (what Gemini sees):")
    print("=" * 80)
    print(formatted)
    print("=" * 80)
    print(f"\nTotal: {len(formatted):,} characters")
    
    # Optionally show the full prompt
    if "--full-prompt" in flags:
        print("\n" + "=" * 80)
        print("FULL SYNTHESIS PROMPT:")
        print("=" * 80)
        prompt = build_synthesis_prompt(entity, entity_type, formatted)
        print(prompt)


if __name__ == "__main__":
    main()
