"""Merge and deduplicate entities across books."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

from . import config
from .models import EntityExtraction, MergedEntity
from .utils import read_json, slugify, write_json

logger = logging.getLogger(__name__)


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
            aliases=sorted(aliases),
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
                record.aliases = sorted(
                    set(record.aliases + ent.aliases + [ent.name, record.canonical_name])
                )
                if book_slug not in record.appears_in:
                    record.appears_in.append(book_slug)
                if extraction.chunk_id not in record.extraction_refs:
                    record.extraction_refs.append(extraction.chunk_id)
            else:
                key = slugify(ent.name)
                merged[key] = MergedEntity(
                    canonical_name=ent.name,
                    aliases=sorted(set(ent.aliases)),
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
