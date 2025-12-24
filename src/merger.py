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

    write_json({"entities": [m.model_dump() for m in merged.values()]}, output_path)
    logger.info(
        "Merged %s entities (%s unique) -> %s",
        entity_type,
        len(merged),
        output_path,
    )
    return output_path

