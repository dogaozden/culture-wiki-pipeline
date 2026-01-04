"""Entity extraction stage."""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
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

QUOTE REQUIREMENTS:
- Copy the EXACT text from the passage, word-for-word
- Include COMPLETE SENTENCES (not fragments)
- 20-100 words per quote — enough for full context
- Quotes are your EVIDENCE — if you can't find a direct quote, don't include the fact
- Prefer quotes that are specific and informative over generic descriptions"""

ENTITY_PROMPTS = {
    "characters": """Extract all CHARACTERS (people, beings, individuals) mentioned in this chapter.

Include:
- Named individuals (humans, aliens, drone personalities, Mind avatars)
- Unnamed but significant characters ("the captain", "the Chelgrian diplomat")
- Do NOT include ships/Minds themselves here—only their avatars if they appear as characters

For each character provide:
- name: Primary name used in this chapter
- aliases: Other names/titles this SAME character is CALLED in the text (empty array if none)

  STRICT ALIAS RULES - Only include if:
  ✓ The text explicitly says "X, also known as Y" or "X, called Y"
  ✓ It's a shortened/formal form of the same name ("Dajeil" for "Dajeil Gelian")
  ✓ It's a title/rank used for THIS person ("Ms Gelian", "Commander")
  
  NEVER include as aliases:
  ✗ Descriptive phrases ("the woman", "an old flame", "the human")
  ✗ Pronouns (he, she, they)
  ✗ Narrator descriptions or epithets
  ✗ Other characters who appear in the same scene
  ✗ Ships/Minds (even if the character is associated with them)

- species: If mentioned (human, Chelgrian, Homomdan, Idirian, drone, avatar, etc.) or null
- affiliation: Culture, Special Circumstances, Elench, etc. if mentioned, or null
- prominence: How significant is this character IN THIS CHAPTER?
  - "major": Central to the chapter, has dialogue/action, POV character
  - "supporting": Appears and interacts but not central
  - "mentioned": Only referenced/talked about, doesn't appear directly
- prominence_score: Numeric score 1-10 for this chapter (10=POV character, 7-9=major, 4-6=supporting, 1-3=mentioned)

- relationships: Array of connections to OTHER entities observed in THIS chapter (empty if none)
  - target: Name of the other entity (character, ship, faction, etc.)
  - type: Nature of relationship ("serves on", "loves", "works for", "enemy of", "child of", etc.)
  - strength: Intensity/importance of relationship 1-10 (10=central conflict/love, 5=working relationship, 1=acquaintance)
  - quote: EXACT text evidence (20-50 words)

- information: Extract ALL notable facts about this character in THIS chapter.
  Be thorough — extract every distinct piece of information.

  Look for facts about:
  * Physical appearance and description
  * Actions taken and behaviors
  * Dialogue and statements made
  * Relationships revealed or implied
  * Backstory and history
  * Emotions, reactions, and internal states
  * Abilities, skills, or roles
  * Possessions or associated objects
  
  For each fact:
  - fact: One specific observation
  - quote: EXACT verbatim text supporting this (20-100 words, complete sentences)
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
- name: Full ship name (e.g., "Sleeper Service", "Grey Area")
- aliases: Other names this SAME ship is called in the text (empty array if none)
  
  STRICT ALIAS RULES - Only include if:
  ✓ The text explicitly says "X, also known as Y" or "X (nicknamed Y)"
  ✓ It's a shortened form of the same name ("the Sleeper" for "Sleeper Service")
  ✓ It's a previous/alternate official name for THIS ship
  
  NEVER include as aliases:
  ✗ Avatar names (avatars are SEPARATE entities from ships!)
  ✗ Generic references ("the ship", "the vessel", "the GSV")
  ✗ Other ships that appear in the same scene
  ✗ Related but DIFFERENT entities
  
  Example: Grey Area's alias is "Meatfucker" (same ship)
  Counter-example: "Amorphia" is the Sleeper Service's AVATAR, NOT an alias of the ship

