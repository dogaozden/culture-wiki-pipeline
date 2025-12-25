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
        choices=["chunk", "extract", "merge", "synthesize", "render", "clean", "all"],
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
    parser.add_argument(
        "--clean",
        action="append",
        choices=[
            "all",
            "cache",
            "extractions",
            "entities",
            "articles",
            "wiki",
            "chunks",
            "output",
        ],
        help="For clean stage: which outputs to delete (use multiple flags)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="For clean stage: skip confirmation prompt",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of parallel workers (default: 16)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Max synthesis retries on citation failures (default: 10)",
    )

    args = parser.parse_args()
    if args.clean and args.stage != "clean":
        parser.error("--clean requires --stage clean")
    
    # Override config if flags provided
    from src import config
    if args.workers is not None:
        config.PARALLEL_WORKERS = args.workers
    
    pipeline = Pipeline()
    pipeline.run(
        stage=args.stage,
        book=args.book,
        entity_type=args.entity_type,
        force=args.force,
        dry_run=args.dry_run,
        limit=args.limit,
        clean_targets=args.clean,
        yes=args.yes,
        max_retries=args.max_retries,
    )


if __name__ == "__main__":
    main()
