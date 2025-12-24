"""Merge and deduplicate entities across books."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

from . import config
from .models import EntityExtraction, MergedEntity
from .utils import read_json, slugify, write_json

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
    'an old flame', 'the woman', 'the man', 'the human', 'the drone',
    'the avatar', 'the creature', 'the emissary', 'the bird', 'the machine',
    'old perpetually pregnant', 'the other woman', 'lady', 'mistress',
    'boy', 'child', 'arse-hole', 'imbecile', 'moron', 'wretch', 'spy',
    'he', 'she', 'it', 'they', 'he/she', 'her man', 'your girl', 'our lass',
    'the bad guy', 'the girl', 'this human', 'young lady', 'representative',
}

import re
_ALIAS_PATTERNS = [re.compile(p, re.IGNORECASE) for p in ALIAS_BLACKLIST_PATTERNS]


def _is_valid_alias(alias: str) -> bool:
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
    return True


def _filter_aliases(aliases: List[str]) -> List[str]:
    """Filter out hallucinated/descriptive aliases, keeping only proper names."""
    return [a for a in aliases if _is_valid_alias(a)]


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
    return None


def _name_keys(entity: MergedEntity) -> set[str]:
    keys = {slugify(entity.canonical_name)}
    keys.update(slugify(alias) for alias in entity.aliases)
    return {k for k in keys if k}


def _consolidate_overlaps(merged: Dict[str, MergedEntity]) -> Dict[str, MergedEntity]:
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
        canonical = max(
            (e.canonical_name for e in group_entities), key=lambda n: len(n)
        )
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
            aliases=sorted(_filter_aliases(list(aliases))),
            appears_in=sorted(appears_in),
            extraction_refs=sorted(extraction_refs),
        )

    return consolidated


def merge_entities(entity_type: str, force: bool = False) -> Path:
    """Merge entities of a type and write merged JSON."""
    output_path = config.ENTITIES_DIR / f"{entity_type}.json"
    if output_path.exists() and not force:
        logger.info("Merged entities already exist at %s", output_path)
        return output_path

    extractions = _load_extractions(entity_type)
    merged: Dict[str, MergedEntity] = {}

    for extraction in extractions:
        book_slug = extraction.chunk_id.split("_")[0]
        for ent in extraction.entities:
            candidate_names = [ent.name] + list(ent.aliases)
            existing_key = _find_existing_key(merged, candidate_names)

            if existing_key:
                record = merged[existing_key]
                new_aliases = _filter_aliases(ent.aliases + [ent.name, record.canonical_name])
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
                    aliases=sorted(_filter_aliases(list(ent.aliases))),
                    appears_in=[book_slug],
                    extraction_refs=[extraction.chunk_id],
                )

    merged = _consolidate_overlaps(merged)
    write_json({"entities": [m.model_dump() for m in merged.values()]}, output_path)
    logger.info(
        "Merged %s entities (%s unique) -> %s",
        entity_type,
        len(merged),
        output_path,
    )
    return output_path
