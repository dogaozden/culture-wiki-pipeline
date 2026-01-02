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
        choices=["chunk", "extract", "merge", "synthesize", "render", "clean", "all", "image"],
        default="all",
        help="Pipeline stage to run",
    )
    parser.add_argument("--book", help="Process a specific book directory")
    parser.add_argument("--entity", help="Process a specific entity by name")
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
    parser.add_argument(
        "--skip-dedup",
        action="store_true",
        help="Skip LLM-based semantic deduplication during merge stage (faster but less thorough)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Gemini model to use (e.g., gemini-2.5-flash-lite, gemini-3-flash-preview). Default: gemini-3-flash-preview",
    )
    parser.add_argument(
        "--ship-class",
        help="For image stage: generate test image for a ship class (e.g. GSV)",
    )
    parser.add_argument(
        "--min-citations",
        type=int,
        default=10,
        help="For image stage: minimum citations to consider an entity 'major' (default: 10)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Enable LLM response caching (default: off). Cache is model-specific.",
    )

    args = parser.parse_args()
    if args.clean and args.stage != "clean":
        parser.error("--clean requires --stage clean")
    
    # Override config if flags provided
    from src import config
    if args.workers is not None:
        config.PARALLEL_WORKERS = args.workers
    config.USE_CACHE = args.use_cache
    if args.model is not None:
        config.GEMINI_MODEL = args.model
    
    pipeline = Pipeline()
    pipeline = Pipeline()
    
    if args.stage == "image":
        from src.image_generator import generate_images
        generate_images(
            entity_name=args.entity,
            book_slug=args.book,
            entity_type=args.entity_type,
            ship_class=args.ship_class if hasattr(args, "ship_class") else None,
            min_citations=args.min_citations if hasattr(args, "min_citations") else 10,
            dry_run=args.dry_run
        )
    else:
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
            skip_dedup=args.skip_dedup,
            entity_name=args.entity,
        )


if __name__ == "__main__":
    main()