- class: Ship class if mentioned (GSV, GCU, ROU, LOU, Plate class, etc.) or null
- affiliation: Culture, Elench, Affront, etc. or null
- status: Active, destroyed, sublimed, eccentric, etc. if mentioned or null
- prominence: How significant is this ship IN THIS CHAPTER?
  - "major": Central to events, has extensive description or communication
  - "supporting": Appears/acts but not central
  - "mentioned": Only referenced by name or in passing
- prominence_score: Numeric score 1-10 for this chapter (10=main POV ship, 7-9=major, 4-6=supporting, 1-3=mentioned)

- relationships: Array of connections to OTHER entities in THIS chapter (empty if none)
  - target: Name of the other entity
  - type: "carries", "communicates with", "allied with", "attacks", "part of fleet", etc.
  - strength: Intensity 1-10 (10=central conflict, 5=regular interaction, 1=passing mention)
  - quote: EXACT text evidence (20-50 words)

- information: Extract ALL notable facts about this ship/Mind in THIS chapter.
  Be thorough — extract every distinct piece of information.
  
  Look for facts about:
  * Physical description and size
  * Capabilities and weapons
  * Personality and quirks
  * Actions taken and decisions made
  * Communications and dialogue
  * History and backstory
  * Current mission or purpose
  * Crew, passengers, or inhabitants
  
  For each fact:
  - fact: One specific observation
  - quote: EXACT verbatim text (20-100 words, complete sentences)
  - location: Brief context

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
    "locations": """Extract all LOCATIONS mentioned in this chapter.

Include:
- Orbitals (name them specifically)
- Planets and moons
- Ships when used as SETTINGS (where events happen, not as characters)
- Specific NAMED places within ships/Orbitals (bays, towers, habitats)
- Star systems, nebulae, regions of space
- Airspheres, Rocks, Shellworlds

Do NOT extract generic/unnamed locations like "the corridor", "a room", "the bay".

For each location provide:
- name: Primary name (must be a proper name, not a generic description)
- aliases: Other names this SAME location is CALLED in the text (empty array if none)

  STRICT ALIAS RULES - Only include if:
  ✓ The text explicitly identifies it as an alternate name
  ✓ It's an abbreviated form of the same place
  
  NEVER include as aliases:
  ✗ Generic descriptions ("the tower", "the orbital")
  ✗ Different locations that are nearby
  ✗ Ship names when the ship is just docked there

- type: Orbital, planet, GSV interior, Hub, airsphere, habitat, etc. or null
- affiliation: Who controls/inhabits it if mentioned
- prominence: How significant is this location IN THIS CHAPTER?
  - "major": Primary setting, extensively described
  - "supporting": Scene takes place here briefly
  - "mentioned": Only referenced, not a setting

- information: Extract ALL notable facts about this location in THIS chapter.
  Be thorough — extract every distinct piece of information.
  
  Look for facts about:
  * Physical description and atmosphere
  * Size and scale
  * Inhabitants and population
  * Events that occur there
  * History and significance
  * Unique features or landmarks
  
  For each fact:
  - fact: One specific observation
  - quote: EXACT verbatim text (20-100 words, complete sentences)
  - location: Context in narrative

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
    "technology": """Extract NAMED or SIGNIFICANT TECHNOLOGY mentioned in this chapter.

Include:
- Named weapons (knife missiles, effectors, gridfire, CAM, Lazy guns, etc.)
- Specific devices with names or unique description
- Implants and augmentations (neural lace, drug glands, etc.)
- Named ship systems or capabilities
- Communication tech (tight-beam, hyperspace relays)

Do NOT extract:
- Generic technology ("a weapon", "the computer", "a tool")
- Everyday items unless they have Culture-specific names
- Technology only mentioned in passing without description

For each technology provide:
- name: The specific name used (must be a proper name or Culture-specific term)
- aliases: Other names this SAME technology is CALLED in the text (empty array if none)

  STRICT ALIAS RULES - Only include if:
  ✓ The text explicitly identifies it as an alternate name
  ✓ It's an abbreviated form of the same tech
  
  NEVER include as aliases:
  ✗ Generic descriptions ("the device", "the weapon")
  ✗ Different but related technologies

