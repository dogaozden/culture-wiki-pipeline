"""Synthesize wiki articles from merged entities and extractions."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from . import config
from .chunker import load_chapters
from .gemini_client import GeminiClient
from .models import Article, ArticleSection, Chapter, Citation, EntityExtraction, MergedEntity
from .utils import read_json, slugify, utc_now, write_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are writing a comprehensive wiki article for a Culture series encyclopedia.

CRITICAL REQUIREMENT:
You MUST include EVERY fact provided below. Do not skip, summarize, or editorially select facts.
The extraction stage has already determined what is significant. Your job is to weave ALL provided
facts into a coherent article, not to judge what's important.

WRITING GUIDELINES:
- Write in an engaging, encyclopedic style that captures the drama and nuance of the source material
- EVERY factual claim must have an inline citation using the EXACT fact ID provided (e.g., [excession_ch001#003])
- Use ONLY the provided facts and quotes—do not add information not present in the sources
- Include ALL facts provided, even if they seem minor or repetitive
- Write in flowing prose, not bullet points
- Let the article length be determined by the facts—do not compress to hit a word target

NARRATIVE APPROACH:
- Facts are provided in CHRONOLOGICAL ORDER (by chapter). Use this to tell the story as it unfolds.
- Create a compelling narrative that follows the character's journey through the novel
- Build tension, highlight turning points, and capture emotional beats
- Connect events causally—show how earlier events lead to later consequences
- For major characters, the article should read like a dramatic biography, not a dry list of facts

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
- Use the EXACT fact IDs provided (format: [chunk_id#seq], e.g., [excession_ch001#003])
- Do NOT renumber citations—use the IDs exactly as given
- EVERY FACT MUST BE CITED: If there are 100 facts, your article must have ~100 citations
- Maximum 2 citations per sentence—if you have many facts, write more sentences!
- Prefer direct quotes for dramatic or significant moments
- The number of citations in your article should equal the number of facts provided"""

# Pattern to match fact ID citations like [excession_ch001#003] or [excession_ch001#001, excession_ch002#005]
FACT_ID_PATTERN = re.compile(r"\[([a-z0-9_#,\s]+)\]", re.IGNORECASE)
# Legacy pattern for integer citations (kept for backwards compatibility during transition)
CITATION_PATTERN = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
CHUNK_ID_PATTERN = re.compile(r"([a-z0-9-]+_(?:ch\d{3}|split_\d{3})(?:_p\d{2})?)")


def _get_missing_citation_tolerance(fact_count: int) -> int:
    """
    Return how many missing citations are acceptable based on total fact count.
    Larger entities get more tolerance since LLMs struggle to cite 100+ facts perfectly.
    """
    if fact_count > 80:
        return 3
    elif fact_count > 50:
        return 2
    elif fact_count > 25:
        return 2
    elif fact_count > 10:
        return 1
    else:
        return 0  # Small entities must have perfect citations

def _load_extractions(entity_type: str, book_slug: str = None) -> List[EntityExtraction]:
    """Load extractions for an entity type, optionally filtered by book.
    
    Args:
        entity_type: The entity type to load (e.g., 'factions')
        book_slug: If provided, only load extractions from this book's folder.
                   Special value "cross-book" loads ALL books (for cross-book synthesis).
    """
    base_dir = config.EXTRACTIONS_DIR / entity_type
    extractions: List[EntityExtraction] = []
    if not base_dir.exists():
        return extractions
    
    # Special case: "cross-book" means load from ALL books
    load_all = book_slug == "cross-book"
    
    for book_dir in base_dir.iterdir():
        if not book_dir.is_dir():
            continue
        
        # Filter by book if specified (unless loading all for cross-book)
        if book_slug and not load_all:
            # Normalize both slugs for comparison (handle hyphens vs underscores)
            dir_slug = book_dir.name.lower().replace("_", "-")
            target_slug = book_slug.lower().replace("_", "-")
            if dir_slug != target_slug:
                continue  # Skip this book's extractions
        
        for path in book_dir.glob("*.json"):
            extractions.append(EntityExtraction(**read_json(path)))
    
    if book_slug:
        logger.info("Loaded %d extractions for %s (book: %s)", len(extractions), entity_type, book_slug)
    
    return extractions


def _build_fact_lookup(extractions: List[EntityExtraction]) -> Dict[str, dict]:
    """Build lookup from fact_id -> fact info for citation rebuilding.
    
    This enables O(1) lookup when the LLM uses a [fact_id] tag.
    The quote and metadata come from the original extraction, not the LLM.
    """
    lookup: Dict[str, dict] = {}
    for extraction in extractions:
        for entity in extraction.entities:
            for info in entity.information:
                fact_id = info.id.lower() if info.id else None
                if fact_id:
                    # Infer book title from chunk_id (e.g. excession_split_001 -> Excession)
                    chunk_id = extraction.chunk_id
                    book_title = chunk_id.split("_")[0].title() if "_" in chunk_id else "Unknown"
                    
                    lookup[fact_id] = {
                        "id": info.id,
                        "fact": info.fact,
                        "quote": info.quote,
                        "book": book_title,
                        "chapter": extraction.chapter_title,
                        "chunk_id": extraction.chunk_id,
                    }
    return lookup


