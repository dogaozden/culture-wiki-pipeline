"""Central configuration for the Culture Wiki pipeline."""

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency in some environments
    load_dotenv = None

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if load_dotenv:
    load_dotenv(PROJECT_ROOT / ".env", override=True)
BOOKS_DIR = PROJECT_ROOT / "books"
DATA_DIR = PROJECT_ROOT / "data"
CHUNKS_DIR = DATA_DIR / "chunks"
EXTRACTIONS_DIR = DATA_DIR / "extractions"
ENTITIES_DIR = DATA_DIR / "entities"
ARTICLES_DIR = DATA_DIR / "articles"
CACHE_DIR = DATA_DIR / ".cache"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
WIKI_DIR = PROJECT_ROOT / "wiki"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# Chapter handling
MAX_CHAPTER_TOKENS = 50_000
MIN_CHAPTER_TOKENS = 500

# API configuration
# 2.5-flash-lite: Better instruction following, no semantic hallucinations (use for extraction)
# 3-flash: Better dedup accuracy (use for merge), better prose quality (use for synthesis)
GEMINI_MODEL = "gemini-2.5-flash-lite-preview-09-2025"  # Default for extract
GEMINI_MODEL_MERGE = "gemini-3-flash-preview"  # Default for merge - better dedup accuracy
GEMINI_MODEL_SYNTHESIS = "gemini-2.5-flash-lite-preview-09-2025"  # Dev default. Use --model gemini-3-flash-preview for prod
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
MAX_RETRIES = 3
REQUESTS_PER_MINUTE = 60
USE_CACHE = False  # LLM cache is opt-in; enable with --use-cache flag

# Entity types we process
ENTITY_TYPES = [
    "characters",
    "ships_and_minds",
    "locations",
    "technology",
    "factions",
    "events",
]

# Parallelism
PARALLEL_WORKERS = 32

# I/O defaults
DEFAULT_ENCODING = "utf-8"
