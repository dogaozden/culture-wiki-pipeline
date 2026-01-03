"""Cross-book entity merger.

This module identifies entities appearing in multiple books and merges them
into unified entities for cross-book synthesis.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import config
from .models import MergedEntity
from .utils import read_json, write_json, slugify

logger = logging.getLogger(__name__)

# Books in publication order
PUBLICATION_ORDER = [
    "consider-phlebas",      # 1987
    "the-player-of-games",   # 1988
    "use-of-weapons",        # 1990
    "the-state-of-the-art",  # 1991
    "excession",             # 1996
    "inversions",            # 1998
    "look-to-windward",      # 2000
    "matter",                # 2008
    "surface-detail",        # 2010
    "the-hydrogen-sonata",   # 2012
]

# Generic aliases that should never be used for matching
GENERIC_ALIAS_BLACKLIST = {
    # Descriptive phrases
    "the woman", "the man", "the human", "the drone", "the ship",
    "the captain", "the agent", "the ambassador", "the contact",
    "the mind", "the avatar", "the creature", "the being",
    "the person", "the stranger", "the visitor", "the guest",
    # Pronouns
    "she", "he", "they", "it", "her", "him", "them",
    # Numbers (clone groups etc.)
    "one", "two", "three", "four", "five",
    # Titles without names
    "commander", "admiral", "captain", "ambassador", "agent",
    "the commander", "the admiral", "the ambassador",
}


def _normalize_name(name: str) -> str:
    """Normalize entity name for comparison."""
    return name.lower().strip()


def _is_valid_alias(alias: str) -> bool:
    """Check if an alias is specific enough for cross-book matching."""
    normalized = alias.lower().strip()
    
    # Skip blacklisted aliases
    if normalized in GENERIC_ALIAS_BLACKLIST:
        return False
    
    # Skip very short aliases (< 3 chars)
    if len(normalized) < 3:
        return False
    
    # Skip single common words
    common_words = {"the", "and", "for", "with", "from", "that", "this"}
    if normalized in common_words:
        return False
    
    return True


def _get_book_entity_files(entity_type: str) -> List[Tuple[str, Path]]:
    """Get all book-specific entity files for a type, sorted by publication order."""
    files = []
    
    # First add files in publication order
    for book_slug in PUBLICATION_ORDER:
        path = config.ENTITIES_DIR / f"{entity_type}_{book_slug}.json"
        if path.exists():
            files.append((book_slug, path))
    
    # Then add any remaining files not in the publication order list
    for path in config.ENTITIES_DIR.glob(f"{entity_type}_*.json"):
        book_slug = path.stem.replace(f"{entity_type}_", "")
        if book_slug not in PUBLICATION_ORDER and book_slug != "cross-book":
            files.append((book_slug, path))
    
    return files


def find_cross_book_entities(entity_type: str) -> Dict[str, List[Tuple[str, dict]]]:
    """Find entities appearing in 2+ books.
    
    Returns:
        Dict mapping normalized canonical name -> list of (book_slug, entity_data) tuples
    """
    # Collect all entities by normalized canonical name
    entities_by_name: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
    
    # Also build alias -> canonical name mapping for cross-referencing
    alias_to_canonical: Dict[str, Set[str]] = defaultdict(set)
    
    for book_slug, path in _get_book_entity_files(entity_type):
        try:
            data = read_json(path)
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)
            continue
        
        for entity in data.get("entities", []):
            canonical = entity.get("canonical_name", "")
            if not canonical:
                continue
            
            normalized = _normalize_name(canonical)
            entities_by_name[normalized].append((book_slug, entity))
            
            # Map valid aliases to this canonical name
            for alias in entity.get("aliases", []):
                if _is_valid_alias(alias):
                    alias_to_canonical[_normalize_name(alias)].add(normalized)
    
    # Find entities in 2+ books (by canonical name match)
    multi_book = {}
    for name, entries in entities_by_name.items():
        if len(entries) >= 2:
            # Check that entries are from different books
            books = {e[0] for e in entries}
            if len(books) >= 2:
                multi_book[name] = entries
    
    # TODO: Future enhancement - use alias matching with LLM validation
    # For now, we only match on canonical name to be safe
    
    return multi_book


def merge_cross_book_entity(
    entity_type: str,
    canonical_name: str,
    book_entries: List[Tuple[str, dict]],
) -> MergedEntity:
    """Merge entity data from multiple books into a unified entity.
    
    Args:
        entity_type: The entity type (e.g., 'factions')
        canonical_name: The canonical name to use
        book_entries: List of (book_slug, entity_data) tuples, sorted by publication order
    
    Returns:
        A unified MergedEntity combining data from all books
    """
    # Use the first book's entity as the base (publication order)
    base_entity = book_entries[0][1]
    
    # Collect from all books
    all_aliases: Set[str] = set()
    all_extraction_refs: List[str] = []
    all_appears_in: List[str] = []
    all_information: List[dict] = []
    
    for book_slug, entity in book_entries:
        # Add book to appears_in
        if book_slug not in all_appears_in:
            all_appears_in.append(book_slug)
        
        # Merge aliases
        for alias in entity.get("aliases", []):
            if alias and alias.strip():
                all_aliases.add(alias.strip())
        
        # Merge extraction refs
        for ref in entity.get("extraction_refs", []):
            if ref not in all_extraction_refs:
                all_extraction_refs.append(ref)
        
        # Merge information facts (dedup by fact ID if present)
        seen_fact_ids = {info.get("id") for info in all_information if info.get("id")}
        for info in entity.get("information", []):
            fact_id = info.get("id")
            if fact_id and fact_id in seen_fact_ids:
                continue  # Skip duplicate
            all_information.append(info)
            if fact_id:
                seen_fact_ids.add(fact_id)
    
    # Build merged entity dict
    merged_data = {
        "canonical_name": canonical_name,
        "aliases": sorted(all_aliases),
        "appears_in": all_appears_in,  # Already in publication order
        "extraction_refs": all_extraction_refs,
        "information": all_information,
    }
    
    # Copy any additional fields from base entity
    for key in ["species", "affiliation", "class", "type", "status", "category"]:
        if key in base_entity and base_entity[key]:
            merged_data[key] = base_entity[key]
    
    return MergedEntity(**merged_data)


def run_cross_book_merge(
    entity_type: str = None,
    force: bool = False,
) -> None:
    """Run cross-book entity merge for one or all entity types.
    
    Args:
        entity_type: Specific type to merge, or None for all types
        force: Overwrite existing cross-book files
    """
    types = [entity_type] if entity_type else config.ENTITY_TYPES
    
    for et in types:
        logger.info("Finding cross-book entities for %s...", et)
        
        output_path = config.ENTITIES_DIR / f"{et}_cross-book.json"
        if output_path.exists() and not force:
            logger.info("Skipping %s — cross-book file exists (use --force to overwrite)", et)
            continue
        
        # Find entities in multiple books
        cross_book = find_cross_book_entities(et)
        
        if not cross_book:
            logger.info("No cross-book entities found for %s", et)
            continue
        
        logger.info("Found %d cross-book entities for %s:", len(cross_book), et)
        for name, entries in list(cross_book.items())[:10]:
            books = [e[0] for e in entries]
            logger.info("  - %s: %s", name, books)
        
        if len(cross_book) > 10:
            logger.info("  ... and %d more", len(cross_book) - 10)
        
        # Merge each entity
        merged_entities = []
        for name, entries in cross_book.items():
            # Use the proper-cased name from the first book
            proper_name = entries[0][1].get("canonical_name", name)
            merged = merge_cross_book_entity(et, proper_name, entries)
            merged_entities.append(merged)
        
        # Sort by number of books (most cross-referenced first)
        merged_entities.sort(key=lambda e: -len(e.appears_in))
        
        # Write output
        output_data = {
            "entities": [e.model_dump() for e in merged_entities],
            "metadata": {
                "type": "cross-book",
                "entity_type": et,
                "count": len(merged_entities),
            }
        }
        write_json(output_data, output_path)
        logger.info("Wrote %d cross-book %s entities to %s", len(merged_entities), et, output_path)