- category: Weapon, implant, ship system, communication, tool, etc.
- prominence: How significant is this tech IN THIS CHAPTER?
  - "major": Extensively described or central to plot
  - "supporting": Used or mentioned with some detail
  - "mentioned": Only named in passing

- information: Extract ALL notable facts about this technology in THIS chapter.
  Be thorough — extract every distinct piece of information.
  
  Look for facts about:
  * How it works and operates
  * Effects and capabilities
  * Usage and applications
  * Limitations or drawbacks
  * Who uses it and how
  
  For each fact:
  - fact: One specific observation
  - quote: EXACT verbatim text (20-100 words, complete sentences)
  - location: Context

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
    "factions": """Extract all FACTIONS, CIVILIZATIONS, and ORGANIZATIONS mentioned in this chapter.

Include:
- The Culture and its divisions (Contact, Special Circumstances, etc. — ONLY if explicitly mentioned in THIS text)
- Other civilizations (Affront, Chelgrians, Idirans, Homomda, Elench, etc. — ONLY if explicitly mentioned in THIS text)
- Political groups, religious organizations (ONLY if explicitly mentioned in THIS text)
- Informal groups (e.g. "the Interesting Times Gang" — ONLY if explicitly mentioned in THIS text)
- Equiv-tech civs, hegemonizing swarms, sublimed entities (ONLY if explicitly mentioned in THIS text)

For each faction provide:
- name: Primary name
- aliases: Other names this SAME faction is CALLED in the text (empty array if none)

  STRICT ALIAS RULES - Only include if:
  ✓ The text explicitly identifies it as an alternate name
  ✓ It's an abbreviated or formal variant
  
  NEVER include as aliases:
  ✗ Subgroups (Special Circumstances is NOT an alias of The Culture)
  ✗ Member names or ships belonging to the faction
  ✗ Generic descriptions ("the aliens", "the enemy")

- type: Civilization, division, organization, informal group, etc.
- prominence: How significant is this faction IN THIS CHAPTER?
  - "major": Central to plot, extensively discussed
  - "supporting": Involved but not central
  - "mentioned": Only referenced in passing
- prominence_score: Numeric score 1-10 for this chapter (10=central faction, 7-9=major, 4-6=supporting, 1-3=mentioned)

- relationships: Array of connections to OTHER factions in THIS chapter (empty if none)
  - target: Name of the other faction
  - type: "allied with", "at war with", "part of", "opposes", etc.
  - strength: Intensity 1-10 (10=open war/deep alliance, 5=diplomatic tension, 1=passing mention)
  - quote: EXACT text evidence (20-50 words)

- information: Extract ALL notable facts about this faction in THIS chapter.
  Be thorough — extract every distinct piece of information.
  
  Look for facts about:
  * Beliefs and ideology
  * Actions and decisions
  * Relationships with other factions
  * Internal structure and leadership
  * History and origins
  * Goals and motivations
  
  For each fact:
  - fact: One specific observation
  - quote: EXACT verbatim text (20-100 words, complete sentences)
  - location: Context

Return JSON: {"entities": [...]}
If none found: {"entities": []}""",
    "events": """Extract NAMED EVENTS and SIGNIFICANT HISTORICAL INCIDENTS mentioned in this chapter.

CRITICAL: Events are OCCURRENCES, not entities. Do NOT extract:
- Characters (people, beings, Minds, drones) — use "characters" extraction for those
- Ships/vessels — use "ships_and_minds" extraction for those
- Locations/places — use "locations" extraction for those
- Factions/organizations — use "factions" extraction for those
- Technology/devices — use "technology" extraction for those

Events ARE things that HAPPENED. Look for:
- Named wars or conflicts (e.g. "Idiran War", "Twin Novae Battle" — ONLY if the name appears in THIS text)
- Named missions or operations with specific names
- Historical incidents given titles (e.g. "The Subliming" — ONLY if explicitly named in THIS text)
- Specific battles or encounters with identifiable names
- Major plot events that characters refer to by name

Do NOT extract:
- Character actions ("Genar-Hofoen's arrival" — that's about a character)
- Ship communications ("GSV message" — that's about a ship)
- Location descriptions ("The tower's construction" — that's about a location)
- Generic occurrences ("a meeting", "a battle", "a conversation")
- Vague references without a specific named event

For each event provide:
- name: The event's proper name (e.g., "Idiran War", "Battle of Boustrago")
  MUST be a named event, not a description of what a character/ship did
- aliases: Other names this SAME event is CALLED in the text (empty array if none)

  STRICT ALIAS RULES - Only include if:
  ✓ The text explicitly identifies it as an alternate name for the same event
  
  NEVER include as aliases:
  ✗ Related but different events
  ✗ Participants in the event
  ✗ Generic descriptions

- timeframe: When it occurred relative to narrative (current, "300 years ago", during Idiran War) if mentioned, or null
- participants: Key entities involved if mentioned (array of names), or null
- prominence: How significant is this event IN THIS CHAPTER?
  - "major": Central to the chapter's plot, extensively discussed
  - "supporting": Discussed with some context
  - "mentioned": Only referenced briefly by name

- information: Extract ALL notable facts about this event in THIS chapter.
  Be thorough — extract every distinct piece of information.
  
  Look for facts about:
  * What happened and how
  * Causes and triggers
  * Outcomes and consequences
  * Who was involved
  * Significance and impact
  
  For each fact:
  - fact: One specific observation about the EVENT itself
  - quote: EXACT verbatim text (20-100 words, complete sentences)
  - location: Context in narrative

Return JSON: {"entities": [...]}
If no NAMED EVENTS found: {"entities": []}""",
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


