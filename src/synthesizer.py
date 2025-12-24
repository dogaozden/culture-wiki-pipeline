"""Synthesize wiki articles from merged entities and extractions."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from . import config
from .chunker import load_chapters
from .gemini_client import GeminiClient
from .models import Article, ArticleSection, Chapter, Citation, EntityExtraction, MergedEntity
from .utils import read_json, slugify, utc_now, write_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = SYSTEM_PROMPT = """You are writing a comprehensive wiki article for a Culture series encyclopedia. 

CRITICAL REQUIREMENT:
You MUST include EVERY fact provided below. Do not skip, summarize, or editorially select facts.
The extraction stage has already determined what is significant. Your job is to weave ALL provided 
facts into a coherent article, not to judge what's important.

WRITING GUIDELINES:
- Write in an engaging, encyclopedic style that captures the drama and nuance of the source material
- EVERY factual claim must have an inline citation in the format [N] where N is the citation number
- Use ONLY the provided facts and quotes—do not add information not present in the sources
- Include ALL facts provided, even if they seem minor or repetitive
- Write in flowing prose, not bullet points
- Let the article length be determined by the facts—do not compress to hit a word target

STRUCTURE REQUIREMENTS:
For characters, include sections covering (where applicable):
- Background and origins
- Physical description and distinguishing traits  
- Personality and psychology
- Key relationships (with specific details about each)
- Chronological narrative of their role in events
- Notable quotes or memorable moments
- Fate/outcome

For ships and Minds, include:
- Class, designation, and physical description
- Personality and behavioral traits
- Role in events (chronologically)
- Relationships with other entities
- Notable actions or decisions

For locations, include:
- Physical description and geography
- Significance in the narrative
- Events that took place there
- Who inhabits or visits the location

For technology, include:
- Description and function
- How it's used in the narrative
- Who uses it and in what context

For factions, include:
- Overview and philosophy
- Key members or representatives
- Role in events
- Relationships with other factions

For events, include:
- Chronological narrative of what happened
- Key participants
- Causes and consequences
- Significance in the broader context

CITATION RULES:
- Cite liberally—almost every sentence should have at least one citation
- Group related citations: [1][2] or [1,2] for claims supported by multiple sources
- Prefer direct quotes for dramatic or significant moments
- You should have roughly as many citations as facts provided"""

CITATION_PATTERN = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
CHUNK_ID_PATTERN = re.compile(r"([a-z0-9-]+_ch\d{3}(?:_p\d{2})?)")

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


def _normalize_for_match(text: str) -> str:
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
    }
    for src, dest in replacements.items():
        text = text.replace(src, dest)
    return " ".join(text.split())


def _quote_in_text(quote: str, text: str) -> bool:
    if not quote or not text:
        return False
    return _normalize_for_match(quote) in _normalize_for_match(text)


def _extract_citation_ids(text: str) -> set[int]:
    ids: set[int] = set()
    for match in CITATION_PATTERN.finditer(text or ""):
        numbers = [n.strip() for n in match.group(1).split(",") if n.strip()]
        for num in numbers:
            try:
                ids.add(int(num))
            except ValueError:
                continue
    return ids


# Minimum citation rate (citations per sentence) to accept an article
MIN_CITATION_RATE = 0.5  # At least 50% of sentences should have citations

def _count_sentences(text: str) -> int:
    """Count approximate number of sentences in text."""
    if not text:
        return 0
    # Simple sentence counting: split on .!? followed by space or end
    import re
    sentences = re.split(r'[.!?]+(?:\s|$)', text)
    return len([s for s in sentences if s.strip()])


def _count_citations_in_text(text: str) -> int:
    """Count number of citation markers [N] in text."""
    return len(CITATION_PATTERN.findall(text or ""))


def _check_citation_density(summary: str, sections: List, entity_name: str) -> bool:
    """
    Check if article has sufficient citation density.
    Returns True if OK, False if too few citations.
    """
    total_sentences = _count_sentences(summary)
    total_citations = _count_citations_in_text(summary)
    
    for section in sections:
        content = getattr(section, 'content', '') or section.get('content', '')
        total_sentences += _count_sentences(content)
        total_citations += _count_citations_in_text(content)
    
    if total_sentences == 0:
        return True  # Edge case
    
    citation_rate = total_citations / total_sentences
    
    if citation_rate < MIN_CITATION_RATE:
        logger.warning(
            "Low citation density for %s: %d citations / %d sentences = %.1f%% (min: %.0f%%)",
            entity_name,
            total_citations,
            total_sentences,
            citation_rate * 100,
            MIN_CITATION_RATE * 100,
        )
        return False
    
    return True


def _normalize_chunk_id(raw: str | None) -> str:
    if not raw:
        return ""
    cleaned = raw.strip().lower()
    match = CHUNK_ID_PATTERN.search(cleaned)
    if match:
        return match.group(1)
    return cleaned


def _base_chunk_id(chunk_id: str) -> str:
    if not chunk_id:
        return ""
    return chunk_id.split("_p", 1)[0]


def _compute_prominence_score(citations: List[Citation], related: List[str]) -> int:
    unique_chapters = {
        _base_chunk_id(c.chunk_id) for c in citations if getattr(c, "chunk_id", None)
    }
    unique_chapter_count = len({c for c in unique_chapters if c})
    citation_count = len(citations)
    related_set = {name.strip().lower() for name in related if name and name.strip()}
    cross_reference_count = len(related_set)
    return unique_chapter_count * 10 + citation_count + cross_reference_count


def _format_extractions_for_prompt(
    pairs: List[Tuple[EntityExtraction, Dict]],
    chunk_map: Dict[str, Chapter],
) -> str:
    lines: List[str] = []
    for extraction, ent in pairs:
        chapter = chunk_map.get(extraction.chunk_id)
        if chapter is None:
            logger.warning(
                "Missing chapter text for %s; skipping extraction facts",
                extraction.chunk_id,
            )
            continue
        valid_infos: List[Dict] = []
        lines.append(
            f"- Source chunk {extraction.chunk_id} ({extraction.chapter_title}) for {ent['name']}"
        )
        for info in ent.get("information", []):
            fact = info.get("fact", "").strip()
            quote = info.get("quote", "").strip()
            context = info.get("location") or ""
            if not _quote_in_text(quote, chapter.text):
                continue
            valid_infos.append(info)
            lines.append(
                f"  • Fact: {fact}\n    Quote: \"{quote}\"\n    Context: {context}"
            )
        if not valid_infos:
            lines.pop()
    return "\n".join(lines)


def build_synthesis_prompt(
    merged: MergedEntity, entity_type: str, formatted_extractions: str
) -> str:
    appears_in = ", ".join(merged.appears_in)
    aliases_str = ", ".join(merged.aliases) if merged.aliases else "None"
    
    # Count facts for reference
    fact_count = formatted_extractions.count("• Fact:")

    return f"""{SYSTEM_PROMPT}

