# Culture Wiki — LLM Context Document

**For:** AI Agents (Claude, Codex, Gemini)
**Purpose:** Technical implementation details for safe codebase changes
**Last Updated:** January 4, 2026

> 📌 **See [ARCHITECTURE.md](./ARCHITECTURE.md)** for human-readable overview and CLI usage.

---

## Repository Layout

```
Culture-Wiki/
├── run.py                   # CLI entry point
├── src/                     # Pipeline code
│   ├── config.py            # Global constants + .env loading
│   ├── chunker.py           # Chapter loading and chunk splitting
│   ├── extractor.py         # Entity extraction via Gemini structured output
│   ├── merger.py            # Deduplicate entities (with LLM garbage filter + cross-entity dedup)
│   ├── cross_book_merger.py # Find entities in 2+ books, merge into unified cross-book entities
│   ├── synthesizer.py       # Article synthesis via Gemini structured output
│   ├── renderer.py          # HTML rendering using Jinja templates
│   ├── image_generator.py   # Entity portrait generation (Curator architecture)
│   ├── gemini_client.py     # API wrapper, caching, retries, rate limiting
│   ├── models.py            # Pydantic models for all data structures
│   ├── utils.py             # Slugify, token count, JSON I/O, timestamps
│   ├── estimator.py         # Synthesis time estimation
│   ├── validator.py         # Alias/quote validation against source text
│   └── pipeline.py          # Orchestration
├── books/                   # Source content (one folder per book, one file per chapter)
├── data/                    # Derived artifacts
│   ├── extractions/         # Per-chapter extracted entities
│   ├── entities/            # Merged entities ({type}_{book}.json for book-specific)
│   ├── articles/            # Synthesized articles
│   ├── images/              # Generated entity portraits ({type}/{slug}.png)
│   ├── .cache/              # Gemini response cache (model-scoped)
│   └── common_words.txt     # Autolink filter (Google's top 3000 words)
├── templates/               # Jinja templates (do not modify unless changing UI)
├── initial files/           # Static assets (CSS, citations.js)
├── wiki/                    # Rendered static site output
├── crown_jewels/            # Hand-written article overrides
├── .env                     # Local API key config (not committed)
├── deduplicate_entities.py  # Standalone LLM dedup module
├── citation_checker.py      # Optional LLM-based fact-quote verification
├── extract_descriptions.py  # Visual description extraction for images
├── generate_image_prompt.py # Convert descriptions to image prompts
├── convert_epubs.py         # Extract .epub to chapter HTML
├── inspect_epubs.py         # Debug helper for epub structure
├── show_entity_data.py      # Debug: view compiled entity data
└── inspect_genai.py         # List available Gemini models
```

---

## Configuration (src/config.py)

### Paths
- `PROJECT_ROOT`, `BOOKS_DIR`, `DATA_DIR`, `CHUNKS_DIR`, `EXTRACTIONS_DIR`
- `ENTITIES_DIR`, `ARTICLES_DIR`, `CACHE_DIR`, `WIKI_DIR`, `TEMPLATES_DIR`, `PROMPTS_DIR`, `IMAGES_DIR`

### API Models
| Purpose | Model | Notes |
|---------|-------|-------|
| Extraction | `gemini-2.5-flash-lite-preview-09-2025` | Better instruction following |
| Merge | `gemini-3-flash-preview` | Better dedup accuracy |
| Synthesis | `gemini-2.5-flash-lite-preview-09-2025` | Use `--model gemini-3-flash-preview` for prod |
| Image | `gemini-3-pro-image-preview` | Portrait generation |

### Runtime Constants
- `REQUESTS_PER_MINUTE = 60` (enforced in Gemini client)
- `MAX_RETRIES = 3` (Gemini client retries)
- `PARALLEL_WORKERS = 32` (extract/synthesize)
- `MAX_CHAPTER_TOKENS = 10000` (split threshold)
- `MIN_CHAPTER_TOKENS = 500`
- `USE_CACHE = False` (opt-in with `--use-cache`)
- `DEFAULT_ENCODING = "utf-8"`

---

## Core Data Models (src/models.py)

All models set `model_config = ConfigDict(extra="ignore")` to drop unknown fields.

