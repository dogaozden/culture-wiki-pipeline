# Culture Wiki — Commands & Architecture

*How we turn novels into a wiki, step by step.*

---

## 🔄 The Pipeline

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│    CHUNK    │ →  │   EXTRACT   │ →  │    MERGE    │ →  │  SYNTHESIZE │ →  │   RENDER    │
│ (split text)│    │(per chapter)│    │ (combine)   │    │ (write)     │    │ (HTML)      │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                                             ↓                   ↓
                                      ┌─────────────┐     ┌─────────────┐
                                      │    CROSS    │     │    IMAGE    │ (optional)
                                      │(multi-book) │     │ (portraits) │
                                      └─────────────┘     └─────────────┘
```

**Pipeline Orchestration (`src/pipeline.py`)**
- Stage order is enforced by `Pipeline.run()` (`chunk` → `extract` → `merge` → `synthesize` → `render`, or `clean`).
- `--book` accepts a direct path, exact folder name, or slugified name; if omitted, all book folders are processed.
- Book discovery **excludes** `books/Apocrypha/` because those files are rendered directly in the render stage.

> [!IMPORTANT]  
> **Model Selection (Dec 2025)**  
> Different stages use different models by default:
>
> | Stage | Model | Why |
> |-------|-------|-----|
> | Extract | `gemini-2.5-flash-lite` | Better instruction following, no semantic hallucinations |
> | Merge | `gemini-3-flash-preview` | Better dedup accuracy (events: 5 → 97 entities vs 2.5 Lite) |
> | Synthesize | `gemini-2.5-flash-lite` | Cheaper for high-volume; use `--model gemini-3-flash-preview` for prod |
>
> Testing revealed `gemini-3-flash-preview` causes **semantic hallucinations** during extraction (facts not supported by quotes), but produces better wiki article prose. The pipeline now uses each model where it performs best.

> [!TIP]
> **Other Quality-of-Life Features:**
> - **Progress bars**: All stages show tqdm progress bars with entity type names
> - **Opt-in cache**: LLM cache is OFF by default. Use `--use-cache` to enable.
> - **Tolerance validation**: Large entities (80+ facts) allow up to 3 missing citations
> - **`--book` flag**: Process a single book (works for chunk/extract/merge/synthesize stages)
> - **Selective runs**: `--entity` targets a single entity; `--entity-type` restricts to one type
> - **Controlled extraction**: `--dry-run` prints the first prompt; `--limit N` caps chapters
> - **Merge tuning**: `--skip-dedup` skips LLM semantic dedup (faster, less thorough)
> - **Cleaning outputs**: `--stage clean` + `--clean` targets (use `--yes` to skip confirmation)
> - **Synthesis time estimator**: `src/estimator.py` reads merged entity JSON files and estimates runtime at a configurable calls/hour rate

**CLI Entry Point (`run.py`)**
- `--stage`: one of `chunk`, `extract`, `merge`, `synthesize`, `render`, `clean`, `all`, `image`, `cross`.
- `--force`: reprocess even if cached outputs exist (does not bypass the Gemini cache).
- `--entity-type`: restricts work to a single entity type for extract/merge/synthesize/image.
- `--use-cache`: enable the Gemini response cache (off by default).
- `--clean`: one or more targets (`all`, `cache`, `extractions`, `entities`, `articles`, `wiki`, `chunks`, `output`).

**Utility Helpers (`src/utils.py`)**
- `ensure_dir()` creates directories (with parents) when needed.
- `slugify()` normalizes strings for filesystem-safe names.
- `count_tokens()` uses tiktoken if available, otherwise a word-count fallback.
- `read_json()`/`write_json()` handle JSON I/O with `DEFAULT_ENCODING` and pretty output.
- `utc_now()` returns ISO-8601 UTC timestamps.
- `rate_limit_sleep()` sleeps until a provided Unix timestamp.

**Gemini Client (`src/gemini_client.py`)**
- Uses the `google.genai` SDK client with a lightweight wrapper.
- Enforces structured JSON via response schema for extract/synthesize.
- Opt-in cache is **model-scoped** (model name is part of the cache path) to avoid cross-model reuse.
- Rate limiting uses a 60-second sliding window; retries use exponential backoff (tenacity).
- `preflight()` sends a minimal request once per run to fail fast on API key/model access issues.

---

## Stage 0: CHUNK 📄

**What happens:**  
Split book markdown files into processable chunks that fit within token limits.

**The process:**
1. **Load chapter files** — Reads `*.md` and `*.html` from `books/{BookName}/`
2. **Split by scene breaks** — Uses `***` or `---` separators first
3. **Split by paragraphs** — Falls back to paragraph splitting if no scene breaks
4. **Token limit check** — Each chunk stays under `MAX_CHAPTER_TOKENS` (10,000)

**When to use:**  
If your book has very long chapters that exceed the token limit, run chunk first:
```bash
python3 run.py --stage chunk --book SurfaceDetail
```

> 💡 **Note:** For most pre-split books (like Excession), chunking happens automatically during extract. You only need to run chunk explicitly if you're debugging or have unusual file formats.

**Input:**  
`books/{BookName}/*.md` — One markdown file per chapter

**Output:**  
Chapter objects in memory (no disk writes) → passed to extract stage

---

## Stage 1: EXTRACTION 📖

**What happens:**  
We read each chapter of a novel and ask Gemini to extract structured information.

**Per chapter, per entity type:**  
For each chapter, we make **6 separate API calls** — one for each entity type:
- Characters (people, drones, avatars)
- Ships & Minds (vessels, AIs)
- Locations (planets, orbitals, habitats)
- Technology (weapons, devices)
- Factions (civilizations, organizations)
- Events (battles, incidents)

**What Gemini returns:**  
For each entity found:
- **Name** — Primary name used in this chapter
- **Aliases** — Other names for the same entity (must appear in text!)
- **Information** — List of facts, each with a verbatim quote as evidence
- **Relationships** — Connections to other entities
- **Prominence score** — How important is this entity in this chapter?

### Post-Processing (Deterministic, No API Cost)

After getting LLM response, we run deterministic code to:

1. **Validate entity names** — Skip entities whose primary `name` does not appear in the source text
   - Case-insensitive match after normalizing quotes/ellipses and collapsing whitespace
   - Logged as warnings to surface hallucinated entities

2. **Validate quotes** — Check each quote exists in source text
   - Rejects hallucinated quotes before saving
   - Logged as warnings for debugging

3.  **Assign Fact IDs** — Each fact gets unique ID: `{chunk_id}#{seq}`
    -   Example: `excession_ch014#003` = 3rd fact from chapter 14
    -   IDs are stable across re-runs (deterministic)
    -   Used by synthesis for citation validation

4.  **Filter aliases against source text** — Drop aliases that do not appear verbatim in the chapter
    -   Case-insensitive match after normalizing quotes/ellipses and collapsing whitespace
    -   Logged as warnings to surface hallucinated aliases

```
LLM Response → Validate Quotes → Assign Fact IDs → Save JSON
                    ↓ (reject)
           ⚠️ Hallucinated quote logged
```

> 💡 **Why this matters:** Fact IDs are assigned AFTER the LLM call, so re-running 
> extraction with cached LLM responses still regenerates fact IDs at near-zero cost.

**Output:**  
`data/extractions/{type}/{book}/{chapter}_{type}.json`

**Example:**  
Chapter 1 of Excession → 6 files:
- `excession_ch001_characters.json`
- `excession_ch001_ships_and_minds.json`
- ...etc.

---

## Stage 2: MERGE 🔗

**What happens:**  
Combine all chapter extractions into unified entity lists.

**The process (in order):**

1.  **Deterministic Python matching (FREE)**  
    -   Slug-based: "Sleeper Service" from Ch1 + "Sleeper Service" from Ch5 → same entity
    -   Alias overlap: If Entity A has alias "X" and Entity B is named "X" → merge
    -   Ship prefix stripping: "GCU Grey Area" matches "Grey Area"
    -   Generic alias exclusion: skips vague aliases (e.g., "woman", "the man") during matching to avoid false merges

2.  **Canonical name selection**  
    Picks the name that appears in the **most chapters** (by extraction_refs count).
    This ensures "Sleeper Service" (48 chapters) beats "Quietly Confident" (mentioned once).

3.  **Type Voting (NEW)**
    -   Scans ALL extractions to count how often an entity appears in each type.
    -   Example: "Veppers" appears 50 times as `character`, 3 times as `ship`.
    -   Action: Removes "Veppers" from `ships` because `characters` is the dominant type.
    -   Prevents cross-contamination (e.g. ship names appearing as locations).
    -   When running book-scoped merges (`--book`), type voting can be limited to that book’s extractions.

4.  **Automated Alias Verification (NEW)**
    -   Before merging, validates all aliases against source text.
    -   Rejects hallucinated aliases (e.g. "Ms Gelian" for Ulver Seich) that don't appear verbatim.
    -   Prevents false merges caused by shared hallucinated aliases.
    -   Normalizes quotes (curly quotes → ASCII, ellipsis → `...`) and collapses whitespace before matching.
    -   Locates chunk text by scanning `books/` for slug-matching folders and `.md`/`.html`/`.txt` chapter files.
    -   Additional alias blacklist filters drop pronouns, generic roles, and ship-prefix-like aliases for non-ship types.

5.  **LLM Garbage Filter (NEW Dec 2025)**
    -   Removes generic descriptors: "the woman", "Admiral", "Speaker 1"
    -   Runs before within-entity dedup
    -   **Book Isolation**: Targeted at book-specific entity files (e.g., `characters_excession.json`) to prevent cross-novel leakage.
    -   Uses 3 Flash by default (much more conservative than 2.5 Flash Lite)

6.  **LLM Deduplication**
    -   First runs **alias pre-filter**: asks LLM to identify facts mentioning former names
    -   Then runs **main dedup**: compares entities with their alias-related facts
    -   Batched processing with retry logic for API failures
    -   **Default model: gemini-3-flash-preview** for better accuracy
    -   Alias pre-filter resolves each entity's `extraction_refs` to extraction JSON and scans facts for the entity name + aliases (caps facts per entity)
    -   Applied dedup keeps the canonical entity and appends deleted names as aliases
    -   Cross-entity dedup prompt includes a ship name reference list to remove leaked ships from other types

> 🏗️ **Two-Pass Architecture**
> 
> **Why two passes?** Cross-type filters (e.g., "remove ships from characters") need data from ALL entity types. If we filter while merging, some types don't exist yet.
> 
> **Pass 1: Merge All Types**
> 1. Load extractions for this entity type
> 2. Consolidate by name/alias matching
> 3. Apply **Type Voting** (`build_type_voting_map()` + `get_type_exclusions()`)
>    - Counts how often each entity appears in each type across ALL extractions
>    - Excludes entities where this type is NOT dominant
>    - Example: "Veppers" appears 50× as character, 3× as ship → removed from ships
> 4. Write preliminary JSON
>
> **Pass 2: Apply Filters** (calls `apply_entity_filters()`)
> 1. Load merged entities from Pass 1
> 2. Apply **Deterministic Cross-Type Filters** (now with fresh data from all types!)
>    - `_filter_known_ships()`: Remove ship names from non-ship types
>    - `_filter_known_characters()`: Remove character names from non-character types
>    - `_filter_entities_from_events()`: Remove leaked character/ship/location names from events
> 3. Apply **LLM Filters** (Garbage → Semantic Dedup → Cross-Entity)

> ✅ **Fixed: Canonical Name Selection**
> Previously picked the longest name, causing "Quietly Confident" to be canonical instead of "Sleeper Service".
> Now uses chapter frequency — names appearing in more chapters win.

**Output:**  
`data/entities/{type}_{book}.json` — Per-book merged entities (e.g. `characters_excession.json`)
(Global `data/entities/{type}.json` is only produced if merging across all books)

**Example entry:**
```json
{
  "canonical_name": "Sleeper Service",
  "aliases": ["The Sleeper", "SS"],
  "appears_in": ["excession"],
  "extraction_refs": ["excession_ch001", "excession_ch005", ...]
}
```

**Merge Summary Report:**  
At the end of merge, a table shows what each filter stage removed:

| Column | Filter Type | Description |
|--------|-------------|-------------|
| **Raw** | — | Total entities before any filtering |
| **Consolid** | Deterministic | Alias slug consolidation |
| **TypeVote** | Deterministic | Dominant type enforcement |
| **Determ** | Deterministic | Ship/event entity removal |
| **Garbage** | LLM | Generic descriptor removal |
| **Dedup** | LLM | Within-entity duplicate removal |
| **Cross** | LLM | entities that belong to a different type (e.g., ship names in characters) |
| **Final** | — | ~1,141 articles remaining (Global Restoration complete) |

---

## Stage 3: SYNTHESIS ✍️

**Goal:** Turn merged facts into a cohesive, verified article.

**The Workflow (Refactored Strategy):**
1. **Prompt Construction:** 
   - We feed the LLM the Merged Entity + all Extractions (facts with IDs).
2. **LLM Generation (Text Only):**
   - We ask the LLM to write the article with *inline citation tags* only (e.g. `[excession_ch001#003]`).
   - We do *NOT* ask it to output the full citations array (saves tokens, prevents errors).
3. **Python Post-Processing (The "Rebuild" Step):**
   - Code parses the LLM output for all `[fact_id]` tags.
   - It looks up each ID in the original extraction files.
   - It constructs the full `citations` JSON array deterministically using the *original* verified quote and metadata.
   - Citation metadata comes from extraction JSON (chapter title and book inferred from `chunk_id`); `source_file` is filled from the chapter map.
   - **Validation:** If the LLM uses a tag that doesn't exist in the input -> **REJECT** the article (Hallucination detected, no retries).
4. **Citation Density Gate:**
   - Articles must meet a minimum citation rate (50% of sentences include citations).
   - Low-density articles are retried up to the synthesis retry limit, then skipped.
   - Retries use a cache-busting key (`{type}:{slug}:retryN`) to avoid Gemini cache hits.

**Why this is better:**
- **Zero Quote Hallucinations:** The quote always comes from the source file, never the LLM's memory.
- **Cheaper & Faster:** Reduces output tokens by ~50% (citations were the bulk of the output).
- **No Token Limits:** Prevents "max output" cutoffs on massive entities like *Sleeper Service*.

> 💡 **Why does chunking run during synthesis?**  
> The synthesizer builds a `chunk_map` by loading all chapter files. This map is used to:
> 1. Validate that citation quotes exist in source text
> 2. Fill citation metadata (`source_file`, chapter info)
> 3. Look up fact IDs when rebuilding the citations array
>
> This adds ~10 seconds of startup overhead. Future optimization could cache the chunk_map or only load chapters for the book being synthesized.


### Fact ID System

**Format:** `{chunk_id}#{sequence}` (e.g., `excession_ch014#003`)

**At extraction time:**
- Each fact is assigned a unique ID based on its source chapter and sequence
- Quotes are validated against source text before saving
- Hallucinated quotes are rejected and logged

**At synthesis time:**
- Fact IDs are shown in the prompt: `• [excession_ch001#003] Fact: ...`
- LLM is instructed to use exact IDs in article text and citations array
- Validation checks that all referenced `[fact_id]` markers exist in the citations array
- IDs are normalized to lowercase for consistent matching
 - Facts are only included in the prompt if their quotes appear verbatim in chapter text
 - Entity matching is name/alias overlap against extractions (case-insensitive)

### Citation Validation Flow

```
Prompt: "[excession_ch014#003] Fact: The ship was massive..."
                            ↓
LLM writes: "The ship was massive [excession_ch014#003]."
                            ↓
Validation: Is excession_ch014#003 in our fact_id_map? ✓
            Quote matches pre-validated quote? ✓
                            ↓
Citation accepted → Build from pre-validated data
```

**Key benefits:**
- No more citation number mismatches
- Traceability: fact ID links directly to source file
- Pre-validation: quotes verified at extraction, not synthesis
- Deduplication: same quote across entities only cited once
> - Complex entities require synthesizing disparate information
>
> **Current mitigations:**
> - Retry up to N times (configurable)
> - Skip entities after max retries
>
> **Potential future fixes:**
> - Chunk large entities into sections
> - Add explicit citation count to prompt
> - Post-process to renumber citations
>
> **NO looser validation** — We require 100% valid citations. Never accept hallucinated sources.

**Book-scoped synthesis behavior:**
- If a `{type}_{book}.json` file is missing, synthesis falls back to the global `{type}.json` and filters entities by `appears_in` for that book.
- When `--force` and `--book` are both used, stale article deletion only applies to articles that belong to that book.
- `--entity` matches either the canonical name or any alias (case-insensitive).

**Output:**  
`data/articles/{type}/{slug}.json`

**Example article structure:**
```json
{
  "entity_name": "Dajeil Gelian",
  "summary": "A Culture citizen who...",
  "sections": [
    {"title": "Background", "content": "..."},
    {"title": "Relationship with Genar-Hofoen", "content": "..."}
  ],
  "citations": [
    {"id": 1, "quote": "She had been pregnant...", "book": "excession", ...}
  ],
  "related_entities": ["Genar-Hofoen", "Sleeper Service", ...]
}
```

---

## Stage 4: RENDER 🌐

**What happens:**  
Turn JSON articles into a browseable HTML website.

**The process:**

1. **Build entity lookup** — Map all entity names/aliases to their URLs (for cross-linking)
2. **Apply crown jewels** — Copy any hand-written JSON from `crown_jewels/` into `data/articles/` to override LLM output
3. **Render HTML** from Jinja templates
4. **Copy assets** — CSS, fonts, images
5. **Generate indexes** — Book pages, entity type pages
6. **Generate Search Index** — `search_index.json` for client-side search
7. **Generate Graph** — `graph_data.json` and `graph.html` for relationship visualization

**Output:**  
`wiki/` — Static HTML site you can serve locally or deploy

**Features:**
- Clickable citations (scroll to source quote)
- Cross-entity links (character can link to ship they're on)
- Automatic in-article autolinks (first mention per block, avoids self-links)
- Citation display numbers are mapped from fact IDs (renderer moves citations after punctuation and anchors use normalized IDs)
- **Interactive Graph**: Visual exploration of entity relationships
- **Instant Search**: Client-side search across all entities
- Book-organized navigation
- Prominence-based sorting (computed at render time from chapter coverage + citation + cross-reference counts)
- **Apocrypha rendering**: Markdown files in `books/Apocrypha/` are rendered directly to HTML (bypasses the entity extraction pipeline)
- **Entity images**: If `data/images/{entity_type}/{slug}.png` exists, displays portrait in article header
- **Renderer details**:
  - Autolink regex is built from entity names + aliases (skips very short names and common English words from `data/common_words.txt`), links only first mention per text block, and avoids matching inside HTML tags
  - Entity lookup resolves URLs by slug, canonical name, and aliases (templates can use `entity_link`)
  - Legacy integer citations like `[1,2]` are still supported
  - Search index truncates summaries to 150 chars and caps aliases at 5
  - Graph data uses `related_entities` to emit edges; nodes include `type`, `url`, `prominence`, and `books`
  - Book rendering writes `wiki/index.html` plus per-book indexes and `_book-only/` debug pages; falls back to `wiki/old_index.html` if no books
  - Crown jewel overrides require `entity_type` in JSON and are copied before rendering

---

## Stage 5: IMAGE 🎨 (Optional)

**What happens:**  
Generate portrait images for entities using Gemini's native image generation (gemini-3-pro-image-preview).

**The process (Curator Architecture):**

1. **Description Extraction (Pass 1)**
   - loads merged entity's `extraction_refs` (up to 10 chunks)
   - For each chunk, asks LLM: "Find the best physical description of {entity_name}"
   - **Hallucination check**: Validates each extracted quote exists in source text
   - Rejected quotes logged as warnings

2. **Curator Selection (Pass 2)**
   - If multiple valid descriptions found, LLM picks the most representative one
   - Prefers stable appearances over temporary disguises
   - Logs reasoning: "Candidate 3 has most detailed physical features..."

3. **Prompt Generation**
   - Converts selected quote into image generation prompt
   - Adds Culture-universe sci-fi aesthetic, cinematic lighting
   - Ship classes (GSV, GCU, ROU, LOU, MSV, GCV) have predefined prompts, including `xGSV/xGCU/xROU` prefixes

4. **Image Generation**
   - Calls `gemini-3-pro-image-preview` via `generate_content()` with `response_modalities: ["IMAGE", "TEXT"]`
   - Saves to `data/images/{entity_type}/{slug}.png`
   - Ships without a found quote can still use the class-based prompt fallback

**CLI Usage:**
```bash
# Generate image for a specific entity
python3 run.py --stage image --entity "Dajeil Gelian"

# Generate for all major entities in a book
python3 run.py --stage image --book excession --min-citations 10

# Test with a ship class prompt
python3 run.py --stage image --ship-class GSV --dry-run

# Dry run (no API calls)
python3 run.py --stage image --entity "Dajeil Gelian" --dry-run
```

**Key features:**
- **Quote validation**: Only verbatim-verified descriptions used
- **Source traceability**: Logs which chunk file the quote came from
- **Skip existing**: Won't regenerate images that already exist
- **Ship class shortcuts**: GSV/GCU/ROU/etc. have curated sci-fi prompts

**Supporting tools:**
- `extract_descriptions.py` — standalone CLI to extract visual description quotes for a single entity or all “major” entities in a book (min citation threshold), using `extraction_refs` to load source chunks and a JSON-returning prompt. Useful for testing description extraction without running full image generation.
- `generate_image_prompt.py` — standalone CLI that turns description quotes into structured prompt JSON (image_prompt/negative_prompt/style_tags); supports ship-class shortcuts (GSV/GCU/ROU/etc.), dry runs, and batch input from `extract_descriptions.py` output.

**Output:**  
`data/images/{entity_type}/{slug}.png` → Copied to `wiki/images/` during render

---

## Stage 6: CROSS-BOOK 🔗

**What happens:**  
Find entities appearing in multiple books and create unified articles with facts from all appearances.

**The process:**

1. **Find cross-book entities** (`--stage cross`)
   - Scans all `{entity_type}_{book}.json` files
   - Matches on canonical name (case-insensitive)
   - Collects aliases but **does not use them for matching yet** (future LLM validation)
   - `GENERIC_ALIAS_BLACKLIST` only gates which aliases are considered valid
   - Only entities in 2+ books qualify
   - Book slugs outside the publication list are included after the ordered set

2. **Merge entity data**
   - Combines `extraction_refs`, `aliases`, `information[]` from all books
   - Orders by publication date (Consider Phlebas 1987 → Hydrogen Sonata 2012)
   - Deduplicates facts by fact_id
   - Uses the first book in publication order as the base entity
   - Copies extra fields from the base entity when present (`species`, `affiliation`, `class`, `type`, `status`, `category`)
   - Outputs are sorted by number of books in `appears_in` (descending)

3. **Synthesize cross-book articles** (`--book cross-book`)
   - Loads extractions from ALL books
   - Creates unified articles with multi-book citations

4. **Render with priority** (automatic)
   - If entity appears in 2+ books, cross-book article is rendered to main path
   - Book-only version goes to `_book-only/` subfolder for debugging

**Example workflow:**
```bash
# Step 1: Merge cross-book entities
python3 run.py --stage cross --force

# Step 2: Synthesize cross-book articles
python3 run.py --stage synthesize --book cross-book

# Step 3: Render (cross-book priority automatic)
python3 run.py --stage render
```

**Output files:**
- `data/entities/{type}_cross-book.json` — Merged entity data
- `data/articles/{type}/*.json` — Articles with `appears_in: ["book1", "book2", ...]`
- `wiki/books/{book}/{type}/_book-only/*.html` — Debug versions

**Generic Alias Blacklist:**  
These aliases are ignored during cross-book matching to prevent false merges:
- "the woman", "the man", "the human", "the drone", "the ship"
- "he", "she", "they", "the captain", "the ambassador"
- Numbers: "one", "two", "three"

**Code:** `src/cross_book_merger.py`

---

## 🔧 The MCP Server

A separate component that exposes the wiki to Claude Desktop:

- Loads all synthesized articles into memory
- Provides tools: `search_wiki`, `get_entity`, `get_relationships`, etc.
- Enables Claude to answer questions about the Culture using your wiki

---

## 📁 Data Flow

```
books/{book}/              ← Raw chapter text files (md/html)
    ↓
data/extractions/          ← Per-chapter, per-type JSON extractions
    ↓
data/entities/             ← Merged entity lists (one per type, or {type}_{book}.json for book-specific)
    ↓
data/articles/             ← Synthesized wiki articles
    ↓
data/images/               ← Generated entity portraits (optional, --stage image)
    ↓
wiki/                      ← Rendered HTML website (images copied to wiki/images/)
```

---

## 🎯 Key Design Principles

1. **Verbatim citations required**  
   Every fact must have a quote from the source text. No hallucinated information.

2. **Idempotent operations**  
   Running a stage twice produces the same result. Cached outputs are skipped.

3. **Entity types are processed independently**  
   Characters, ships, locations, etc. each have their own extraction prompts and merge logic.

4. **Graceful handling of LLM failures**  
   Retries on API errors. Falls back to cached data when possible.

---

## 💰 Cost Breakdown (per novel)

| Stage | API Calls | Est. Cost |
|-------|-----------|-----------|
| Extract | ~100 chapters × 6 types = 600 calls | ~$3-5 |
| Merge (dedup) | ~10 batches × 6 types = 60 calls | ~$0.50 |
| Synthesize | ~1000 entities | ~$5-10 |
| Image (optional) | ~50 major entities × 3 calls each | ~$2-5 |
| **Total** | | **~$10-20 per novel** |

---

*Last updated: January 2, 2026*
## 💻 CLI Usage

### Setup
0. **Set API key** (required for extract/synthesize)
   Edit `.env` (repo root) and set:
   ```bash
   GEMINI_API_KEY=your_key_here
   ```

### Common Workflows

**Full pipeline in one go (all stages):**
```bash
python3 run.py --stage all
```

**Clean derived outputs (optional):**
```bash
# Full rebuild (clears all derived data + wiki):
python3 run.py --stage clean --clean all --yes

# Only clear Gemini cache (force new API calls):
python3 run.py --stage clean --clean cache --yes

# Only clear rendered site outputs:
python3 run.py --stage clean --clean output --yes
```

### Stage-by-Stage

1. **Extraction (API calls)**
   ```bash
   # Quick prompt preview (no API calls)
   python3 run.py --stage extract --book excession --dry-run
   
   # Small test run (first 2 chapters, one entity type)
   python3 run.py --stage extract --book excession --entity-type characters --limit 2
   
   # Full extraction for a book
   python3 run.py --stage extract --book excession
   ```

2. **Merge (Local + Optional LLM)**
   ```bash
   # Merge all extractions across books
   python3 run.py --stage merge --model gemini-2.5-flash-lite-preview-09-2025

   # Skip LLM deduplication (faster)
   python3 run.py --stage merge --skip-dedup

   # Merge for a specific book only (creates {entity_type}_{book_slug}.json)
   python3 run.py --stage merge --book surface_detail
   ```
   
   > ⚠️ **Note:** When running a book through the pipeline for the first time, use `--force`.
   > ```bash
   > python3 run.py --stage merge --book matter --force
   > ```


3. **Synthesis (API calls)**
   ```bash
   # Synthesize articles (skips existing unless --force)
   python3 run.py --stage synthesize

   # With specific settings
   python3 run.py --stage synthesize --workers 8 --max-retries 5 --model gemini-2.5-flash-lite-preview-09-2025

   # Synthesize for a specific book only (uses book-specific entity file if exists)
   python3 run.py --stage synthesize --book surface_detail
   ```

4. **Render (Local)**
   ```bash
   python3 run.py --stage render
   ```

5. **Serve**
   ```bash
   cd wiki && python3 -m http.server 8000
   ```
### Reference: CLI Flags

- `--force`: Regenerate outputs even if they already exist.
- `--entity`: Focus on a single named entity (synthesis/image).
- `--entity-type`: Focus on a single category (characters, ships_and_minds, etc.).
- `--dry-run`: Print the first extract prompt and skip API calls.
- `--limit N`: Process only N items (speed up testing).
- `--workers N`: Set parallel workers (default: 32).
- `--max-retries N`: Set max synthesis retries on citation failures (default: 10).
- `--book`: Limit processing to a specific book (works for chunk/extract/merge/synthesize).
- `--skip-dedup`: Skip LLM deduplication during merge.
- `--use-cache`: Enable LLM response caching (default: OFF). Cache is model-specific.
- `--clean`: For clean stage, select output targets (`all`, `cache`, `extractions`, `entities`, `articles`, `wiki`, `chunks`, `output`).
- `--yes`: For clean stage, skip confirmation prompt.
- `--model`: Override the default model for the stage.
  - Synthesis defaults to `gemini-3-flash-preview` (better prose)
  - Extract/Merge default to `gemini-2.5-flash-lite` (no hallucinations)
- `--ship-class`: For image stage, generate a ship class test image (GSV/GCU/ROU/etc.).
- `--min-citations N`: For image stage, minimum citations to treat an entity as "major".

### Debugging & Verification

**Citation verification (API calls):**
```bash
# Verify fact-quote alignment using LLM
python3 citation_checker.py --entity-type characters --limit 5

# Check a specific article
python3 citation_checker.py --article dajeil-gelian

# Dry run
python3 citation_checker.py --dry-run

# Verify/Fix extraction aliases (hallucination check)
python3 verify_extraction_aliases.py --book excession

# Dry-run with verbose logging (auto-discovers books if --book omitted)
python3 verify_extraction_aliases.py --dry-run --verbose
```

**View compiled entity data:**
```bash
python3 show_entity_data.py characters "Dajeil Gelian"

# With full synthesis prompt
python3 show_entity_data.py characters Dajeil --full-prompt
```

**Gemini model listing:**
```bash
# Lists available Gemini models via GeminiClient
python3 inspect_genai.py
```

**Manual LLM deduplication:**
```bash
# Preview duplicates
python3 deduplicate_entities.py --entity-type events --dry-run

# Apply deduplication
python3 deduplicate_entities.py --entity-type events --apply
```
Notes:
- The script runs an alias pre-filter that loads per-entity facts from `extraction_refs` and surfaces only name/alias-related facts to the LLM.
- When applied, deleted names are added as aliases on the kept entity.
- The same module also provides `run_garbage_filter()` and `run_cross_entity_dedup()` helpers (used by merge) for LLM garbage cleanup and removing leaked ships from other types.


### MCP Server (Claude Desktop)

The Culture Wiki can be accessed from Claude Desktop via MCP. See `MCP_SETUP.md` for configuration instructions.

**Test locally:**
```bash
source venv/bin/activate && python3 mcp_server.py
```

**Available tools:**
- `search_wiki`: Full-text search across all entities
- `get_entity`: Get full article about a specific entity
- `list_entities`: Browse entities by type
- `get_relationships`: See entity connections
- `get_citation`: Get a specific source quote by ID
- `get_wiki_stats`: Overview of wiki coverage
- `list_apocrypha`: List available "Apocrypha" documents (raw markdown)
- `get_apocrypha`: Get the content of a specific "Apocrypha" document

---

## 🤖 Documentation Audit Agent

We use a self-correcting workflow to keep this documentation up to date.

**Script**: `.agent/scripts/audit_docs.py`  
**How it works**:
1. Checks `git status` for modified/added/deleted Python files.
2. Spawns a **Codex subagent** for each changed file.
3. The subagent reads the code and this documentation.
4. If the documentation is missing new features or architectural changes, the subagent updates it.
5. If a file was deleted, the subagent removes references to it.

**Usage**:
```bash
# Audit only changed files (FAST)
python3 .agent/scripts/audit_docs.py

# Force audit of entire codebase (SLOW)
python3 .agent/scripts/audit_docs.py --all
```
