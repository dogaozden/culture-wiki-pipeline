"""CLI entry point for Culture Wiki pipeline."""

import argparse
import logging

from src.pipeline import Pipeline


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Generate Culture Wiki")
    parser.add_argument(
        "--stage",
        choices=["chunk", "extract", "merge", "synthesize", "render", "all"],
        default="all",
        help="Pipeline stage to run",
    )
    parser.add_argument("--book", help="Process a specific book directory")
    parser.add_argument("--entity-type", help="Process a single entity type")
    parser.add_argument(
        "--force", action="store_true", help="Reprocess even if cached outputs exist"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="For extract stage: print the first prompt and skip API calls",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="For extract stage: process only the first N chapters",
    )

    args = parser.parse_args()
    pipeline = Pipeline()
    pipeline.run(
        stage=args.stage,
        book=args.book,
        entity_type=args.entity_type,
        force=args.force,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