def _rebuild_citations_from_text(
    text: str,
    fact_lookup: Dict[str, dict],
    chunk_map: Dict[str, Chapter],
) -> Tuple[List[Citation], List[str]]:
    """Parse [fact_id] tags from text and rebuild citations from extraction data.
    
    Returns (citations, missing_ids).
    - citations: List of Citation objects built from original extraction data
    - missing_ids: List of fact IDs referenced in text but not found in extractions (hallucinations)
    """
    # Extract all fact IDs from text
    fact_ids: set[str] = set()
    # Pattern allows hyphens in chunk IDs (e.g., Matter_chap-3#019)
    pattern = re.compile(r'\[([a-zA-Z0-9_\-]+#\d{3})\]', re.IGNORECASE)
    for match in pattern.finditer(text or ""):
        fact_ids.add(match.group(1).lower())
    
    citations: List[Citation] = []
    missing: List[str] = []
    
    for fact_id in sorted(fact_ids):
        if fact_id in fact_lookup:
            info = fact_lookup[fact_id]
            chunk_id = info["chunk_id"].lower()
            chapter = chunk_map.get(chunk_id)
            
            citations.append(Citation(
                id=fact_id,
                book=info["book"],
                chapter=info.get("chapter", ""),
                quote=info["quote"],
                chunk_id=chunk_id,
                source_file=chapter.source_file if chapter else None,
            ))
        else:
            missing.append(fact_id)
    
    return citations, missing


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


def _extract_fact_ids(text: str) -> set[str]:
    """Extract fact IDs from text like [excession_ch001#003] or [excession_ch001#001][excession_ch002#005]."""
    ids: set[str] = set()
    # Pattern matches fact IDs: word characters, underscores, hyphens, followed by # and digits
    fact_id_pattern = re.compile(r"\[([a-z0-9_-]+#\d+)\]", re.IGNORECASE)
    for match in fact_id_pattern.finditer(text or ""):
        ids.add(match.group(1).lower())  # Normalize to lowercase
    return ids


def _extract_citation_ids(text: str) -> set[str]:
    """Extract citation IDs from text. Supports both fact IDs and legacy integer IDs."""
    # Try fact ID format first
    fact_ids = _extract_fact_ids(text)
    if fact_ids:
        return fact_ids

    # Fall back to legacy integer format for backwards compatibility
    ids: set[str] = set()
    for match in CITATION_PATTERN.finditer(text or ""):
        numbers = [n.strip() for n in match.group(1).split(",") if n.strip()]
        for num in numbers:
            try:
                ids.add(str(int(num)))  # Convert to string for consistent typing
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
    """Count number of citation markers in text (both fact IDs and legacy integers)."""
    if not text:
        return 0
    # Count fact ID citations [chunk_id#seq]
    fact_id_count = len(re.findall(r"\[[a-z0-9_-]+#\d+\]", text, re.IGNORECASE))
    if fact_id_count > 0:
        return fact_id_count
    # Fall back to legacy integer citations [N]
    return len(CITATION_PATTERN.findall(text))


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
    # If it's a split file ID (excession_split_010), it might not match our stricter chXXX pattern
    match = CHUNK_ID_PATTERN.search(cleaned)
    if match:
        return match.group(1)
    # Fallback to just the lowercased segment before #
    return cleaned.split("#")[0]


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
    # Sort by chunk_id for chronological ordering
    sorted_pairs = sorted(pairs, key=lambda p: p[0].chunk_id.lower())
    lines: List[str] = []
    for extraction, ent in sorted_pairs:
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
            fact_id = info.get("id", "")
            if not _quote_in_text(quote, chapter.text):
                continue
            valid_infos.append(info)
            lines.append(
                f"  • [{fact_id}] Fact: {fact}\n    Quote: \"{quote}\"\n    Context: {context}"
            )
        if not valid_infos:
            lines.pop()
    return "\n".join(lines)


