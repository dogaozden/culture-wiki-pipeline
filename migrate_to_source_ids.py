#!/usr/bin/env python3
"""
Migration script to convert extraction files from old chunk_id format to source filename format.

Old format: excession_ch001_characters.json with fact IDs like excession_ch001#001
New format: Excession_split_006_characters.json with fact IDs like Excession_split_006#001

Usage:
    python migrate_to_source_ids.py --dry-run   # Preview changes
    python migrate_to_source_ids.py --apply     # Apply changes
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Project paths
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
EXTRACTIONS_DIR = DATA_DIR / "extractions"
BOOKS_DIR = PROJECT_ROOT / "books"

# Entity types
ENTITY_TYPES = ["characters", "ships_and_minds", "locations", "technology", "factions", "events"]


def build_chapter_mapping(book_dir: Path) -> dict[str, str]:
    """
    Build a mapping from old chunk_id format to source filename.
    Uses the chunker module to get the same chapter list as extraction.
    Returns: {"excession_ch001": "Excession_split_006", "excession_ch002": "Excession_split_008", ...}
    """
    import sys
    # Add project root to path so we can import src as a package
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    
    from src.chunker import load_chapters
    
    mapping = {}
    book_slug = book_dir.name.lower().replace(" ", "-")
    
    try:
        chapters = load_chapters(book_dir)
    except Exception as e:
        logger.warning("Failed to load chapters from %s: %s", book_dir, e)
        return mapping
    
    for chapter in chapters:
        # Build old-style chunk_id
        old_chunk_id = f"{book_slug}_ch{chapter.index:03d}"
        if chapter.is_split:
            old_chunk_id += f"_p{chapter.split_part:02d}"
        
        # New chunk_id is source file stem
        new_chunk_id = Path(chapter.source_file).stem
        mapping[old_chunk_id] = new_chunk_id
    
    return mapping


def migrate_extraction_file(
    json_path: Path, 
    chunk_mapping: dict[str, str], 
    dry_run: bool = True
) -> bool:
    """
    Migrate a single extraction JSON file to use source filename IDs.
    Returns True if changes were made.
    """
    # Read the file
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    old_chunk_id = data.get("chunk_id", "")
    
    # Check if already migrated (contains "split_")
    if "_split_" in old_chunk_id:
        logger.debug("Already migrated: %s", json_path.name)
        return False
    
    # Look up new chunk_id
    # Handle both lowercased (excession_ch001) and mixed case
    lookup_key = old_chunk_id.lower()
    new_chunk_id = chunk_mapping.get(lookup_key)
    
    if not new_chunk_id:
        logger.warning("No mapping found for %s in %s", old_chunk_id, json_path.name)
        return False
    
    # Update chunk_id
    data["chunk_id"] = new_chunk_id
    
    # Update fact IDs in entities
    for entity in data.get("entities", []):
        for info in entity.get("information", []):
            old_id = info.get("id", "")
            if old_id and "#" in old_id:
                # Replace the chunk portion of the fact ID
                old_base, seq = old_id.rsplit("#", 1)
                info["id"] = f"{new_chunk_id}#{seq}"
    
    if dry_run:
        logger.info("[DRY RUN] Would update: %s -> %s", json_path.name, f"{new_chunk_id}_{json_path.stem.split('_')[-1]}.json")
        return True
    
    # Write updated content
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Rename file
    entity_type = json_path.stem.split("_")[-1]  # e.g., "characters"
    new_filename = f"{new_chunk_id}_{entity_type}.json"
    new_path = json_path.parent / new_filename
    
    if new_path != json_path:
        json_path.rename(new_path)
        logger.info("Migrated: %s -> %s", json_path.name, new_filename)
    
    return True


def migrate_all(dry_run: bool = True) -> tuple[int, int]:
    """
    Migrate all extraction files.
    Returns: (files_updated, files_skipped)
    """
    updated = 0
    skipped = 0
    
    for book_dir in BOOKS_DIR.iterdir():
        if not book_dir.is_dir() or book_dir.name.startswith("."):
            continue
        
        book_slug = book_dir.name.lower()
        logger.info("Processing book: %s", book_dir.name)
        
        # Build chapter mapping for this book
        chunk_mapping = build_chapter_mapping(book_dir)
        logger.info("  Found %d chapter mappings", len(chunk_mapping))
        
        # Process extraction files for each entity type
        for entity_type in ENTITY_TYPES:
            entity_dir = EXTRACTIONS_DIR / entity_type / book_slug
            if not entity_dir.exists():
                continue
            
            for json_path in sorted(entity_dir.glob("*.json")):
                if migrate_extraction_file(json_path, chunk_mapping, dry_run):
                    updated += 1
                else:
                    skipped += 1
    
    return updated, skipped


def main():
    parser = argparse.ArgumentParser(description="Migrate extraction files to source filename IDs")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--apply", action="store_true", help="Apply changes")
    args = parser.parse_args()
    
    if not args.dry_run and not args.apply:
        print("Please specify --dry-run or --apply")
        print("\nExample:")
        print("  python migrate_to_source_ids.py --dry-run   # Preview changes")
        print("  python migrate_to_source_ids.py --apply     # Apply changes")
        return
    
    dry_run = args.dry_run
    
    if not EXTRACTIONS_DIR.exists():
        logger.error("Extractions directory not found: %s", EXTRACTIONS_DIR)
        return
    
    logger.info("Starting migration (dry_run=%s)", dry_run)
    updated, skipped = migrate_all(dry_run=dry_run)
    
    print(f"\nSummary: {updated} files {'would be ' if dry_run else ''}updated, {skipped} skipped")
    
    if dry_run and updated > 0:
        print("\nRun with --apply to apply these changes")


if __name__ == "__main__":
    main()
