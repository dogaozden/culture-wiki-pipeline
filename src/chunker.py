"""Load and chunk chapter files (md or html)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, List

from . import config
from .models import Chapter
from .utils import count_tokens, slugify

logger = logging.getLogger(__name__)

SCENE_BREAK_PATTERN = re.compile(r"\n\s*[\*\-_]{3,}\s*\n", re.MULTILINE)


def extract_chapter_title(text: str, fallback: str) -> str:
    """Extract chapter title from the first H1 or first non-empty line."""
    header_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if header_match:
        return header_match.group(1).strip()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return fallback


def _commit_chunk(
    base_chapter: Chapter, chunk_text: str, part_number: int | None = None
) -> Chapter:
    """Create a Chapter instance for a specific chunk of text."""
    return base_chapter.model_copy(
        update={
            "text": chunk_text,
            "token_count": count_tokens(chunk_text),
            "is_split": part_number is not None,
            "split_part": part_number,
        }
    )


def _chunk_segments(
    base_chapter: Chapter, segments: Iterable[str], max_tokens: int
) -> List[Chapter]:
    """Chunk a list of text segments into parts under the token limit."""
    chunks: List[Chapter] = []
    buffer: List[str] = []

    def finalize_buffer():
        if not buffer:
            return
        chunk_text = "\n\n".join(buffer).strip()
        if chunk_text:
            chunks.append(_commit_chunk(base_chapter, chunk_text, len(chunks) + 1))
        buffer.clear()

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue

        seg_tokens = count_tokens(seg)
        if seg_tokens > max_tokens:
            # Flush what we have so far, then split the oversized segment further.
            finalize_buffer()
            paragraphs = re.split(r"\n\s*\n", seg)
            if len(paragraphs) == 1:
                logger.warning(
                    "Segment still exceeds max tokens (%s) for chapter %s; splitting by length",
                    max_tokens,
                    base_chapter.source_file,
                )
                midpoint = len(seg) // 2
                paragraphs = [seg[:midpoint], seg[midpoint:]]
            chunks.extend(_chunk_segments(base_chapter, paragraphs, max_tokens))
            continue

        candidate_parts = buffer + [seg]
        candidate_text = "\n\n".join(candidate_parts)
        if count_tokens(candidate_text) > max_tokens and buffer:
            finalize_buffer()
            buffer.append(seg)
        else:
            buffer.append(seg)

    finalize_buffer()
    return chunks


def split_long_chapter(
    chapter: Chapter, max_tokens: int = config.MAX_CHAPTER_TOKENS
) -> List[Chapter]:
    """Split a chapter into sub-chunks if it exceeds the token limit."""
    if chapter.token_count <= max_tokens:
        return [chapter]

    segments = SCENE_BREAK_PATTERN.split(chapter.text)
    if len(segments) == 1:
        logger.info(
            "Splitting long chapter %s by paragraphs (no scene breaks found)",
            chapter.source_file,
        )
        segments = re.split(r"\n\s*\n", chapter.text)

    chunks = _chunk_segments(chapter, segments, max_tokens)
    # Ensure part numbers are sequential
    for idx, chunk in enumerate(chunks, 1):
        chunks[idx - 1] = chunk.model_copy(update={"split_part": idx, "is_split": True})
    return chunks


def load_chapters(
    book_dir: Path,
    max_tokens: int = config.MAX_CHAPTER_TOKENS,
) -> List[Chapter]:
    """Load chapter files from a book directory."""
    book_dir = Path(book_dir)
    if not book_dir.exists():
        raise FileNotFoundError(f"Book directory not found: {book_dir}")

    book_slug = slugify(book_dir.name)
    book_title = book_dir.name.replace("_", " ").strip()

    def sort_key(f):
        match = re.search(r"(\d+)", f.stem)
        if match:
            return (0, int(match.group(1)))
        return (1, f.stem)

    chapter_files = sorted(
        list(book_dir.glob("*.md")) + list(book_dir.glob("*.html")),
        key=sort_key,
    )

    chapters: List[Chapter] = []
    chapter_index = 0

    for chapter_file in chapter_files:
        text = chapter_file.read_text(encoding=config.DEFAULT_ENCODING)
        text = text.strip()
        if not text:
            logger.debug("Skipping empty file %s", chapter_file.name)
            continue

        token_count = count_tokens(text)
        chapter_index += 1

        title = extract_chapter_title(text, fallback=f"Chapter {chapter_index}")
        base_chapter = Chapter(
            book_slug=book_slug,
            book_title=book_title,
            index=chapter_index,
            title=title,
            text=text,
            token_count=token_count,
            source_file=chapter_file.name,
        )

        chapter_chunks = split_long_chapter(base_chapter, max_tokens=max_tokens)
        chapters.extend(chapter_chunks)

    return chapters


def chunk_book(book_path: Path, force: bool = False) -> List[Chapter]:
    """Chunk a single book directory. No disk writes; returns Chapter objects."""
    chapters = load_chapters(book_path)
    logger.info("Loaded %s chapters for %s", len(chapters), Path(book_path).name)
    return chapters
