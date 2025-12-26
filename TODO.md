# Culture Wiki — Improvement Roadmap

## ✅ Completed
- [x] Full pipeline working end-to-end on Excession
- [x] Synthesis prompts updated with entity-type-aware guidance
- [x] Prominence-based sorting
- [x] Citation verification (quote existence + fact-quote alignment checks)
- [x] **Fix alias hallucinations** — Tightened extraction prompts + merge filter (Dec 24)
- [x] **Synthesis uses ALL facts** — Removed editorial discretion (Dec 24)
- [x] **Clickable citations** — Smooth scroll + highlight animation (Dec 24)
- [x] **Citation placement** — Post-processor moves citations after punctuation (Dec 24)
- [x] **Extraction improvements** — Relationships, prominence scoring, longer quotes (Dec 25)
- [x] **Synthesis retry logic** — Auto-retry on citation validation failures, max 10 attempts (Dec 25)
- [x] **CLI flags** — `--workers` and `--max-retries` for runtime config (Dec 25)
- [x] **Full pipeline run** — 123 character articles including Dajeil Gelian (Dec 25)
- [x] **LLM deduplication integrated** — Now runs automatically during merge stage (Dec 25)
  - Added batching (100 entities per batch) to handle large entity lists
  - Catches semantic duplicates like "The Idiran War" = "Idiran War"
  - `--skip-dedup` flag to bypass for faster runs
  - Tested on events: found 62 duplicates in 282 entities
- [x] **Cross-entity linking** — Related entities now correctly link across types (Dec 25)
  - Characters can link to ships/locations/etc in Related section
  - Global entity lookup built during render (2,974 entries)
  - Unlinked entities styled in gray
- [x] **Model refinement** — Stage-specific models: 2.5-lite for extraction (accuracy), 3-flash for synthesis (prose) (Dec 26)
- [x] **Progress bars** — Added `tqdm` to extract, merge, and synthesize stages (Dec 26)
- [x] **Opt-in Cache** — LLM cache is now OFF by default to prevent cross-model contamination (Dec 26)
- [x] **Tolerance validation** — Large entities (80+ facts) allow small % of missing citations (Dec 26)
- [x] **Manual repair workflow** — `dump_synthesis_prompt.py` + `rebuild_citations.py` for massive entities (Dec 26)
- [x] **MCP Server** — Claude Desktop can now browse the wiki via MCP tools (Dec 25)
  - 8 tools: search_wiki, get_entity, list_entities, get_relationships, get_quote, get_citation, get_random_entity, get_wiki_stats
  - Auto-loads 1,229 entities into memory for fast queries
  - See MCP_SETUP.md for Claude Desktop configuration


## 📋 Near-Term

- [ ] **Architecture Refactor: Python-Rebuilt Citations (High Priority)**
  - **Goal:** Stop asking LLM to output massive citation arrays.
  - **New Workflow:**
    1. LLM outputs article text with inline tags only (e.g., `...detected[Excession_split_001#003]`)
    2. Python parses tags, looks up original extraction for quote/metadata
    3. Python constructs the `citations` array deterministically
  - **Benefits:** 30-50% cheaper, zero quote hallucinations, avoids output token limits
  - **Validation:** If LLM outputs a tag ID that doesn't exist in input -> FAIL the article (hallucination check)

- [ ] **Narrative improvements**
  - Sort extractions by chunk ID before synthesis (ensure chronological narrative)
  - Update synthesis prompt to emphasize "Narrative Flow" (tell the story sequentially)

- [ ] **Streaming support**
  - Implement streaming in `gemini_client.py` to prevent timeouts on massive entities (100+ facts)

- [ ] **Apply deduplication to all entity types** and re-synthesize
  - Run `python3 run.py --stage merge --force` to re-merge with deduplication
  - Then `python3 run.py --stage synthesize --force` to regenerate articles
  

- [ ] **Process remaining 9 Culture novels**
  - Consider Phlebas, Player of Games, Use of Weapons, etc.
  - Order: chronological publication or narrative order?
  
- [ ] **Cross-book entity linking**
  - Entities appearing in multiple books merge automatically (slug-based)
  - Add `first_appearance` field for timeline context
  - Test with characters who span novels (e.g., Diziet Sma)

- [ ] **Search functionality**
  - Client-side with lunr.js or fuse.js
  - Index entity names, aliases, summaries

- [ ] **Book selection landing page**
  - Landing screen before main wiki showing available books
  - Filter entities by book until cross-book linking is ready
  - Each book shows entity counts and completion status

## 🔮 Longer-Term
- [ ] **Client-side search**
  - lunr.js or fuse.js for full-text search
  - Index: entity names, aliases, summaries, section content
  - Keyboard shortcut (Cmd+K) for quick access

- [ ] **Incremental updates**
  - Re-extract single chapters without full re-merge
  - Track extraction timestamps for staleness detection
  
- [ ] **Public deployment**
  - GitHub Pages or Netlify
  - CI/CD for automated rebuilds

- [ ] **Relationship visualization**
  - Character relationship graphs (force-directed with D3.js)
  - Interactive: click nodes to navigate to articles
  - Timeline of events across novels
  - Faction hierarchies

- [ ] **Reader contributions**
  - Correction suggestions via GitHub issues
  - Missing entity reports

## 🏗️ Architecture Notes

### LLM Deduplication (NEW)
The merge stage now includes LLM-powered semantic deduplication:
1. After slug-based consolidation, `run_deduplication()` is called
2. Entities are sent to Gemini in batches of 100
3. LLM identifies semantic duplicates (e.g., "The War" = "Culture-Affront War")
4. Duplicates are removed, with deleted names added as aliases to kept entities
5. Skip with `--skip-dedup` flag for faster runs

### Citation Verification & Retry Logic
The synthesizer validates citations and retries on failure:
1. `_format_extractions_for_prompt()` — drops facts with quotes not found in source
2. `_synthesize_one()` — drops citations with invalid quotes
3. **Retry loop** — If LLM references non-existent citation IDs, retries up to 10x (configurable)
4. **Failure logging** — After max retries, logs `[ERROR]` with entity name and missing IDs

### Cross-Book Linking Strategy
Current merge algorithm already handles this:
- Entities match by slug (name/alias)
- `appears_in` tracks which books
- `extraction_refs` tracks all source chapters
- Canonical name = longest string among matches

To test: Process a second book with overlapping characters.

### Cross-Entity Linking (NEW)
Related entities now correctly link across entity types:
1. During `render_all()`, build global lookup mapping names/slugs to (type, slug)
2. Jinja filter `entity_link` resolves entity name to correct URL
3. Template renders clickable links for found entities, gray text for missing
4. Aliases are also indexed for fuzzy matching

### MCP Server (NEW)
The wiki is accessible from Claude Desktop via MCP (`mcp_server.py`):
- Uses FastMCP for simple tool definitions
- Loads all articles into memory on startup
- Full-text search with relevance scoring
- Configure in Claude Desktop via `claude_desktop_config.json`