def build_synthesis_prompt(
    merged: MergedEntity, entity_type: str, formatted_extractions: str
) -> str:
    appears_in = ", ".join(merged.appears_in)
    aliases_str = ", ".join(merged.aliases) if merged.aliases else "None"
    
    # Count facts for reference (format is "• [fact_id] Fact:" so count "] Fact:")
    fact_count = formatted_extractions.count("] Fact:")

    return f"""{SYSTEM_PROMPT}

ENTITY DETAILS:
- Name: {merged.canonical_name}
- Also known as: {aliases_str}
- Type: {entity_type}
- Appears in: {appears_in}
- Facts provided: {fact_count} (you must use ALL of them)

EXTRACTED INFORMATION (use ALL of this):
{formatted_extractions}

Return a JSON object with this EXACT structure:
```json
{{
  "summary": "A compelling 2-3 sentence overview with [fact_id] citations...",
  "sections": [
    {{"title": "Section Title", "content": "Section content with [fact_id] citations..."}},
    ...
  ],
  "citations": [],
  "related_entities": ["Entity Name 1", "Entity Name 2", ...]
}}
```

IMPORTANT - CITATIONS ARE REBUILT BY PYTHON:
- Do NOT populate the citations array - leave it empty: []
- Just use [fact_id] tags inline in your text (e.g., [excession_split_010#003])
- Python will automatically rebuild the citations from source data
- This saves tokens and prevents errors

FIELD DETAILS:
- summary: 2-3 sentence overview with [fact_id] inline citations
- sections: 4-8 sections for major entities, each with title and content containing [fact_id] citations
- citations: LEAVE EMPTY - Python rebuilds these automatically
- related_entities: Names of other entities mentioned (for cross-linking)

CRITICAL:
- Use the EXACT fact IDs provided in brackets before each fact (e.g., [excession_split_010#003])
- Do NOT output citations - leave the array empty
- Output ONLY the JSON object, no markdown code fences or other text"""


def _build_chunk_context_map(book_slug: str = None) -> Dict[str, Chapter]:
    """Build a map of chunk_id -> Chapter for context lookup.
    
    Args:
        book_slug: If provided, only load chapters from this book (faster).
                   Special value "cross-book" loads ALL books.
    """
    chunk_map: Dict[str, Chapter] = {}
    if not config.BOOKS_DIR.exists():
        return chunk_map
    
    # Special case: "cross-book" means load from ALL books
    load_all = book_slug == "cross-book"
    
    for book_dir in config.BOOKS_DIR.iterdir():
        if not book_dir.is_dir() or book_dir.name.startswith("."):
            continue
        
        # Filter by book if specified (unless loading all for cross-book)
        if book_slug and not load_all:
            dir_slug = slugify(book_dir.name)
            target_slug = book_slug.lower().replace("_", "-")
            if dir_slug != target_slug:
                continue  # Skip this book
        
        try:
            chapters = load_chapters(book_dir)
        except Exception as exc:
            logger.warning("Failed to load chapters for %s: %s", book_dir.name, exc)
            continue
        for chapter in chapters:
            chunk_map[chapter.chunk_id] = chapter
            # Also index by source filename base (lowercased) to match Fact ID format
            source_file_base = chapter.source_file.split(".")[0].lower()
            chunk_map[source_file_base] = chapter
    return chunk_map


