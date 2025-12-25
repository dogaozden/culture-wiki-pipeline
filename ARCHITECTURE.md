# Culture Wiki — Architecture Overview

*How we turn novels into a wiki, step by step.*

---

## 🔄 The Pipeline

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  EXTRACT    │ →  │    MERGE    │ →  │  SYNTHESIZE │ →  │   RENDER    │
│(per chapter)│    │ (combine)   │    │ (write)     │    │ (HTML)      │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

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
   Currently picks the longest name. This can cause issues (see Known Issues below).

3. **LLM Deduplication (runs AFTER deterministic)**  
   Send entity batches to Gemini asking: "Are any of these the same thing?"  
   Catches semantic duplicates the Python code misses.

> ⚠️ **Known Issue: Wrong Canonical Name**  
> If a minor alias gets more extraction refs, it may become the canonical name.
> Example: "Quietly Confident" (old ship name mentioned once) became canonical instead of "Sleeper Service" (used throughout the book).
> 
> **Proposed fix:** Use prominence scores or mention frequency to pick canonical name.

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

---

## Stage 3: SYNTHESIS ✍️

**What happens:**  
Write wiki articles for each entity using all extracted facts.

**The process:**

1. **Gather all facts** from every chapter where the entity appears
2. **Build a prompt** containing:
   - Entity name and aliases
   - All extracted facts with their source quotes
   - Instructions for wiki-style writing
3. **Ask Gemini** to write a cohesive article using these facts
4. **Validate citations** — Every claim must trace back to a source quote
5. **Retry if needed** — If Gemini invents citations, we retry (up to 10x)

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
