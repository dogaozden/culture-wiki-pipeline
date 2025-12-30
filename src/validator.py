"""Validation utilities for extraction data."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .config import EXTRACTIONS_DIR, PROJECT_ROOT, ENTITY_TYPES
from .utils import slugify

logger = logging.getLogger(__name__)


def normalize_for_match(text: str) -> str:
    """Normalize text for alias matching."""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
    }
    for src, dest in replacements.items():
        text = text.replace(src, dest)
    return " ".join(text.split())


def load_chunk_text(chunk_id: str, book_slug: str) -> Optional[str]:
    """Load the source text for a chunk."""
    # Try to find the book directory
    books_dir = PROJECT_ROOT / "books"
    
    # Try various book folder patterns
    candidates = [
        books_dir / book_slug,
        books_dir / book_slug.replace("-", "_"),
        books_dir / book_slug.replace("_", " ").title().replace(" ", "_"),
    ]
    
    # Also search for matching folders
    for folder in books_dir.iterdir():
        if folder.is_dir() and slugify(folder.name) == book_slug:
            candidates.insert(0, folder)
    
    for book_dir in candidates:
        if not book_dir.exists():
            continue
        
        # Try different file formats
        for ext in [".md", ".html", ".txt"]:
            chunk_file = book_dir / f"{chunk_id}{ext}"
            if chunk_file.exists():
                return chunk_file.read_text(encoding="utf-8")
    
    return None


def verify_aliases_in_file(
    extraction_path: Path,
    book_slug: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, int]:
    """Verify and fix aliases in a single extraction file.
    
    Returns dict with counts of removed aliases per entity.
    """
    stats = {"entities": 0, "aliases_checked": 0, "aliases_removed": 0}
    
    try:
        # Load extraction JSON
        with open(extraction_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        chunk_id = data.get("chunk_id", "")
        entities = data.get("entities", [])
        
        # Load source text
        source_text = load_chunk_text(chunk_id, book_slug)
        if not source_text:
            if verbose:
                logger.warning(f"Could not find source for {chunk_id}")
            return stats
        
        source_normalized = normalize_for_match(source_text.lower())
        modified = False
        
        for entity in entities:
            stats["entities"] += 1
            name = entity.get("name", "Unknown")
            original_aliases = entity.get("aliases", [])
            validated_aliases = []
            
            for alias in original_aliases:
                if not alias:
                    continue
                stats["aliases_checked"] += 1
                
                alias_normalized = normalize_for_match(alias.lower())
                if alias_normalized in source_normalized:
                    validated_aliases.append(alias)
                else:
                    stats["aliases_removed"] += 1
                    logger.warning(
                        f"HALLUCINATED ALIAS in {chunk_id}: '{name}' has fake alias '{alias}'"
                    )
                    modified = True
            
            entity["aliases"] = validated_aliases
        
        # Write fixed file
        if modified and not dry_run:
            with open(extraction_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"Fixed {extraction_path.name}")
            
    except Exception as e:
        logger.error(f"Error validating {extraction_path}: {e}")
    
    return stats


def verify_book_extractions(
    book_slug: str,
    entity_types: Optional[List[str]] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, int]:
    """Verify all extraction files for a book."""
    
    types = entity_types or ENTITY_TYPES
    total_stats = {"entities": 0, "aliases_checked": 0, "aliases_removed": 0}
    
    logger.info(f"Verifying extractions for {book_slug}...")
    
    for entity_type in types:
        extraction_dir = EXTRACTIONS_DIR / entity_type / book_slug
        if not extraction_dir.exists():
            continue
        
        for extraction_file in sorted(extraction_dir.glob("*.json")):
            stats = verify_aliases_in_file(
                extraction_file,
                book_slug,
                dry_run=dry_run,
                verbose=verbose,
            )
            for k, v in stats.items():
                total_stats[k] += v
    
    if total_stats["aliases_removed"] > 0:
        logger.info(f"Cleaned extractons for {book_slug}: removed {total_stats['aliases_removed']} hallucinated aliases")
    
    return total_stats