def _normalize_for_match(text: str) -> str:
    """Normalize text for quote matching."""
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
    """Check if quote exists in source text."""
    if not quote or not text:
        return False
    return _normalize_for_match(quote) in _normalize_for_match(text)


# Minimum similarity ratio for fuzzy quote repair (0.0 to 1.0)
# Minimum similarity ratio for fuzzy quote repair (0.0 to 1.0)
QUOTE_REPAIR_THRESHOLD = 0.82


def _find_best_match(query: str, source: str, min_ratio: float = QUOTE_REPAIR_THRESHOLD) -> tuple[str | None, float]:
    """Find the best matching substring for a query in source text.
    
    Optimized: Uses targeted search near occurrences of the first few words + early exit.
    Returns (matched_text, similarity_ratio) if found above threshold.
    """
    query_norm = _normalize_for_match(query)
    source_norm = _normalize_for_match(source)
    query_words = query_norm.split()
    source_words = source_norm.split()
    
    if len(query_words) < 5:
        return None, 0.0  # Too short to match reliably
    
    best_match_words = None
    best_ratio = 0.0
    best_start = 0
    best_end = 0
    
    # Heuristic: Identify candidate start positions
    # Look for occurrences of ANY of the first 3 significant words to be robust against start-of-quote errors
    candidate_starts = set()
    
    # Try first 3 words (skipping very short ones unless they're all we have)
    check_words = []
    for w in query_words[:3]:
        if len(w) > 3:
            check_words.append(w.lower())
    
    # If no long words in first 3, just use the first word
    if not check_words:
        check_words.append(query_words[0].lower())
        
    for check_word in check_words:
        # Simple optimization: check first 4 chars
        check_prefix = check_word[:4]
        for i, w in enumerate(source_words):
            if w.lower().startswith(check_prefix):
                # If we matched the 2nd or 3rd word, offset start index back
                # This is approximate, but helps find the true start
                candidate_starts.add(max(0, i - 2)) 
                candidate_starts.add(i)

    # Sort and convert to list
    sorted_candidates = sorted(list(candidate_starts))
    
    # If no candidate starts found, or list is empty, fall back to dense sampling
    if not sorted_candidates:
        # Fallback: Check every 10th word (denser than previous 20)
        sorted_candidates = list(range(0, len(source_words), 10))
    
    # Window sizes to try
    window_sizes = [
        max(5, len(query_words) - 5),
        len(query_words),
        len(query_words) + 5,
    ]
    
    for window_size in window_sizes:
        if window_size < 5 or window_size > len(source_words):
            continue
        for start in sorted_candidates:
            # Check a small range around the candidate start
            for offset in [-3, -2, -1, 0, 1, 2, 3]:
                i = start + offset
                if i < 0 or i + window_size > len(source_words):
                    continue
                candidate = " ".join(source_words[i:i + window_size])
                
                # Turn off auto-junk heuristic for speed
                matcher = SequenceMatcher(None, query_norm, candidate, autojunk=False)
                
                # Quick check
                if matcher.quick_ratio() < min_ratio:
                    continue
                    
                ratio = matcher.ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = i
                    best_end = i + window_size
                    best_match_words = source_words[best_start:best_end]
                    
                    if best_ratio >= 0.95:
                        break
            if best_ratio >= 0.95:
                break
        if best_ratio >= 0.95:
            break
    
    if best_ratio >= min_ratio and best_match_words:
        original_words = source.split()
        if best_end <= len(original_words):
            matched_text = " ".join(original_words[best_start:best_end])
            return matched_text, best_ratio
    
    return None, 0.0