ENTITY DETAILS:
- Name: {merged.canonical_name}
- Also known as: {aliases_str}
- Type: {entity_type}
- Appears in: {appears_in}
- Facts provided: {fact_count} (you must use ALL of them)

EXTRACTED INFORMATION (use ALL of this):
{formatted_extractions}

Return a JSON object with:
- summary: A compelling 2-3 sentence overview of the entity
- sections: Array of objects with "title" and "content" (with [N] inline citations throughout). Include 4-8 sections for major entities.
- citations: Array of objects with fields: id (integer), book, chapter (optional), quote (COPY EXACTLY from the Quote field above - do not paraphrase), chunk_id (copy from Source chunk ID above)
- related_entities: Array of names of other entities mentioned (for cross-linking)

CRITICAL: For each citation, the "quote" field must be copied EXACTLY from the provided quotes above. Do not paraphrase or generate new quotes."""


def _build_chunk_context_map() -> Dict[str, Chapter]:
    chunk_map: Dict[str, Chapter] = {}
    if not config.BOOKS_DIR.exists():
        return chunk_map
    for book_dir in config.BOOKS_DIR.iterdir():
        if not book_dir.is_dir() or book_dir.name.startswith("."):
            continue
        try:
            chapters = load_chapters(book_dir)
        except Exception as exc:
            logger.warning("Failed to load chapters for %s: %s", book_dir.name, exc)
            continue
        for chapter in chapters:
            chunk_map[chapter.chunk_id] = chapter
    return chunk_map


def synthesize_articles(
    entity_type: str, client: GeminiClient, force: bool = False
) -> None:
    """Generate wiki articles for a specific entity type."""
    merged_entities = _load_merged_entities(entity_type)
    extractions = _load_extractions(entity_type)
    chunk_map = _build_chunk_context_map()
    output_dir = config.ARTICLES_DIR / entity_type
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_slugs = {slugify(ent.canonical_name) for ent in merged_entities}

    if force:
        for path in output_dir.glob("*.json"):
            if path.stem not in expected_slugs:
                logger.info("Removing stale article %s", path.name)
                path.unlink()

    pending: List[MergedEntity] = []
    for merged in merged_entities:
        slug = slugify(merged.canonical_name)
        outfile = output_dir / f"{slug}.json"
        if outfile.exists() and not force:
            logger.info("Skipping synthesis for %s (cached)", merged.canonical_name)
            continue
        pending.append(merged)

    if not pending:
        logger.info("No entities pending synthesis for %s", entity_type)
        return

    def _synthesize_one(merged: MergedEntity) -> None:
        slug = slugify(merged.canonical_name)
        outfile = output_dir / f"{slug}.json"

        matches = _matching_extractions(merged, extractions)
        if not matches:
            logger.warning("No extractions found for %s; skipping", merged.canonical_name)
            if force and outfile.exists():
                outfile.unlink()
            return

        formatted = _format_extractions_for_prompt(matches, chunk_map)
        if not formatted.strip():
            logger.warning(
                "No valid quotes found for %s; skipping synthesis", merged.canonical_name
            )
            if force and outfile.exists():
                outfile.unlink()
            return
        prompt = build_synthesis_prompt(merged, entity_type, formatted)
        try:
            payload = client.synthesize(prompt)
            citations_payload = payload.get("citations", [])
            citations: List[Citation] = []
            for item in citations_payload:
                if not isinstance(item, dict):
                    logger.warning(
                        "Skipping malformed citation for %s: %r",
                        merged.canonical_name,
                        item,
                    )
                    continue
                raw_chunk_id = item.get("chunk_id")
                chunk_id = _normalize_chunk_id(
                    str(raw_chunk_id) if raw_chunk_id is not None else ""
                )
                chapter = chunk_map.get(chunk_id)
                if chapter is None:
                    logger.warning(
                        "Unknown chunk_id for citation in %s: %r",
                        merged.canonical_name,
                        raw_chunk_id,
                    )
                    continue
                quote = str(item.get("quote", "")).strip()
                if not _quote_in_text(quote, chapter.text):
                    logger.warning(
                        "Citation quote not found for %s (%s)",
                        merged.canonical_name,
                        chunk_id,
                    )
                    continue
                item = {
                    **item,
                    "chunk_id": chunk_id,
                    "source_file": chapter.source_file,
                    "chapter": chapter.title,
                    "book": chapter.book_title,
                }
                citations.append(Citation(**item))

            sections = [
                ArticleSection(**section) for section in payload.get("sections", [])
            ]
            related_entities = payload.get("related_entities", []) or []
            if not isinstance(related_entities, list):
                related_entities = []
            prominence_score = _compute_prominence_score(citations, related_entities)
            article = Article(
                entity_name=merged.canonical_name,
                entity_type=entity_type.rstrip("s"),
                slug=slug,
                aliases=merged.aliases,
                appears_in=merged.appears_in,
                summary=payload.get("summary", ""),
                sections=sections,
                citations=citations,
                related_entities=related_entities,
                prominence_score=prominence_score,
                generated_at=utc_now(),
            )
            cited_ids = set(c.id for c in citations)
            referenced_ids = set()
            referenced_ids.update(_extract_citation_ids(article.summary))
            for section in sections:
                referenced_ids.update(_extract_citation_ids(section.content))
            if referenced_ids and not referenced_ids.issubset(cited_ids):
                missing = sorted(referenced_ids - cited_ids)
                logger.warning(
                    "Missing citations for %s; skipping article. Missing ids: %s",
                    merged.canonical_name,
                    missing,
                )
                if force and outfile.exists():
                    outfile.unlink()
                return

            # Check citation density - reject articles with too few citations
            if not _check_citation_density(article.summary, sections, merged.canonical_name):
                logger.warning(
                    "Skipping article for %s due to low citation density",
                    merged.canonical_name,
                )
                if force and outfile.exists():
                    outfile.unlink()
                return

            write_json(article.model_dump(), outfile)
            logger.info("Synthesized article for %s", merged.canonical_name)
        except Exception as exc:
            logger.error("Failed to synthesize %s: %s", merged.canonical_name, exc)
            if force:
                raise

    if config.PARALLEL_WORKERS <= 1:
        for merged in pending:
            _synthesize_one(merged)
        return

    with ThreadPoolExecutor(max_workers=config.PARALLEL_WORKERS) as executor:
        futures = {executor.submit(_synthesize_one, merged): merged for merged in pending}
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                logger.error(
                    "Synthesis worker failed for %s: %s",
                    futures[future].canonical_name,
                    exc,
                )
                if force:
                    raise exc
