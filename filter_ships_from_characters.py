#!/usr/bin/env python3
"""Filter ships from character extractions and articles.

Ships that leak into character extractions are detected by prefix patterns
(GSV, GCU, ROU, etc.) and moved/deleted as appropriate.
"""

import json
import re
from pathlib import Path

# Ship designation prefixes (Culture and other)
SHIP_PREFIXES = [
    "GSV", "GCU", "GCV", "ROU", "LCU", "GOU", "LOU", "MSV", "VFP",
    "LSV", "dROU", "fROU", "oROU", "OU", "Eccentric", "ex-"
]

# Compile pattern for efficient matching
SHIP_PATTERN = re.compile(
    r'^(' + '|'.join(re.escape(p) for p in SHIP_PREFIXES) + r')\s+',
    re.IGNORECASE
)


def is_ship_name(name: str) -> bool:
    """Check if entity name looks like a ship."""
    return bool(SHIP_PATTERN.match(name.strip()))


def filter_extractions(entity_type: str = "characters", dry_run: bool = True):
    """Remove ship entities from character extractions."""
    base_dir = Path("data/extractions") / entity_type
    if not base_dir.exists():
        print(f"No extractions directory: {base_dir}")
        return
    
    removed = []
    for book_dir in base_dir.iterdir():
        if not book_dir.is_dir():
            continue
        for path in book_dir.glob("*.json"):
            data = json.load(open(path))
            original_count = len(data.get("entities", []))
            
            # Filter out ships
            filtered = [e for e in data.get("entities", []) if not is_ship_name(e.get("name", ""))]
            ships_removed = [e["name"] for e in data.get("entities", []) if is_ship_name(e.get("name", ""))]
            
            if ships_removed:
                removed.extend(ships_removed)
                print(f"{path.name}: Removing {len(ships_removed)} ships: {ships_removed}")
                
                if not dry_run:
                    data["entities"] = filtered
                    with open(path, "w") as f:
                        json.dump(data, f, indent=2)
    
    print(f"\n{'Would remove' if dry_run else 'Removed'} {len(removed)} ship entities from {entity_type} extractions")
    if dry_run:
        print("Run with --apply to actually apply changes")


def filter_articles(entity_type: str = "characters", dry_run: bool = True):
    """Remove ship articles from character category."""
    articles_dir = Path("data/articles") / entity_type
    if not articles_dir.exists():
        print(f"No articles directory: {articles_dir}")
        return
    
    removed = []
    for path in articles_dir.glob("*.json"):
        data = json.load(open(path))
        name = data.get("entity_name", "")
        if is_ship_name(name):
            removed.append(path.name)
            print(f"Removing article: {path.name} ({name})")
            if not dry_run:
                path.unlink()
    
    print(f"\n{'Would remove' if dry_run else 'Removed'} {len(removed)} ship articles from {entity_type}")
    if dry_run:
        print("Run with --apply to actually apply changes")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Filter ships from character data")
    parser.add_argument("--apply", action="store_true", help="Actually apply changes (default: dry run)")
    parser.add_argument("--extractions", action="store_true", help="Filter extractions")
    parser.add_argument("--articles", action="store_true", help="Filter articles")
    args = parser.parse_args()
    
    dry_run = not args.apply
    
    if args.extractions or (not args.extractions and not args.articles):
        print("=== Filtering Extractions ===")
        filter_extractions("characters", dry_run)
        print()
    
    if args.articles or (not args.extractions and not args.articles):
        print("=== Filtering Articles ===")
        filter_articles("characters", dry_run)
