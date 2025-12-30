#!/usr/bin/env python3
"""Verify and fix hallucinated aliases in existing extraction files.

This script scans extraction JSON files and removes any aliases that don't
appear verbatim in the source text. This catches LLM hallucinations like
'Ms Gelian' being wrongly assigned to Ulver Seich.

Usage:
    python verify_extraction_aliases.py [--book BOOK] [--dry-run] [--verbose]

Examples:
    # Fix all books
    python verify_extraction_aliases.py
    
    # Dry run for Excession only
    python verify_extraction_aliases.py --book excession --dry-run
"""

import argparse
import logging
from src.validator import verify_book_extractions
from src.config import EXTRACTIONS_DIR
from src.utils import slugify

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Verify extraction aliases against source text")
    parser.add_argument("--book", help="Book slug to verify (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fixed without modifying files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    if args.dry_run:
        logger.info("DRY RUN - no files will be modified")
    
    # Find books to process
    if args.book:
        books = [slugify(args.book)]
    else:
        # Discover all books from extractions
        books = set()
        for entity_dir in EXTRACTIONS_DIR.iterdir():
            if entity_dir.is_dir():
                for book_dir in entity_dir.iterdir():
                    if book_dir.is_dir() and not book_dir.name.startswith("."):
                        books.add(book_dir.name)
        books = sorted(books)
    
    logger.info(f"Verifying aliases for {len(books)} book(s): {', '.join(books)}")
    
    grand_total = {"entities": 0, "aliases_checked": 0, "aliases_removed": 0}
    
    for book_slug in books:
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing: {book_slug}")
        logger.info(f"{'='*60}")
        
        stats = verify_book_extractions(
            book_slug,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        
        for k, v in stats.items():
            grand_total[k] += v
        
        if stats["aliases_removed"] > 0:
            logger.info(f"Book {book_slug}: Removed {stats['aliases_removed']} hallucinated aliases")
    
    logger.info(f"\n{'='*60}")
    logger.info("SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Total entities checked: {grand_total['entities']}")
    logger.info(f"Total aliases checked: {grand_total['aliases_checked']}")
    logger.info(f"Total aliases removed: {grand_total['aliases_removed']}")
    
    if args.dry_run and grand_total["aliases_removed"] > 0:
        logger.info("\nRun without --dry-run to apply fixes")


if __name__ == "__main__":
    main()