def synthesize_articles(
    entity_type: str, client: GeminiClient, force: bool = False, max_retries: int = None, book_slug: str = None, entity_name: str = None
) -> None:
    """Generate wiki articles for a specific entity type."""
    # Default max retries if not specified
    MAX_SYNTHESIS_RETRIES = max_retries if max_retries is not None else 10
    
    # Load from book-specific entity file if filtering by book
    if book_slug:
        entity_file = config.ENTITIES_DIR / f"{entity_type}_{book_slug}.json"
        if not entity_file.exists():
            logger.warning("No book-specific entity file found at %s, falling back to main file", entity_file)
            entity_file = config.ENTITIES_DIR / f"{entity_type}.json"
            merged_entities = _load_merged_entities(entity_type)
            # Filter entities to only those appearing in the book
            # Normalize: convert hyphens/underscores for consistent matching
            normalized_slug = book_slug.lower().replace("-", "_")
            merged_entities = [e for e in merged_entities if any(
                b.lower().replace("-", "_") == normalized_slug for b in e.appears_in
            )]
            logger.info("Filtered entities from %d to %d for book: %s", len(_load_merged_entities(entity_type)), len(merged_entities), book_slug)
        else:
            # Load from book-specific file
            from .utils import read_json
            data = read_json(entity_file)
            merged_entities = [MergedEntity(**e) for e in data.get("entities", [])]
            logger.info("Loaded %d entities from book-specific file: %s", len(merged_entities), entity_file)
    else:
        merged_entities = _load_merged_entities(entity_type)

    # Filter by entity name if provided
    if entity_name:
        entity_name_lower = entity_name.lower()
        filtered = []
        for e in merged_entities:
            # Check canonical name
            if e.canonical_name.lower() == entity_name_lower:
                filtered.append(e)
                continue
            # Check aliases
            if any(a.lower() == entity_name_lower for a in e.aliases):
                filtered.append(e)
                continue
        merged_entities = filtered
        logger.info("Filtered entities by name '%s': %d matched", entity_name, len(merged_entities))
    
    # Load extractions - filter by book if specified for proper isolation
    extractions = _load_extractions(entity_type, book_slug=book_slug)
    chunk_map = _build_chunk_context_map(book_slug=book_slug)
    fact_lookup = _build_fact_lookup(extractions)  # For Python citation rebuild
    output_dir = config.ARTICLES_DIR / entity_type
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_slugs = {slugify(ent.canonical_name) for ent in merged_entities}

    if force and book_slug:
        # Only remove stale articles that belong to this book
        for path in output_dir.glob("*.json"):
            if path.stem not in expected_slugs:
                try:
                    import json
                    with open(path) as f:
                        article_data = json.load(f)
                    article_books = article_data.get('appears_in', [])
                    # Only delete if article belongs to this book
                    if book_slug.lower().replace("-", "_") in [b.lower().replace("-", "_") for b in article_books]:
                        logger.info("Removing stale article %s (belongs to %s)", path.name, book_slug)
                        path.unlink()
                except Exception:
                    pass  # Skip if can't read article

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
        
        for attempt in range(1, MAX_SYNTHESIS_RETRIES + 1):
            try:
                # Use unique cache key per attempt to bypass cache on retries
                cache_key = f"{entity_type}:{slug}" if attempt == 1 else f"{entity_type}:{slug}:retry{attempt}"
                payload = client.synthesize(prompt, cache_key=cache_key)
                
                # === PYTHON CITATION REBUILD ===
                # Extract all text content (summary + sections)
                all_text = payload.get("summary", "")
                for section in payload.get("sections", []):
                    all_text += " " + section.get("content", "")
                
                # Rebuild citations from fact_lookup (not LLM output)
                citations, missing_ids = _rebuild_citations_from_text(
                    all_text, fact_lookup, chunk_map
                )
                
                # FAIL immediately if LLM used unknown fact IDs (hallucination)
                if missing_ids:
                    logger.error(
                        "HALLUCINATION DETECTED for %s: LLM used %d unknown fact IDs: %s",
                        merged.canonical_name,
                        len(missing_ids),
                        missing_ids[:5],  # Show first 5
                    )
                    if force and outfile.exists():
                        outfile.unlink()
                    return  # FAIL - do not retry, this is a hallucination

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
                # NOTE: Old validation for "referenced IDs subset of cited IDs" removed.
                # The Python rebuild step already validates that all fact IDs exist.
                # If any unknown fact IDs were used, we already failed above.

            # Check citation density - reject articles with too few citations
                if not _check_citation_density(article.summary, sections, merged.canonical_name):
                    if attempt < MAX_SYNTHESIS_RETRIES:
                        logger.warning(
                            "Low citation density for %s (attempt %d/%d). Retrying...",
                            merged.canonical_name,
                            attempt,
                            MAX_SYNTHESIS_RETRIES,
                        )
                        continue  # Retry
                    else:
                        logger.warning(
                            "Skipping article for %s due to low citation density after %d attempts",
                            merged.canonical_name,
                            MAX_SYNTHESIS_RETRIES,
                        )
                        if force and outfile.exists():
                            outfile.unlink()
                        return

                # Success! Write the article and exit retry loop
                write_json(article.model_dump(), outfile)
                logger.info("Synthesized article for %s", merged.canonical_name)
                break  # Exit retry loop on success
                
            except Exception as exc:
                if attempt < MAX_SYNTHESIS_RETRIES:
                    logger.warning(
                        "Failed to synthesize %s (attempt %d/%d): %s. Retrying...",
                        merged.canonical_name,
                        attempt,
                        MAX_SYNTHESIS_RETRIES,
                        exc,
                    )
                    continue
                logger.error("Failed to synthesize %s after %d attempts: %s", merged.canonical_name, MAX_SYNTHESIS_RETRIES, exc)
                # Continue to next entity instead of crashing

    if config.PARALLEL_WORKERS <= 1:
        for merged in pending:
            _synthesize_one(merged)
        return

    failed_count = 0
    with ThreadPoolExecutor(max_workers=config.PARALLEL_WORKERS) as executor:
        futures = {executor.submit(_synthesize_one, merged): merged for merged in pending}
        for future in tqdm(as_completed(futures), total=len(futures), desc=f"Synthesizing {entity_type}"):
            exc = future.exception()
            if exc:
                failed_count += 1
                logger.error(
                    "Synthesis worker failed for %s: %s",
                    futures[future].canonical_name,
                    exc,
                )
                # Continue to next entity instead of crashing
    
    if failed_count > 0:
        logger.warning("Synthesis completed with %d failures out of %d entities", failed_count, len(pending))