### Chapter
- `book_slug`, `book_title`, `index`, `title`, `text`, `token_count`
- `source_file`, `section_type`, `is_split`, `split_part`
- `chunk_id` property: `Path(source_file).stem` + `_split_{split_part:03d}` when split

### Extraction Models
- **EntityFact**: `id` (format: `chunk_id#seq`), `fact`, `quote`, `location`
- **EntityRelationship**: `target`, `type`, `strength` (1-10), `quote`
- **Entity**: `name`, `aliases`, `species`, `affiliation`, `prominence` (major/supporting/mentioned), `prominence_score` (1-10), `relationships`, `information`
- **EntityExtraction**: `chunk_id`, `chapter_title`, `entity_type`, `entities`

### Merged Entities
- **MergedEntity**: `canonical_name`, `aliases`, `appears_in`, `extraction_refs`

### Article Models
- **Citation**: `id` (fact ID or legacy int), `book`, `quote`, `chunk_id`, `chapter`, `source_file`
- **ArticleSection**: `title`, `content`, `citations` (fact IDs as str or legacy int)
- **Article**: `entity_name`, `entity_type`, `slug`, `summary`, `generated_at`, `aliases`, `appears_in`, `sections`, `citations`, `related_entities`, `prominence_score`

### Structured Output Schemas
- `ExtractionResponse` → `ExtractedEntity` → `FactInfo`, `ExtractedRelationship`
- `SynthesisResponse` → `SynthesisSection`, `SynthesisCitation`
- `SynthesisCitation.id` is `str` (fact ID), not `int`

---

## Entity Types

| Type | Description |
|------|-------------|
| `characters` | People, drones, Mind avatars |
| `ships_and_minds` | Culture ships, Minds, non-Culture vessels |
| `locations` | Orbitals, planets, ship interiors |
| `technology` | Weapons, devices, implants |
| `factions` | Civilizations, organizations |
| `events` | Battles, incidents, historical events |

---

## Key Design Decisions

### Why Gemini for Extraction?
- Structured output support (JSON schema enforcement)
- Cost-effective for high-volume extraction (~$2-3/book)
- Fast enough for parallel workers

### Why Verbatim Quote Citations?
1. **UX**: Readers hover to see exact source passage
2. **Hallucination check**: If quote doesn't exist, citation is invalid
3. **Trust**: Wiki is only as reliable as its sources

### Why Static HTML Output?
- No server needed for deployment
- GitHub Pages / Netlify friendly
- Fast, cacheable, works offline

### Why Predefined Ship Prompts?
- Culture ship names are abstract ("Mistake Not...") and confuse image generators
- Class-based visual identity (GSV=huge, ROU=military) is more accurate to lore
- Ensures consistent sci-fi art style across the wiki

---

## Stage Implementation Details

### Chunker (src/chunker.py)
- **File discovery**: loads `*.md` and `*.html`, ordered by first number in filename stem
- **Title extraction**: first H1, else first non-empty line, else `"Chapter N"`
- **Split strategy**: scene breaks (`***`/`---`/`___`) → paragraphs → length-based
- **Oversize handling**: recursive paragraph splits, then length split as last resort
- **Output**: in-memory `Chapter` objects with `is_split` + `split_part`; no disk writes

### Extractor (src/extractor.py)
- `_resolve_book_dir()` accepts direct path, folder name, or slugified name
- `build_extraction_prompt()` merges system prompt + per-entity prompt with chapter metadata
- **Validation in `_write_extraction()`**:
  - Entity names must appear in source (case-insensitive, normalized)
  - Quote validation with fuzzy repair (`SequenceMatcher`, threshold 0.82)
  - Hallucinated aliases removed if not in chapter text
  - Fact IDs assigned: `{chunk_id}#{seq:03d}`
