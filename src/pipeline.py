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
        books: List[Path] = []
        for path in config.BOOKS_DIR.iterdir():
            if path.is_dir() and not path.name.startswith("."):
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
            )
        if stage in ("merge", "all"):
            self.merge(entity_type=entity_type, force=force, skip_dedup=skip_dedup)
        if stage in ("synthesize", "all"):
            self.synthesize(entity_type=entity_type, force=force, max_retries=max_retries)
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

    def merge(self, entity_type: Optional[str] = None, force: bool = False, skip_dedup: bool = False) -> None:
        from .merger import merge_entities
        from .gemini_client import GeminiClient

        # Use merge-specific model (3 Flash for better dedup accuracy)
        # unless user explicitly overrode via --model flag
        merge_model = config.GEMINI_MODEL_MERGE if hasattr(config, 'GEMINI_MODEL_MERGE') else config.GEMINI_MODEL
        if config.GEMINI_MODEL != "gemini-2.5-flash-lite-preview-09-2025":
            # User explicitly chose a model via --model, respect that
            merge_model = config.GEMINI_MODEL
        
        client = None
        if not skip_dedup:
            client = GeminiClient(model_name=merge_model)
            client.preflight()
            logger.info("Using model %s for merge", merge_model)
        
        types = [entity_type] if entity_type else config.ENTITY_TYPES
        all_stats = []
        for idx, et in enumerate(types, 1):
            logger.info("=== Merging %d/%d: %s ===", idx, len(types), et)
            stats = merge_entities(et, force=force, skip_dedup=skip_dedup, client=client)
            all_stats.append(stats)
        
        # Final summary table
        logger.info("")
        logger.info("=" * 100)
        logger.info("MERGE COMPLETE - Summary by Filter Stage")
        logger.info("=" * 100)
        logger.info("%-18s %6s %8s %8s %8s %8s %8s %8s", "Entity Type", "Raw", "Consolid", "Determ", "Garbage", "Dedup", "Cross", "Final")
        logger.info("-" * 100)
        totals = {"raw": 0, "consolidate": 0, "deterministic": 0, "garbage": 0, "dedup": 0, "cross": 0, "final": 0}
        for s in all_stats:
            logger.info("%-18s %6d %8d %8d %8d %8d %8d %8d", 
                       s["entity_type"], s["raw"], -s["consolidate"], -s["deterministic"], -s["garbage"], -s["dedup"], -s["cross"], s["final"])
            for k in totals:
                totals[k] += s[k]
        logger.info("-" * 100)
        logger.info("%-18s %6d %8d %8d %8d %8d %8d %8d", 
                   "TOTAL", totals["raw"], -totals["consolidate"], -totals["deterministic"], -totals["garbage"], -totals["dedup"], -totals["cross"], totals["final"])
        logger.info("=" * 100)

    def synthesize(
        self, entity_type: Optional[str] = None, force: bool = False, max_retries: Optional[int] = None
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
        
        client = GeminiClient(model_name=synthesis_model)
        client.preflight()
        logger.info("Using model %s for synthesis", synthesis_model)
        types = [entity_type] if entity_type else config.ENTITY_TYPES
        for idx, et in enumerate(types, 1):
            logger.info("=== Synthesizing %d/%d: %s ===", idx, len(types), et)
            synthesize_articles(et, client, force=force, max_retries=max_retries)

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
