"""Merge and deduplicate entities across books."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

from . import config
from .models import EntityExtraction, MergedEntity
from .utils import read_json, slugify, write_json
from .gemini_client import GeminiClient
from tqdm import tqdm

logger = logging.getLogger(__name__)

# Patterns for filtering out non-name aliases (hallucinated descriptive phrases)
# These are common patterns that LLMs extract as "aliases" but aren't proper names
ALIAS_BLACKLIST_PATTERNS = [
    # Articles + generic nouns
    r'^(a|an|the)\s+',           # "the woman", "an old flame"
    r'^(this|that)\s+',          # "this human", "that drone"
    # Pronouns
    r'^(he|she|it|they|him|her|them|his|hers|its|their)$',
    r'^(he/she|s?he)$',
    # Generic role descriptions
    r'^(old|young|new)\s+',      # "old perpetually pregnant"
    # Possessive references
    r'^(my|your|our|her|his|their)\s+',  # "her man", "your girl"
]

# Specific known bad aliases to always filter
ALIAS_BLACKLIST_EXACT = {
    # Generic descriptors
    'an old flame', 'the woman', 'the man', 'the human', 'the drone',
    'the avatar', 'the creature', 'the emissary', 'the bird', 'the machine',
    'old perpetually pregnant', 'the other woman', 'lady', 'mistress',
    'boy', 'child', 'arse-hole', 'imbecile', 'moron', 'wretch', 'spy',
    'he', 'she', 'it', 'they', 'he/she', 'her man', 'your girl', 'our lass',
    'the bad guy', 'the girl', 'this human', 'young lady', 'representative',
    # Generic ship terms (cause false merges)
    'the ship', 'ship', 'the gsv', 'the gcu', 'the rou', 'the vessel',
    'the craft', 'the module', 'the mind', 'its mind',
    # Avatar names (NOT ship aliases - cause cross-entity merges!)
    'amorphia',  # Sleeper Service's avatar, NOT equal to Jaundiced Outlook
}

import re
_ALIAS_PATTERNS = [re.compile(p, re.IGNORECASE) for p in ALIAS_BLACKLIST_PATTERNS]


def _is_valid_alias(alias: str, entity_type: str = None) -> bool:
    """Check if an alias is a proper name vs a descriptive phrase."""
    if not alias or not alias.strip():
        return False
    normalized = alias.strip().lower()
    # Check exact blacklist
    if normalized in ALIAS_BLACKLIST_EXACT:
        return False
    # Check patterns
    for pattern in _ALIAS_PATTERNS:
        if pattern.search(normalized):
            return False
    # Very short aliases are often bad (single letters, pronouns)
    if len(normalized) < 2:
        return False
    # Filter ship prefixes from non-ship entity types
    if entity_type and entity_type != "ships_and_minds":
        if _SHIP_FILTER_PATTERN.match(alias.strip()):
            return False
    return True


def _filter_aliases(aliases: List[str], entity_type: str = None) -> List[str]:
    """Filter out hallucinated/descriptive aliases, keeping only proper names."""
    return [a for a in aliases if _is_valid_alias(a, entity_type)]


def _load_extractions(entity_type: str) -> List[EntityExtraction]:
    base_dir = config.EXTRACTIONS_DIR / entity_type
    if not base_dir.exists():
        logger.warning("No extractions found for %s", entity_type)
        return []

    extractions: List[EntityExtraction] = []
    for book_dir in base_dir.iterdir():
        if not book_dir.is_dir():
            continue
        for path in sorted(book_dir.glob("*.json")):
            extractions.append(EntityExtraction(**read_json(path)))
    return extractions


def _find_existing_key(
    merged: Dict[str, MergedEntity], candidate_names: List[str]
) -> str | None:
    for name in candidate_names:
        key = slugify(name)
        if key in merged:
            return key
        # Also try with class prefix stripped
        stripped = _strip_class_prefix(name)
        if stripped != name:
            stripped_key = slugify(stripped)
            if stripped_key in merged:
                return stripped_key
    return None


# Common Culture ship class prefixes to strip for matching
SHIP_CLASS_PREFIXES = [
    'GCU', 'GSV', 'GCV', 'ROU', 'LOU', 'LCU', 'MSV', 'VFP', 'OU', 'GOU',
    'Plate class', 'Rapid Offensive Unit', 'General Contact Unit',
    'General Systems Vehicle', 'Limited Offensive Unit', 'Offensive Unit',
]

def _strip_class_prefix(name: str) -> str:
    """Strip ship class prefixes for better matching."""
    for prefix in SHIP_CLASS_PREFIXES:
        if name.upper().startswith(prefix.upper() + ' '):
            return name[len(prefix):].strip()
        # Also handle "The GCU X" pattern
        if name.upper().startswith('THE ' + prefix.upper() + ' '):
            return name[len('THE ') + len(prefix):].strip()
    return name


def _name_keys(entity: MergedEntity) -> set[str]:
    """Generate all matching keys for an entity (including stripped prefixes)."""
    keys = {slugify(entity.canonical_name)}
    keys.update(slugify(alias) for alias in entity.aliases)
    
    # Also add stripped versions
    stripped_canonical = _strip_class_prefix(entity.canonical_name)
    if stripped_canonical != entity.canonical_name:
        keys.add(slugify(stripped_canonical))
    
    for alias in entity.aliases:
        stripped = _strip_class_prefix(alias)
        if stripped != alias:
            keys.add(slugify(stripped))
    
    return {k for k in keys if k}


# Ship prefixes for filtering ships that leak into characters
_SHIP_FILTER_PREFIXES = [
    "GSV", "GCU", "GCV", "ROU", "LCU", "GOU", "LOU", "MSV", "VFP",
    "LSV", "dROU", "fROU", "oROU", "OU", "Eccentric", "ex-"
]
_SHIP_FILTER_PATTERN = re.compile(
    r'^(' + '|'.join(re.escape(p) for p in _SHIP_FILTER_PREFIXES) + r')\s+',
    re.IGNORECASE
)


def _filter_ships_from_characters(merged: Dict[str, MergedEntity]) -> Dict[str, MergedEntity]:
    """Remove ship entities that leaked into characters during extraction."""
    filtered = {}
    removed_count = 0
    for key, entity in merged.items():
        if _SHIP_FILTER_PATTERN.match(entity.canonical_name.strip()):
            logger.debug("Filtering ship from characters: %s", entity.canonical_name)
            removed_count += 1
        else:
            filtered[key] = entity
    if removed_count > 0:
        logger.info("Filtered %d ships from characters", removed_count)
    return filtered


def _load_known_ship_names() -> set[str]:
    """Load all ship names from ships_and_minds for cross-reference filtering."""
    ship_file = config.ENTITIES_DIR / "ships_and_minds.json"
    if not ship_file.exists():
        return set()
    
    data = read_json(ship_file)
    ship_names: set[str] = set()
    for ent in data.get("entities", []):
        ship_names.add(slugify(ent.get("canonical_name", "")))
        for alias in ent.get("aliases", []):
            ship_names.add(slugify(alias))
    return ship_names


def _filter_known_ships(merged: Dict[str, MergedEntity], entity_type: str) -> Dict[str, MergedEntity]:
    """Remove entities that match known ship names from ships_and_minds."""
    known_ship_slugs = _load_known_ship_names()
    if not known_ship_slugs:
        return merged
    
    filtered = {}
    removed_count = 0
    for key, entity in merged.items():
        name = entity.canonical_name.strip()
        slug = slugify(name)
        
        # Check if canonical name matches a known ship
        if slug in known_ship_slugs:
            logger.debug("Filtering known ship from %s: %s", entity_type, name)
            removed_count += 1
            continue
        
        # Strip "The " prefix and check again (e.g., "The Grey Area" -> "Grey Area")
        name_no_the = name
        if name.lower().startswith("the "):
            name_no_the = name[4:]
            slug_no_the = slugify(name_no_the)
            if slug_no_the in known_ship_slugs:
                logger.debug("Filtering ship (stripped 'The') from %s: %s", entity_type, name)
                removed_count += 1
                continue
        
        # Check if any known ship name is contained in this entity name
        # (catches "Sleeper's three warships", "xGSV Anticipation...", etc.)
        name_lower = name.lower()
        is_ship_variant = False
        for ship_slug in known_ship_slugs:
            # Skip very short slugs to avoid false positives
            if len(ship_slug) < 5:
                continue
            # Check if ship name is in entity name
            if ship_slug.replace("-", " ") in name_lower.replace("'s", "").replace("'", ""):
                logger.debug("Filtering ship variant from %s: %s (contains '%s')", entity_type, name, ship_slug)
                is_ship_variant = True
                break
        if is_ship_variant:
            removed_count += 1
            continue
        
        # Also check by ship prefix pattern (GSV, GCU, xGSV, etc.)
        if _SHIP_FILTER_PATTERN.match(name) or name.lower().startswith("xgsv "):
            logger.debug("Filtering ship prefix from %s: %s", entity_type, name)
            removed_count += 1
            continue
        
        filtered[key] = entity
    
    if removed_count > 0:
        logger.info("Filtered %d known ships from %s", removed_count, entity_type)
    return filtered


def _filter_entities_from_events(merged: Dict[str, MergedEntity]) -> Dict[str, MergedEntity]:
    """Remove character/ship/location names that leaked into events during extraction."""
    # Load known entities from other types to check against
    known_entity_slugs: set[str] = set()
    for entity_type in ["characters", "ships_and_minds", "locations", "factions", "technology"]:
        entity_file = config.ENTITIES_DIR / f"{entity_type}.json"
        if entity_file.exists():
            data = read_json(entity_file)
            for ent in data.get("entities", []):
                known_entity_slugs.add(slugify(ent.get("canonical_name", "")))
                for alias in ent.get("aliases", []):
                    known_entity_slugs.add(slugify(alias))
    
    # Also filter by ship prefixes
    filtered = {}
    removed_count = 0
    for key, entity in merged.items():
        name = entity.canonical_name.strip()
        slug = slugify(name)
        
        # Check if it matches a known entity from other types
        if slug in known_entity_slugs:
            logger.debug("Filtering entity from events: %s (matches known entity)", name)
            removed_count += 1
            continue
        
        # Check if it's a ship designation
        if _SHIP_FILTER_PATTERN.match(name):
            logger.debug("Filtering ship from events: %s", name)
            removed_count += 1
            continue
        
        filtered[key] = entity
    
    if removed_count > 0:
        logger.info("Filtered %d leaked entities from events", removed_count)
    return filtered


def _consolidate_overlaps(merged: Dict[str, MergedEntity], entity_type: str = None) -> Dict[str, MergedEntity]:
    """Merge entities that share any alias/name slug."""
    entities = list(merged.values())
    if not entities:
        return {}

    parent = list(range(len(entities)))

    def find(idx: int) -> int:
        while parent[idx] != idx:
            parent[idx] = parent[parent[idx]]
            idx = parent[idx]
        return idx

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    name_to_idx: Dict[str, int] = {}
    for idx, entity in enumerate(entities):
        for key in _name_keys(entity):
            if key in name_to_idx:
                union(idx, name_to_idx[key])
            else:
                name_to_idx[key] = idx

    grouped: Dict[int, List[MergedEntity]] = {}
    for idx, entity in enumerate(entities):
        grouped.setdefault(find(idx), []).append(entity)

    consolidated: Dict[str, MergedEntity] = {}
    for group_entities in grouped.values():
        # Prefer the name that appears in the most chapters (extraction refs)
        # This ensures major names like "Sleeper Service" beat minor mentions like "Quietly Confident"
        canonical = max(
            ((e.canonical_name, len(e.extraction_refs)) for e in group_entities),
            key=lambda x: (x[1], len(x[0]))  # Primary: ref count, secondary: longest name
        )[0]
        aliases: set[str] = set()
        appears_in: set[str] = set()
        extraction_refs: set[str] = set()
        for ent in group_entities:
            aliases.update(ent.aliases)
            aliases.add(ent.canonical_name)
            appears_in.update(ent.appears_in)
            extraction_refs.update(ent.extraction_refs)

        aliases.discard(canonical)
        consolidated[slugify(canonical)] = MergedEntity(
            canonical_name=canonical,
            aliases=sorted(_filter_aliases(list(aliases), entity_type)),
            appears_in=sorted(appears_in),
            extraction_refs=sorted(extraction_refs),
        )

    return consolidated


def merge_entities(entity_type: str, force: bool = False, skip_dedup: bool = False, client: GeminiClient = None, book_slug: str = None) -> dict:
    """
    Merge entities of a type and write merged JSON.
    
    Args:
        entity_type: Type of entities to merge
        force: Overwrite existing merged file
        skip_dedup: Skip LLM-based semantic deduplication (faster but less thorough)
        client: Optional GeminiClient for deduplication (created if needed)
        book_slug: Optional book slug to filter extractions (e.g. "surface_detail")
    
    Returns:
        dict with keys: initial, garbage, dedup, cross, final (counts)
    """
    # Use book-specific output path if filtering by book
    if book_slug:
        output_path = config.ENTITIES_DIR / f"{entity_type}_{book_slug}.json"
    else:
        output_path = config.ENTITIES_DIR / f"{entity_type}.json"
    if output_path.exists() and not force:
        logger.info("Merged entities already exist at %s", output_path)
        count = len(read_json(output_path).get("entities", []))
        return {"entity_type": entity_type, "raw": count, "consolidate": 0, "deterministic": 0, "garbage": 0, "dedup": 0, "cross": 0, "final": count}

    extractions = _load_extractions(entity_type)
    
    # Filter to specific book if requested
    if book_slug:
        original_count = len(extractions)
        # Normalize: convert hyphens to underscores for comparison (slugify uses hyphens, chunk_ids use underscores)
        normalized_book_slug = book_slug.lower().replace("-", "_")
        extractions = [e for e in extractions if e.chunk_id.lower().replace("-", "_").startswith(normalized_book_slug)]
        logger.info("Filtered extractions from %d to %d for book: %s (normalized: %s)", original_count, len(extractions), book_slug, normalized_book_slug)
    merged: Dict[str, MergedEntity] = {}

    for extraction in tqdm(extractions, desc="Merging entities"):
        # Extract book slug from chunk_id (e.g., "Excession_split_006" -> "excession")
        chunk_id = extraction.chunk_id
        if "_split_" in chunk_id:
            book_slug = chunk_id.split("_split_")[0].lower()
        else:
            book_slug = chunk_id.split("_")[0].lower()
        for ent in extraction.entities:
            candidate_names = [ent.name] + list(ent.aliases)
            existing_key = _find_existing_key(merged, candidate_names)

            if existing_key:
                record = merged[existing_key]
                new_aliases = _filter_aliases(ent.aliases + [ent.name, record.canonical_name], entity_type)
                record.aliases = sorted(
                    set(record.aliases + new_aliases)
                )
                if book_slug not in record.appears_in:
                    record.appears_in.append(book_slug)
                if extraction.chunk_id not in record.extraction_refs:
                    record.extraction_refs.append(extraction.chunk_id)
            else:
                key = slugify(ent.name)
                merged[key] = MergedEntity(
                    canonical_name=ent.name,
                    aliases=sorted(_filter_aliases(list(ent.aliases), entity_type)),
                    appears_in=[book_slug],
                    extraction_refs=[extraction.chunk_id],
                )

    # Track raw count before any filtering
    raw_count = len(merged)

    merged = _consolidate_overlaps(merged, entity_type)
    count_after_consolidate = len(merged)
    
    # Filter known ships from all entity types except ships_and_minds
    deterministic_removed = 0
    if entity_type in ("characters", "technology", "factions", "locations"):
        before = len(merged)
        merged = _filter_known_ships(merged, entity_type)
        deterministic_removed += before - len(merged)
    
    # Filter known entities from events (characters, ships, locations leak into events)
    if entity_type == "events":
        before = len(merged)
        merged = _filter_entities_from_events(merged)
        deterministic_removed += before - len(merged)
    
    # Calculate consolidation removals (alias dedup)
    consolidate_removed = raw_count - count_after_consolidate
    
    write_json({"entities": [m.model_dump() for m in merged.values()]}, output_path)
    initial_count = len(merged)  # Count after deterministic filters
    logger.info(
        "Merged %s entities (%s unique) -> %s",
        entity_type,
        len(merged),
        output_path,
    )
    
    # Run LLM-based semantic deduplication
    if not skip_dedup:
        try:
            # Import here to avoid circular dependency
            import sys
            sys.path.insert(0, str(config.PROJECT_ROOT))
            from deduplicate_entities import run_deduplication, run_cross_entity_dedup, run_garbage_filter, load_entities
            
            if client is None:
                client = GeminiClient()
            
            # Step 1: Garbage filter - remove obvious garbage (pronouns, numbered speakers)
            garbage_removed = run_garbage_filter(entity_type, client)
            if garbage_removed > 0:
                logger.info("Garbage filter removed %d non-entities from %s", garbage_removed, entity_type)
            
            # Step 2: Within-entity dedup
            dedup_removed = run_deduplication(entity_type, client)
            if dedup_removed > 0:
                logger.info("LLM deduplication removed %d duplicates from %s", dedup_removed, entity_type)
            
            # Step 3: Cross-entity dedup - remove ships from non-ship entity types
            cross_removed = 0
            if entity_type in ("characters", "technology", "factions", "locations"):
                cross_removed = run_cross_entity_dedup(entity_type, client)
                if cross_removed > 0:
                    logger.info("Cross-entity dedup removed %d ships from %s", cross_removed, entity_type)
            
            # Get final count for stats
            final_count = len(load_entities(entity_type))
        except Exception as e:
            logger.warning("LLM deduplication failed for %s: %s", entity_type, e)
            garbage_removed = dedup_removed = cross_removed = 0
            final_count = len(read_json(output_path).get("entities", []))
    else:
        logger.info("Skipping LLM deduplication for %s (--skip-dedup)", entity_type)
        garbage_removed = dedup_removed = cross_removed = 0
        final_count = len(read_json(output_path).get("entities", []))
    
    return {
        "entity_type": entity_type,
        "raw": raw_count,
        "consolidate": consolidate_removed,
        "deterministic": deterministic_removed,
        "garbage": garbage_removed,
        "dedup": dedup_removed,
        "cross": cross_removed,
        "final": final_count,
    }
