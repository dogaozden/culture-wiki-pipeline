"""Estimate synthesis time from merged entity counts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from . import config
except ImportError:  # Allows running as a script from repo root.
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src import config  # type: ignore


def _load_counts(entities_dir: Path) -> Tuple[Dict[str, int], List[str]]:
    counts: Dict[str, int] = {}
    errors: List[str] = []

    for path in sorted(entities_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding=config.DEFAULT_ENCODING))
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            continue
        entity_type = path.stem
        entities = data.get("entities", [])
        counts[entity_type] = len(entities) if isinstance(entities, list) else 0
    return counts, errors


def _format_hours(hours: float) -> str:
    if hours <= 0:
        return "0h 0m"
    total_minutes = int(round(hours * 60))
    return f"{total_minutes // 60}h {total_minutes % 60}m"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate synthesis runtime from merged entity counts"
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=105.0,
        help="Estimated synthesis calls per hour (default: 105)",
    )
    parser.add_argument(
        "--entities-dir",
        type=Path,
        default=config.ENTITIES_DIR,
        help="Directory containing merged entity JSON files",
    )
    args = parser.parse_args()

    counts, errors = _load_counts(args.entities_dir)
    total = sum(counts.values())

    print("Entity counts:")
    for entity_type, count in sorted(counts.items()):
        print(f"- {entity_type}: {count}")
    print(f"TOTAL: {total}")

    if args.rate > 0:
        hours = total / args.rate
        print(
            f"Estimated synthesis time @ {args.rate:.1f} calls/hour: {hours:.2f} hours ({_format_hours(hours)})"
        )

    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"- {err}")


if __name__ == "__main__":
    main()