def _write_extraction(
    chunk: Chapter, entity_type: str, output_dir: Path, payload: dict
) -> None:
    """Validate quotes and assign fact IDs before writing extraction."""
    fact_seq = 1
    validated_entities = []
    
    for ent_data in payload.get("entities", []):
        # ===================================================================
        # ENTITY NAME VERIFICATION: Reject entities not mentioned in source
        # ===================================================================
        entity_name = ent_data.get("name", "").strip()
        if not entity_name:
            continue  # Skip nameless entities
        
        # Check if entity name appears in source text (case-insensitive)
        if not _normalize_for_match(entity_name.lower()) in _normalize_for_match(chunk.text.lower()):
            logger.warning(
                "Hallucinated entity in %s (%s): '%s' not found in source text — skipping",
                chunk.chunk_id,
                entity_type,
                entity_name,
            )
            continue  # Skip this entire entity
        
        validated_infos = []
        
        for info in ent_data.get("information", []):
            quote = info.get("quote", "").strip()
            
            # Extraction-time validation: reject hallucinated quotes
            if not _quote_in_text(quote, chunk.text):
                # Attempt fuzzy repair before rejecting
                repaired_quote, similarity = _find_best_match(quote, chunk.text)
                if repaired_quote:
                    logger.info(
                        "Repaired quote in %s (%s) for entity '%s' (%.0f%% match): %.50s...",
                        chunk.chunk_id,
                        entity_type,
                        ent_data.get("name", "Unknown"),
                        similarity * 100,
                        repaired_quote,
                    )
                    info["quote"] = repaired_quote
                else:
                    logger.warning(
                        "Hallucinated quote in %s (%s) for entity '%s': %.50s...",
                        chunk.chunk_id,
                        entity_type,
                        ent_data.get("name", "Unknown"),
                        quote,
                    )
                    continue  # Skip this fact
            
            # Assign fact ID using source filename for easy verification: {source_file}#{seq}
            # e.g., "Excession_split_006#003" maps to books/excession/Excession_split_006.md
            info["id"] = f"{chunk.chunk_id}#{fact_seq:03d}"
            fact_seq += 1
            validated_infos.append(info)
        
        # Update entity with validated facts
        ent_data["information"] = validated_infos
        
        # ===================================================================
        # ALIAS VERIFICATION: Remove hallucinated aliases not in source text
        # ===================================================================
        original_aliases = ent_data.get("aliases", [])
        validated_aliases = []
        
        for alias in original_aliases:
            if not alias:
                continue
            # Check if alias appears in source text (case-insensitive)
            if _normalize_for_match(alias.lower()) in _normalize_for_match(chunk.text.lower()):
                validated_aliases.append(alias)
            else:
                logger.warning(
                    "Hallucinated alias in %s (%s) for entity '%s': '%s' not found in source text",
                    chunk.chunk_id,
                    entity_type,
                    ent_data.get("name", "Unknown"),
                    alias,
                )
        
        ent_data["aliases"] = validated_aliases
        validated_entities.append(ent_data)
    
    # Build extraction with validated entities
    entities = [Entity(**e) for e in validated_entities]
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
        for future in tqdm(as_completed(futures), total=len(futures), desc="Extracting"):
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
