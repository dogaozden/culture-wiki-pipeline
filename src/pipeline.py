"""Pipeline orchestrator."""

from __future__ import annotations

from __future__ import annotations

import logging
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
    ) -> None:
        stages = ["chunk", "extract", "merge", "synthesize", "render"]
        if stage != "all" and stage not in stages:
            raise ValueError(f"Unknown stage '{stage}'")

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
            self.merge(entity_type=entity_type, force=force)
        if stage in ("synthesize", "all"):
            self.synthesize(entity_type=entity_type, force=force)
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

    def merge(self, entity_type: Optional[str] = None, force: bool = False) -> None:
        from .merger import merge_entities

        types = [entity_type] if entity_type else config.ENTITY_TYPES
        for et in types:
            merge_entities(et, force=force)

    def synthesize(
        self, entity_type: Optional[str] = None, force: bool = False
    ) -> None:
        from .synthesizer import synthesize_articles

        client = self._get_client()
        types = [entity_type] if entity_type else config.ENTITY_TYPES
        for et in types:
            synthesize_articles(et, client, force=force)

    def render(self) -> None:
        from .renderer import render_all

        render_all()
