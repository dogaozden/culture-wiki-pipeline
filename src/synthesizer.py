"""Synthesize wiki articles from merged entities and extractions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from . import config
from .gemini_client import GeminiClient
from .models import Article, ArticleSection, Citation, EntityExtraction, MergedEntity
from .utils import read_json, slugify, utc_now, write_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are writing a wiki article for a Culture series encyclopedia. Write in an encyclopedic tone. EVERY factual claim must have an inline citation in the format [N] where N is the citation number. Use ONLY the provided facts and quotes. Do not add information that is not present in the sources."""


def _load_extractions(entity_type: str) -> List[EntityExtraction]:
    base_dir = config.EXTRACTIONS_DIR / entity_type
    extractions: List[EntityExtraction] = []
    if not base_dir.exists():
        return extractions
    for book_dir in base_dir.iterdir():
        if not book_dir.is_dir():
            continue
        for path in book_dir.glob("*.json"):
            extractions.append(EntityExtraction(**read_json(path)))
    return extractions


def _load_merged_entities(entity_type: str) -> List[MergedEntity]:
    path = config.ENTITIES_DIR / f"{entity_type}.json"
    if not path.exists():
        raise FileNotFoundError(f"Merged entities not found for {entity_type} at {path}")
    payload = read_json(path)
    return [MergedEntity(**item) for item in payload.get("entities", [])]


def _matching_extractions(
    merged: MergedEntity, extractions: Iterable[EntityExtraction]
) -> List[Tuple[EntityExtraction, Dict]]:
    names = {merged.canonical_name.lower(), *[a.lower() for a in merged.aliases]}
    matches: List[Tuple[EntityExtraction, Dict]] = []
    for extraction in extractions:
        for ent in extraction.entities:
            ent_names = {ent.name.lower(), *[a.lower() for a in ent.aliases]}
            if names & ent_names:
                matches.append((extraction, ent.model_dump()))
    return matches


def _format_extractions_for_prompt(pairs: List[Tuple[EntityExtraction, Dict]]) -> str:
    lines: List[str] = []
    for extraction, ent in pairs:
        lines.append(
            f"- Source chunk {extraction.chunk_id} ({extraction.chapter_title}) for {ent['name']}"
        )
        for info in ent.get("information", []):
            fact = info.get("fact", "").strip()
            quote = info.get("quote", "").strip()
            context = info.get("location") or ""
            lines.append(
                f"  • Fact: {fact}\n    Quote: \"{quote}\"\n    Context: {context}"
            )
    return "\n".join(lines)


def build_synthesis_prompt(
    merged: MergedEntity, entity_type: str, formatted_extractions: str
) -> str:
    appears_in = ", ".join(merged.appears_in)
    return f"""{SYSTEM_PROMPT}

USER: Write a wiki article for: {merged.canonical_name}
Type: {entity_type}
Appears in: {appears_in}

EXTRACTED INFORMATION:
{formatted_extractions}

Return a JSON object with:
- summary
- sections: array of objects with title and content (with [N] inline citations)
- citations: array of objects with fields id, book, chapter (optional), quote, chunk_id
- related_entities: optional array of related entity names
"""


def synthesize_articles(
    entity_type: str, client: GeminiClient, force: bool = False
) -> None:
    """Generate wiki articles for a specific entity type."""
    merged_entities = _load_merged_entities(entity_type)
    extractions = _load_extractions(entity_type)
    output_dir = config.ARTICLES_DIR / entity_type
    output_dir.mkdir(parents=True, exist_ok=True)

    for merged in merged_entities:
        slug = slugify(merged.canonical_name)
        outfile = output_dir / f"{slug}.json"
        if outfile.exists() and not force:
            logger.info("Skipping synthesis for %s (cached)", merged.canonical_name)
            continue

        matches = _matching_extractions(merged, extractions)
        if not matches:
            logger.warning("No extractions found for %s; skipping", merged.canonical_name)
            continue

        formatted = _format_extractions_for_prompt(matches)
        prompt = build_synthesis_prompt(merged, entity_type, formatted)
        cache_key = f"synth:{entity_type}:{slug}"

        try:
            payload = client.synthesize(prompt, cache_key=cache_key)
            article = Article(
                entity_name=merged.canonical_name,
                entity_type=entity_type.rstrip("s"),
                slug=slug,
                aliases=merged.aliases,
                appears_in=merged.appears_in,
                summary=payload.get("summary", ""),
                sections=[
                    ArticleSection(**section)
                    for section in payload.get("sections", [])
                ],
                citations=[Citation(**c) for c in payload.get("citations", [])],
                related_entities=payload.get("related_entities", []),
                generated_at=utc_now(),
            )
            write_json(article.model_dump(), outfile)
            logger.info("Synthesized article for %s", merged.canonical_name)
        except Exception as exc:
            logger.error("Failed to synthesize %s: %s", merged.canonical_name, exc)
            if force:
                raise
