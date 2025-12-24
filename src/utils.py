"""Shared helper utilities."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_ENCODING

logger = logging.getLogger(__name__)

_token_encoder = None


def ensure_dir(path: Path) -> Path:
    """Create a directory (and parents) if it does not exist."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(value: str) -> str:
    """Convert a string to a filesystem-friendly slug."""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "item"


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken if available, otherwise fallback to words."""
    global _token_encoder
    try:
        if _token_encoder is None:
            import tiktoken

            _token_encoder = tiktoken.get_encoding("cl100k_base")
        return len(_token_encoder.encode(text))
    except Exception as exc:  # pragma: no cover - fallback path
        logger.debug("Falling back to word-based token count: %s", exc)
        return max(1, len(text.split()))


def read_json(path: Path) -> Any:
    """Read JSON from disk."""
    with path.open("r", encoding=DEFAULT_ENCODING) as f:
        return json.load(f)


def write_json(data: Any, path: Path) -> None:
    """Write JSON to disk with pretty formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=DEFAULT_ENCODING) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def utc_now() -> str:
    """Return an ISO8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def rate_limit_sleep(next_available_ts: float) -> None:
    """Sleep until the provided timestamp."""
    now = time.time()
    if next_available_ts > now:
        time.sleep(next_available_ts - now)

