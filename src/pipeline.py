"""Pipeline orchestrator."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable, List, Optional

from . import config
from .chunker import chunk_book
from .utils import slugify

logger = logging.getLogger(__name__)


class Pipeline:
    """Runs pipeline stages in order."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self) -> GeminiClient:
        if self._client is None:
            from .gemini_client import GeminiClient

            self._client = GeminiClient()
            self._client.preflight()
        return self._client

    def _discover_books(self) -> List[Path]:
        """Discover book folders, excluding Apocrypha (handled manually)."""
        books: List[Path] = []
        for path in config.BOOKS_DIR.iterdir():
            if path.is_dir() and not path.name.startswith(".") and path.name.lower() != "apocrypha":
                books.append(path)
        return books

    def _resolve_books(self, book: Optional[str]) -> Iterable[Path]:
        if book:
            candidate = Path(book)
            if candidate.exists():
                return [candidate]
            slugged = slugify(book)
            direct = config.BOOKS_DIR / book
            slug_dir = config.BOOKS_DIR / slugged
            for opt in (direct, slug_dir):
                if opt.exists():
                    return [opt]
            raise FileNotFoundError(f"Book '{book}' not found under {config.BOOKS_DIR}")
        return self._discover_books()

    # --- stages ---------------------------------------------------------
    def run(
        self,
        stage: str = "all",
        book: Optional[str] = None,
        entity_type: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,
        limit: Optional[int] = None,
        clean_targets: Optional[List[str]] = None,
        yes: bool = False,
        max_retries: Optional[int] = None,
        skip_dedup: bool = False,
        entity_name: Optional[str] = None,
    ) -> None:
        stages = ["chunk", "extract", "merge", "synthesize", "render", "clean"]
        if stage != "all" and stage not in stages:
            raise ValueError(f"Unknown stage '{stage}'")

        if stage == "clean":
            self.clean(targets=clean_targets, yes=yes)
            return
        if stage in ("chunk", "all"):
            self.chunk(book=book, force=force)
        if stage in ("extract", "all"):
            self.extract(
                book=book,
                entity_type=entity_type,
                force=force,
                dry_run=dry_run,
                limit=limit,
                entity_name=entity_name,
            )
        if stage in ("merge", "all"):
            self.merge(entity_type=entity_type, force=force, skip_dedup=skip_dedup, book=book)
        if stage in ("synthesize", "all"):
            self.synthesize(entity_type=entity_type, force=force, max_retries=max_retries, book=book, entity_name=entity_name)
        if stage in ("render", "all"):
            self.render()

    def chunk(self, book: Optional[str] = None, force: bool = False) -> None:
        for book_dir in self._resolve_books(book):
            logger.info("Chunking book %s", book_dir.name)
            chunk_book(book_dir, force=force)

    def extract(
        self,
        book: Optional[str] = None,
        entity_type: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,

        limit: Optional[int] = None,
        entity_name: Optional[str] = None,
    ) -> None:
        from .extractor import extract_entities_for_book

        client = None if dry_run else self._get_client()
        for book_dir in self._resolve_books(book):
            logger.info("Extracting entities for %s", book_dir.name)
            types = [entity_type] if entity_type else config.ENTITY_TYPES
            extract_entities_for_book(
                book_dir.name,
                client,
                entity_types=types,
                force=force,
                dry_run=dry_run,
                limit=limit,
            )

    def merge(self, entity_type: Optional[str] = None, force: bool = False, skip_dedup: bool = False, book: Optional[str] = None) -> None:
        from .merger import merge_entities, apply_entity_filters, build_type_voting_map, get_type_exclusions
        from .gemini_client import GeminiClient

        # Use merge-specific model (3 Flash for better dedup accuracy)
        # unless user explicitly overrode via --model flag
        merge_model = config.GEMINI_MODEL_MERGE if hasattr(config, 'GEMINI_MODEL_MERGE') else config.GEMINI_MODEL
        if config.GEMINI_MODEL != "gemini-2.5-flash-lite-preview-09-2025":
            # User explicitly chose a model via --model, respect that
            merge_model = config.GEMINI_MODEL
        
        # ================================================================
        # BOOK-ISOLATED ARCHITECTURE: Process each book independently
        # ================================================================
        if book:
            # Single book specified
            books_to_process = [book]
        else:
            # No book specified: iterate through ALL books independently
            # This prevents cross-book entity conflation (e.g., Dajeil + Lededje)
            books_to_process = self._discover_books()
            logger.info("Book-isolated merge: processing %d books independently", len(books_to_process))
        
        for book_path in books_to_process:
            book_name = book_path.name if hasattr(book_path, 'name') else str(book_path)
            book_slug = slugify(book_name)
            logger.info("")
            logger.info("=" * 80)
            logger.info("MERGING BOOK: %s", book_name)
            logger.info("=" * 80)
            
            self._merge_single_book(
                book_slug=book_slug,
                entity_type=entity_type,
                force=force,
                skip_dedup=skip_dedup,
                merge_model=merge_model,
            )
    
    def _merge_single_book(self, book_slug: str, entity_type: Optional[str], force: bool, skip_dedup: bool, merge_model: str) -> None:
        """Merge entities for a single book in isolation."""
        from .merger import merge_entities, apply_entity_filters, build_type_voting_map, get_type_exclusions
        from .gemini_client import GeminiClient
        from .validator import verify_book_extractions
        
        # New automated validation step: verify aliases exist in source text
        # This catches LLM hallucinations (e.g. "Ms Gelian" on Ulver Seich)
        logger.info("Verifying extractions for %s...", book_slug)
        verify_book_extractions(book_slug=book_slug, verbose=False)
        
        client = None
        if not skip_dedup:
            client = GeminiClient(model_name=merge_model)
            client.preflight()
            logger.info("Using model %s for merge", merge_model)
        
        # Build type voting map for THIS BOOK ONLY
        logger.info("Building type voting map for %s...", book_slug)
        voting_map = build_type_voting_map(book_slug=book_slug)
        
        types = [entity_type] if entity_type else config.ENTITY_TYPES
        
        # ====================================================================
        # PASS 1: Merge all types (type voting only, no cross-type filters)
        # ====================================================================
        logger.info("")
        logger.info("=" * 80)
        logger.info("PASS 1: Merging all entity types (type voting only)")
        logger.info("=" * 80)
        
        merge_stats = []
        for idx, et in enumerate(types, 1):
            logger.info("=== Merging %d/%d: %s ===", idx, len(types), et)
            
            # Get exclusions for this entity type based on type voting
            type_exclusions = get_type_exclusions(voting_map, et)
            if type_exclusions:
                logger.info("Type voting: %d entities will be excluded from %s", len(type_exclusions), et)
            
            # Pass 1: skip_filters=True (no cross-type or LLM filters)
            stats = merge_entities(et, force=force, skip_dedup=True, client=None, book_slug=book_slug, type_exclusions=type_exclusions, skip_filters=True)
            merge_stats.append(stats)
        
        # ====================================================================
        # PASS 2: Apply filters (cross-type + LLM) with fresh data
        # ====================================================================
        logger.info("")
        logger.info("=" * 80)
        logger.info("PASS 2: Applying filters (all entity data now available)")
        logger.info("=" * 80)
        
        filter_stats = []
        for idx, et in enumerate(types, 1):
            logger.info("=== Filtering %d/%d: %s ===", idx, len(types), et)
            stats = apply_entity_filters(et, skip_dedup=skip_dedup, client=client, book_slug=book_slug)
            filter_stats.append(stats)
        
        # ====================================================================
        # Summary table
        # ====================================================================
        logger.info("")
        logger.info("=" * 116)
        logger.info("MERGE COMPLETE - Summary by Filter Stage")
        logger.info("=" * 116)
        logger.info("%-18s %6s %8s %8s %8s %8s %8s %8s %8s", "Entity Type", "Raw", "Consolid", "TypeVote", "Determ", "Garbage", "Dedup", "Cross", "Final")
        logger.info("-" * 116)
        totals = {"raw": 0, "consolidate": 0, "type_voting": 0, "deterministic": 0, "garbage": 0, "dedup": 0, "cross": 0, "final": 0}
        
        for ms, fs in zip(merge_stats, filter_stats):
            # Combine stats from both passes
            row = {
                "entity_type": ms["entity_type"],
                "raw": ms["raw"],
                "consolidate": ms["consolidate"],
                "type_voting": ms.get("type_voting", 0),
                "deterministic": fs["deterministic"],
                "garbage": fs["garbage"],
                "dedup": fs["dedup"],
                "cross": fs["cross"],
                "final": fs["final"],
            }
            logger.info("%-18s %6d %8d %8d %8d %8d %8d %8d %8d", 
                       row["entity_type"], row["raw"], -row["consolidate"], -row["type_voting"], -row["deterministic"], -row["garbage"], -row["dedup"], -row["cross"], row["final"])
            for k in totals:
                totals[k] += row.get(k, 0)
        
        logger.info("-" * 116)
        logger.info("%-18s %6d %8d %8d %8d %8d %8d %8d %8d", 
                   "TOTAL", totals["raw"], -totals["consolidate"], -totals["type_voting"], -totals["deterministic"], -totals["garbage"], -totals["dedup"], -totals["cross"], totals["final"])
        logger.info("=" * 116)
        logger.info("")
        logger.info("LEGEND:")
        logger.info("  Raw       - Total entities extracted from source text before any filtering")
        logger.info("  Consolid  - Entities merged because they share names or aliases (e.g., 'Dajeil' + 'Dajeil Gelian')")
        logger.info("  TypeVote  - Removed because entity appears more often in a different type (e.g., 'Veppers' is 95%% character, 5%% ship)")
        logger.info("  Determ    - Removed by cross-type filters: known ship names, known character names in wrong types")
        logger.info("  Garbage   - Removed by LLM: generic terms like 'the woman', 'Speaker 1' that aren't real entities")
        logger.info("  Dedup     - Removed by LLM: semantic duplicates the filters missed (e.g., 'the Sleeper' = 'Sleeper Service')")
        logger.info("  Cross     - Removed by LLM: entities that belong to a different type (e.g., ship names in characters)")
        logger.info("  Final     - Entities remaining after all filtering stages")

    def synthesize(
        self, entity_type: Optional[str] = None, force: bool = False, max_retries: Optional[int] = None, book: Optional[str] = None, entity_name: Optional[str] = None
    ) -> None:
        from .synthesizer import synthesize_articles
        from .gemini_client import GeminiClient

        # Use synthesis-specific model (3-flash for better prose quality)
        # unless user explicitly overrode via --model flag
        synthesis_model = config.GEMINI_MODEL_SYNTHESIS if hasattr(config, 'GEMINI_MODEL_SYNTHESIS') else config.GEMINI_MODEL
        # Check if model was overridden by CLI (run.py sets GEMINI_MODEL when --model is used)
        if config.GEMINI_MODEL != "gemini-2.5-flash-lite-preview-09-2025":
            # User explicitly chose a model via --model, respect that
            synthesis_model = config.GEMINI_MODEL
        
        # ================================================================
        # BOOK-ISOLATED ARCHITECTURE: Process each book independently
        # ================================================================
        if book:
            # Single book specified
            books_to_process = [book]
        else:
            # No book specified: iterate through ALL books independently
            books_to_process = self._discover_books()
            logger.info("Book-isolated synthesis: processing %d books independently", len(books_to_process))
        
        client = GeminiClient(model_name=synthesis_model)
        client.preflight()
        logger.info("Using model %s for synthesis", synthesis_model)
        
        types = [entity_type] if entity_type else config.ENTITY_TYPES
        
        for item in books_to_process:
            book_name = item.name if hasattr(item, 'name') else str(item)
            book_slug = slugify(book_name)
            logger.info("")
            logger.info("=" * 80)
            logger.info("SYNTHESIZING BOOK: %s", book_name)
            logger.info("=" * 80)
            
            for idx, et in enumerate(types, 1):
                logger.info("=== Synthesizing %d/%d: %s ===", idx, len(types), et)
                synthesize_articles(et, client, force=force, max_retries=max_retries, book_slug=book_slug, entity_name=entity_name)

    def render(self) -> None:
        from .renderer import render_all

        render_all()

    def clean(self, targets: Optional[List[str]] = None, yes: bool = False) -> None:
        target_map = {
            "cache": config.CACHE_DIR,
            "extractions": config.EXTRACTIONS_DIR,
            "entities": config.ENTITIES_DIR,
            "articles": config.ARTICLES_DIR,
            "wiki": config.WIKI_DIR,
            "chunks": config.CHUNKS_DIR,
        }

        selected = targets or ["all"]
        paths: List[Path] = []
        if "all" in selected:
            paths = list(target_map.values())
        else:
            for target in selected:
                if target == "output":
                    paths.extend([config.ARTICLES_DIR, config.WIKI_DIR])
                else:
                    paths.append(target_map[target])

        unique_paths: List[Path] = []
        for path in paths:
            if path not in unique_paths:
                unique_paths.append(path)

        if not unique_paths:
            logger.info("No clean targets selected")
            return

        logger.info("Clean will remove:")
        for path in unique_paths:
            logger.info("  - %s", path)

        if not yes:
            response = input("Delete these paths? [y/N]: ").strip().lower()
            if response not in ("y", "yes"):
                logger.info("Clean cancelled.")
                return

        for path in unique_paths:
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
