# Culture Wiki — Commands & Architecture

*How we turn novels into a wiki, step by step.*

---

## 🔄 The Pipeline

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  EXTRACT    │ →  │    MERGE    │ →  │  SYNTHESIZE │ →  │   RENDER    │
│(per chapter)│    │ (combine)   │    │ (write)     │    │ (HTML)      │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

> [!IMPORTANT]  
> **Model Selection (Dec 2025)**  
> Different stages use different models by default:
>
> | Stage | Model | Why |
> |-------|-------|-----|
> | Extract | `gemini-2.5-flash-lite` | Better instruction following, no semantic hallucinations |
> | Merge | `gemini-2.5-flash-lite` | Same — accurate deduplication |
> | Synthesize | `gemini-3-flash-preview` | **Better prose quality** (auto-configured) |
>
> Testing revealed `gemini-3-flash-preview` causes **semantic hallucinations** during extraction (facts not supported by quotes), but produces better wiki article prose. The pipeline now uses each model where it performs best.

> [!TIP]
> **Other Quality-of-Life Features:**
> - **Progress bars**: All stages show tqdm progress bars
> - **Opt-in cache**: LLM cache is OFF by default. Use `--use-cache` to enable.
> - **Tolerance validation**: Large entities (80+ facts) allow up to 3 missing citations

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

1. **Validate quotes** — Check each quote exists in source text
   - Rejects hallucinated quotes before saving
   - Logged as warnings for debugging

2. **Assign Fact IDs** — Each fact gets unique ID: `{chunk_id}#{seq}`
   - Example: `excession_ch014#003` = 3rd fact from chapter 14
   - IDs are stable across re-runs (deterministic)
   - Used by synthesis for citation validation

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

1. **Deterministic Python matching (FREE)**  
   - Slug-based: "Sleeper Service" from Ch1 + "Sleeper Service" from Ch5 → same entity
   - Alias overlap: If Entity A has alias "X" and Entity B is named "X" → merge
   - Ship prefix stripping: "GCU Grey Area" matches "Grey Area"

2. **Canonical name selection**  
   Picks the name that appears in the **most chapters** (by extraction_refs count).
   This ensures "Sleeper Service" (48 chapters) beats "Quietly Confident" (mentioned once).

3. **LLM Garbage Filter (NEW Dec 2025)**  
   - Removes generic descriptors: "the woman", "Admiral", "Speaker 1"
   - Runs before within-entity dedup
   - Uses 3 Flash by default (much more conservative than 2.5 Flash Lite)

4. **LLM Deduplication**  
   - First runs **alias pre-filter**: asks LLM to identify facts mentioning former names
   - Then runs **main dedup**: compares entities with their alias-related facts
   - Batched processing with retry logic for API failures
   - **Default model: gemini-3-flash-preview** for better accuracy

> ✅ **Fixed: Canonical Name Selection**  
> Previously picked the longest name, causing "Quietly Confident" to be canonical instead of "Sleeper Service".
> Now uses chapter frequency — names appearing in more chapters win.

**Output:**  
`data/entities/{type}.json` — One file per entity type with all merged entities

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
| **Determ** | Deterministic | Ship/event entity removal |
| **Garbage** | LLM | Generic descriptor removal |
| **Dedup** | LLM | Within-entity duplicate removal |
| **Cross** | LLM | Cross-entity ship removal |
| **Final** | — | Entities after all filtering |

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
   - **Validation:** If the LLM uses a tag that doesn't exist in the input -> **REJECT** the article (Hallucination detected).

**Why this is better:**
- **Zero Quote Hallucinations:** The quote always comes from the source file, never the LLM's memory.
- **Cheaper & Faster:** Reduces output tokens by ~50% (citations were the bulk of the output).
- **No Token Limits:** Prevents "max output" cutoffs on massive entities like *Sleeper Service*.

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
2. **Render HTML** from Jinja templates
3. **Copy assets** — CSS, fonts, images
4. **Generate indexes** — Book pages, entity type pages

**Output:**  
`wiki/` — Static HTML site you can serve locally or deploy

**Features:**
- Clickable citations (scroll to source quote)
- Cross-entity links (character can link to ship they're on)
- Book-organized navigation
- Prominence-based sorting

---

## 🔧 The MCP Server

A separate component that exposes the wiki to Claude Desktop:

- Loads all synthesized articles into memory
- Provides tools: `search_wiki`, `get_entity`, `get_relationships`, etc.
- Enables Claude to answer questions about the Culture using your wiki

---

## 📁 Data Flow

```
novels/excession/          ← Raw chapter text files
    ↓
data/extractions/          ← Per-chapter, per-type JSON extractions
    ↓
data/entities/             ← Merged entity lists (one per type)
    ↓
data/articles/             ← Synthesized wiki articles
    ↓
wiki/                      ← Rendered HTML website
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
| **Total** | | **~$10-15 per novel** |

---

*Last updated: December 25, 2025*
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
   ```

3. **Synthesis (API calls)**
   ```bash
   # Synthesize articles (skips existing unless --force)
   python3 run.py --stage synthesize
   
   # With specific settings
   python3 run.py --stage synthesize --workers 8 --max-retries 5 --model gemini-2.5-flash-lite-preview-09-2025
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
- `--entity-type`: Focus on a single category (characters, ships_and_minds, etc.).
- `--limit N`: Process only N items (speed up testing).
- `--workers N`: Set parallel workers (default: 32).
- `--max-retries N`: Set max synthesis retries on citation failures (default: 10).
- `--skip-dedup`: Skip LLM deduplication during merge.
- `--use-cache`: Enable LLM response caching (default: OFF). Cache is model-specific.
- `--model`: Override the default model for the stage.
  - Synthesis defaults to `gemini-3-flash-preview` (better prose)
  - Extract/Merge default to `gemini-2.5-flash-lite` (no hallucinations)

### Debugging & Verification

**Citation verification (API calls):**
```bash
# Verify fact-quote alignment using LLM
python3 citation_checker.py --entity-type characters --limit 5

# Check a specific article
python3 citation_checker.py --article dajeil-gelian

# Dry run
python3 citation_checker.py --dry-run
```

**View compiled entity data:**
```bash
python3 show_entity_data.py characters "Dajeil Gelian"

# With full synthesis prompt
python3 show_entity_data.py characters Dajeil --full-prompt
```

**Manual LLM deduplication:**
```bash
# Preview duplicates
python3 deduplicate_entities.py --entity-type events --dry-run

# Apply deduplication
python3 deduplicate_entities.py --entity-type events --apply
```


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