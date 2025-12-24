"""Entity extraction stage."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Optional

from . import config
from .chunker import load_chapters
from .gemini_client import GeminiClient
from .models import Chapter, Entity, EntityExtraction
from .utils import ensure_dir, slugify, write_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert literary analyst extracting structured information from Iain M. Banks' Culture novels.

CRITICAL RULES:
1. Extract ONLY information explicitly stated in the text
2. Every fact MUST include a verbatim quote (word-for-word, copy-paste) as evidence
3. If something is implied but not stated, do not include it
4. When in doubt, include the entity with fewer facts rather than inventing details
5. Quotes should be 10-50 words—enough for context, not entire paragraphs"""

ENTITY_PROMPTS = {
    "characters": """Extract all CHARACTERS (people, beings, individuals) mentioned in this chapter.

Include:
- Named individuals (humans, aliens, drone personalities, Mind avatars)
- Unnamed but significant characters ("the captain", "the Chelgrian diplomat")
- Do NOT include ships/Minds themselves here—only their avatars if they appear as characters

For each character provide:
- name: Primary name used in this chapter
- aliases: Other names, titles, nicknames used (empty array if none)
- species: If mentioned (human, Chelgrian, Homomdan, Idirian, drone, avatar, etc.) or null
- affiliation: Culture, Special Circumstances, Elench, etc. if mentioned, or null
- information: Array of facts observed in THIS chapter:
  - fact: One specific observation (role, action, relationship, trait, appearance)
  - quote: EXACT verbatim text supporting this (10-50 words, copy-paste from passage)
  - location: Brief context ("dialogue", "narrator description", "internal monologue")

Return JSON: {"entities": [...]}
If no characters found: {"entities": []}""",
    "ships_and_minds": """Extract all SHIPS and MINDS mentioned in this chapter.

Include:
- Culture ships (GSVs, GCUs, ROUs, LCUs, etc.)
- Non-Culture vessels
- Minds (ship intelligences) when discussed as entities
- Orbitals' Hub Minds if named

For each ship/Mind provide:
- name: Full ship name (e.g., "Sleeper Service", "Grey Area", "Falling Outside The Normal Moral Constraints")
- aliases: Nicknames, shortened names (e.g., "Meatfucker" for Grey Area), or previous names
- class: Ship class if mentioned (GSV, GCU, ROU, LOU, Plate class, etc.) or null
- affiliation: Culture, Elench, Affront, etc. or null
- status: Active, destroyed, sublimed, eccentric, etc. if mentioned or null
- information: Array of facts:
  - fact: One observation (action, personality trait, capability, history)
  - quote: EXACT verbatim text (10-50 words)
  - location: Brief context

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
    "locations": """Extract all LOCATIONS mentioned in this chapter.

Include:
- Orbitals (name them specifically)
- Planets and moons
- Ships when used as settings (not as characters)
- Specific places within ships/Orbitals (bays, sections, habitats)
- Star systems, nebulae, regions of space
- Airspheres, Rocks, Shellworlds

For each location provide:
- name: Primary name
- aliases: Other names or designations
- type: Orbital, planet, GSV interior, Hub, airsphere, etc. or null
- affiliation: Who controls/inhabits it if mentioned
- information: Array of facts:
  - fact: One observation (description, what happens there, characteristics)
  - quote: EXACT verbatim text (10-50 words)
  - location: Context in narrative

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
    "technology": """Extract all TECHNOLOGY and DEVICES mentioned in this chapter.

Include:
- Weapons (knife missiles, effectors, gridfire, CAM, etc.)
- Tools and devices (terminals, gelfield, displacers, etc.)
- Implants and augmentations (neural lace, drug glands, etc.)
- Ship systems and capabilities
- Communication technology (tight-beam, hyperspace relays)
- Anything technological that gets described or used

For each technology provide:
- name: What it's called
- aliases: Other names or colloquial terms
- category: Weapon, implant, ship system, communication, tool, etc.
- information: Array of facts:
  - fact: What it does, how it works, what happens when used
  - quote: EXACT verbatim text (10-50 words)
  - location: Context

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
    "factions": """Extract all FACTIONS, CIVILIZATIONS, and ORGANIZATIONS mentioned in this chapter.

Include:
- The Culture and its divisions (Contact, Special Circumstances, etc.)
- Other civilizations (Affront, Chelgrians, Idirans, Homomda, Elench, etc.)
- Political groups, religious organizations
- Informal groups ("the Interesting Times Gang")
- Equiv-tech civs, hegemonizing swarms, sublimed entities

For each faction provide:
- name: Primary name
- aliases: Other names or shortened versions
- type: Civilization, division, organization, informal group, etc.
- information: Array of facts:
  - fact: Characteristic, belief, action, relationship to other factions
  - quote: EXACT verbatim text (10-50 words)
  - location: Context

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
    "events": """Extract all EVENTS and INCIDENTS mentioned in this chapter.

Include:
- Current events happening in the narrative
- Historical events referenced (Idiran War, etc.)
- Battles, missions, diplomatic incidents
- Personal events (deaths, meetings, discoveries)
- Political events (treaties, conflicts, first contacts)

For each event provide:
- name: Descriptive name or title if given
- aliases: Other ways it's referred to
- timeframe: When it occurred relative to narrative (current, "300 years ago", during Idiran War) if mentioned
- participants: Who was involved if mentioned
- information: Array of facts:
  - fact: What happened, outcome, significance
  - quote: EXACT verbatim text (10-50 words)
  - location: Context in narrative

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
}


def _resolve_book_dir(book: str) -> Path:
    book_path = Path(book)
    if book_path.exists():
        return book_path

    candidates = [
        config.BOOKS_DIR / book,
        config.BOOKS_DIR / slugify(book),
    ]
    for path in candidates:
        if path.exists():
            return path

    slug = slugify(book)
    for path in config.BOOKS_DIR.iterdir():
        if path.is_dir() and slugify(path.name) == slug:
            return path

    raise FileNotFoundError(
        f"Book directory not found for '{book}'. Looked in {candidates}"
    )


def _load_chunks(book: str) -> List[Chapter]:
    book_dir = _resolve_book_dir(book)
    return load_chapters(book_dir)


def build_extraction_prompt(entity_type: str, chunk: Chapter) -> str:
    entity_prompt = ENTITY_PROMPTS.get(entity_type)
    if not entity_prompt:
        raise ValueError(f"Unknown entity type: {entity_type}")

    return f"""{SYSTEM_PROMPT}

{entity_prompt}

---
CHAPTER: {chunk.title}
BOOK: {chunk.book_title}
---

{chunk.text}

---
Extract all {entity_type} from the chapter above. Return valid JSON only."""


def _write_extraction(
    chunk: Chapter, entity_type: str, output_dir: Path, payload: dict
) -> None:
    entities = [Entity(**e) for e in payload.get("entities", [])]
    extraction = EntityExtraction(
        chunk_id=chunk.chunk_id,
        chapter_title=chunk.title,
        entity_type=entity_type,
        entities=entities,
    )
    output_path = output_dir / f"{chunk.chunk_id}_{entity_type}.json"
    write_json(extraction.model_dump(), output_path)


def extract_entities_for_book(
    book: str,
    client: Optional[GeminiClient],
    entity_types: Optional[Iterable[str]] = None,
    force: bool = False,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> None:
    """Run extraction for all chunks in a book."""
    book_slug = slugify(book)
    entity_types = list(entity_types or config.ENTITY_TYPES)
    chunks = _load_chunks(book)
    if limit is not None:
        chunks = chunks[:limit]
    if not chunks:
        logger.info("No chapters found for %s; skipping extraction", book_slug)
        return

    if dry_run:
        if not chunks:
            logger.info("No chapters available for dry run in %s", book_slug)
            return
        if not entity_types:
            logger.info("No entity types configured for dry run")
            return
        first_chunk = chunks[0]
        first_type = entity_types[0]
        prompt = build_extraction_prompt(first_type, first_chunk)
        print("---- DRY RUN PROMPT ----")
        print(prompt)
        print("---- END PROMPT ----")
        return
    if client is None:
        raise RuntimeError("Gemini client is required for extraction unless --dry-run is used.")

    def _extract_one(entity_type: str, chunk: Chapter) -> None:
        output_dir = ensure_dir(config.EXTRACTIONS_DIR / entity_type / book_slug)
        outfile = output_dir / f"{chunk.chunk_id}_{entity_type}.json"
        if outfile.exists() and not force:
            logger.info(
                "Skipping %s (%s) — already extracted", chunk.chunk_id, entity_type
            )
            return

        prompt = build_extraction_prompt(entity_type, chunk)
        cache_key = f"{chunk.chunk_id}:{entity_type}"
        try:
            payload = client.extract(prompt, cache_key=cache_key)
            _write_extraction(chunk, entity_type, output_dir, payload)
            logger.info(
                "Extracted %s for %s (%s)", entity_type, chunk.chunk_id, chunk.title
            )
        except ValueError as exc:
            logger.error(
                "Malformed JSON for %s (%s); skipping. Error: %s",
                chunk.chunk_id,
                entity_type,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Extraction failed for %s (%s): %s", chunk.chunk_id, entity_type, exc
            )
            if force:
                raise

    tasks = [(entity_type, chunk) for entity_type in entity_types for chunk in chunks]
    if config.PARALLEL_WORKERS <= 1:
        for entity_type, chunk in tasks:
            _extract_one(entity_type, chunk)
        return

    with ThreadPoolExecutor(max_workers=config.PARALLEL_WORKERS) as executor:
        futures = {
            executor.submit(_extract_one, entity_type, chunk): (entity_type, chunk)
            for entity_type, chunk in tasks
        }
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                entity_type, chunk = futures[future]
                logger.error(
                    "Extraction worker failed for %s (%s): %s",
                    chunk.chunk_id,
                    entity_type,
                    exc,
                )
                if force:
                    raise exc