- **Output**: `data/extractions/{type}/{book}/{chunk_id}_{type}.json`
- **Caching**: `cache_key = f"{chunk_id}:{entity_type}"` (prompt changes don't invalidate)

### Merger (src/merger.py)
**Two-Pass Architecture**:

1. **Pass 1: Consolidation & Type Voting**
   - Slug/alias matching + ship prefix stripping (`GCU Grey Area` → `Grey Area`)
   - Type voting counts appearances across ALL types; excludes non-dominant types
   - Generic alias filtering (pronouns, roles like "the woman")

2. **Pass 2: Filtering** (`apply_entity_filters()`)
   - Deterministic: `_filter_known_ships()`, `_filter_known_characters()`, `_filter_entities_from_events()`
   - LLM: Garbage filter → Alias pre-filter → Semantic dedup → Cross-entity dedup
   - All LLM filters receive `book_slug` for book isolation

- **Book slug normalization**: Converts hyphens to underscores before matching
- **Output**: `data/entities/{type}_{book}.json`

### Cross-Book Merger (src/cross_book_merger.py)
- Matches on canonical name only (case-insensitive)
- `PUBLICATION_ORDER`: Consider Phlebas (1987) → Hydrogen Sonata (2012)
- Merges `aliases`, `extraction_refs`, `information` (dedup by fact ID)
- Uses first book in publication order as base entity
- **Output**: `data/entities/{type}_cross-book.json`

### Synthesizer (src/synthesizer.py)
- **Inputs**: `MergedEntity` list + all matching extractions + `chunk_map` for validation
- **Fact ID System**: `{chunk_id}#{seq}` shown as `• [excession_ch001#003] Fact: ...`
- **Citation Reconstruction**: LLM outputs text with `[fact_id]` tags; Python rebuilds full citations
- **Validation**:
  - Unknown fact IDs → hard fail (no retries)
  - Citation density < 50% → retry (up to 10x, cache-busting key)
- **Prominence**: `(unique_chapters * 5) + citation_count + cross_ref_count`
- **Entity-Type-Aware Prompts**:
  - **Characters**: Background, physical description, personality, key relationships, chronological narrative, notable moments, fate
  - **Ships/Minds**: Class/designation, personality, role in events, relationships, notable actions
  - **Locations**: Physical description, significance, events there, inhabitants
- **Length Calibration**: 15+ chunks = 1500-3000 words, 5-14 = 800-1500, <5 = 300-800
- **Output**: `data/articles/{type}/{slug}.json`

### Renderer (src/renderer.py)
- **Entity lookup**: maps names/aliases/slugs to URLs; templates use `entity_link` filter
- **Autolink**: regex from entity names (longest-first), first mention per block, skips self-links and common words
- **Common word filtering**: loads `data/common_words.txt` (Google's top 3000)
- **Citations**: moves after punctuation, maps fact IDs to `[1], [2], ...`, anchor IDs use normalized fact IDs
- **Book rendering**: landing page + per-book/type indexes + `_book-only/` debug copies
- **Crown jewels**: copies `crown_jewels/*.json` into `data/articles/` before render
- **Apocrypha**: markdown files in `books/Apocrypha/` render directly with `tables`/`fenced_code`/`toc` extensions
- **Search index**: `wiki/search_index.json` (aliases capped at 5, summaries at 150 chars)
- **Graph data**: `wiki/graph_data.json` with nodes/edges from `related_entities`

### Image Generator (src/image_generator.py)
**Curator Architecture (Two-Pass)**:

1. **Pass 1: Description Extraction**
   - Iterates `extraction_refs` (up to 10 chunks)
   - Validates each extracted quote exists in source via `normalize_for_match()`
   - Collects (source_ref, quote) tuples as candidates

2. **Pass 2: Curator Selection**
   - LLM picks most representative description
   - Avoids temporary disguises

3. **Image Generation**
   - Ship class templates: GSV/GCU/ROU/LOU/MSV/GCV (including `xGSV/xGCU/xROU`)
   - Class detection at start or as standalone token
   - Calls `gemini-3-pro-image-preview` with `response_modalities: ["IMAGE", "TEXT"]`
   - **Output**: `data/images/{type}/{slug}.png`
   - Force: archives to `data/images/.archive/{type}/{slug}_{timestamp}.png`

---

## Gemini Client (src/gemini_client.py)

- Uses `google.genai.Client(api_key=...)`
- **Structured output**: `response_mime_type = "application/json"` + Pydantic schema
- **Model-scoped cache**: `data/.cache/{model_slug}/{stage}/{sha256}.json`
- **Rate limiting**: 60-second sliding window
- **Retries**: tenacity with exponential backoff
- **Preflight**: minimal request to fail fast on API key issues

---

## Validator Utilities (src/validator.py)

- `normalize_for_match()`: curly quotes → ASCII, ellipses → `...`, collapse whitespace
- `load_chunk_text()`: locates chapter by scanning `books/` for slug-matching folders
- `verify_aliases_in_file()`: removes aliases not in source, rewrites JSON
- `verify_book_extractions()`: loops types, aggregates stats

---

## CLI Flags (run.py)

| Flag | Description |
|------|-------------|
| `--stage` | `chunk`, `extract`, `merge`, `synthesize`, `render`, `clean`, `all`, `image`, `cross` |
| `--book` | Limit to specific book (slug matching) |
| `--entity` | Target single entity (name or alias, case-insensitive) |
| `--entity-type` | Process single type |
| `--force` | Reprocess even if outputs exist |
| `--workers N` | Override parallel workers |
| `--max-retries N` | Max synthesis retries (default: 10) |
| `--model` | Override default model for stage |
| `--dry-run` | Print first prompt, no API calls (extract only) |
| `--limit N` | Process first N chapters (extract only) |
| `--skip-dedup` | Skip LLM semantic dedup during merge |
| `--use-cache` | Enable Gemini response cache |
| `--ship-class` | Generate test image for ship class |
| `--min-citations N` | Min citations for "major" entity (image) |
| `--clean` | Clean targets: `all`, `cache`, `extractions`, `entities`, `articles`, `wiki`, `chunks`, `output` |
| `--yes` | Skip confirmation for clean |

---

## Pipeline Orchestration (src/pipeline.py)

- `Pipeline.run()` enforces: `chunk` → `extract` → `merge` → `synthesize` → `render`
- `_resolve_books()`: direct path, folder name, or slugified name
- `_discover_books()`: skips hidden dirs and `books/Apocrypha/`
- Book-isolated: merge/synthesize iterate book-by-book when no `--book` provided

---

## Standalone Scripts

| Script | Purpose |
|--------|---------|
| `deduplicate_entities.py` | LLM dedup with alias pre-filter, garbage filter, cross-entity cleanup |
| `citation_checker.py` | Verify fact-quote alignment (SUPPORTED/PARTIAL/UNSUPPORTED) |
| `extract_descriptions.py` | Pull visual descriptions for entities |
| `generate_image_prompt.py` | Convert descriptions to image prompt JSON |
| `show_entity_data.py` | Debug: view compiled entity data + synthesis prompt |
| `inspect_genai.py` | List available Gemini models |
| `convert_epubs.py` | Extract .epub to chapter HTML/XHTML |
| `inspect_epubs.py` | Debug: list epub contents |
| `verify_extraction_aliases.py` | Verify aliases against source text |

---

## Key Conventions

### Slug and ID Formats
- **Slugify**: lowercase, non-alnum → `-`, collapse repeats
- **book_slug**: slugified folder name
- **chunk_id**: filename stem + `_split_###` for split parts
- **fact_id**: `{chunk_id}#{seq:03d}` (e.g., `excession_ch001#003`)

### Idempotency
- Extraction/synthesis skip existing output files unless `--force`
- Gemini cache uses prompt hash (synthesis) or chunk_id+type (extraction)
- Clear cache: `--stage clean --clean cache --yes`

### Quote Handling
- Quotes must be exact; minor punctuation differences cause skips
- Normalization: curly quotes → ASCII, `…` → `...`, collapse whitespace
- Very short chapters (title-only) produce no valid quotes

---

## Documentation Audit Workflow

Script: `.agent/scripts/audit_docs.py`

```bash
# Audit only git-changed files
python3 .agent/scripts/audit_docs.py

# Force audit ALL Python files
python3 .agent/scripts/audit_docs.py --all
```

Spawns `codex` subagent per file to update documentation for modified/added files or remove references for deleted files.

---

## Dependencies

```
google-genai      # Gemini SDK (not legacy google-generativeai)
pydantic          # Data models with validation
tenacity          # Retry logic with exponential backoff
jinja2            # HTML templating
tiktoken          # Token counting (optional, word-count fallback)
python-dotenv     # .env file loading
markdown          # Apocrypha rendering
tqdm              # Progress bars
```

---

*This document is for agent context. See ARCHITECTURE.md for human-readable documentation.*
