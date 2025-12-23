# Culture Wiki Generator — Technical Specification

## Overview

A Python pipeline that processes Iain M. Banks' Culture series novels and generates a comprehensive, citation-backed wiki as a local HTML website. The system uses a MapReduce-style architecture with the Gemini 3 Flash API to extract and synthesize information.

## Core Design Principles

1. **Citation-First**: Every claim in the wiki MUST be traceable to source text. No citation = no inclusion.
2. **Chunk-Based Processing**: Books are split into ~25k token chunks to avoid "lost in the middle" degradation.
3. **Idempotent Operations**: Re-running the pipeline should skip already-processed items.
4. **Incremental Building**: Can add new books without reprocessing everything.

---

## Directory Structure

```
culture-wiki/
├── books/                          # Input: One folder per book, containing .md chapter files
│   └── excession/
│       ├── Excession_split_006.md  # One file per chapter
│       ├── Excession_split_007.md
│       └── ...
│
├── data/
│   ├── chunks/                 # Processed chapter data
│   │   └── {book_slug}/
│   │       ├── chapter_001.json
│   │       ├── chapter_002.json
│   │       └── manifest.json   # Book metadata
│   │
│   ├── extractions/            # Raw LLM extraction results
│   │   ├── characters/
│   │   │   └── {book_slug}/
│   │   │       └── chapter_001_characters.json
│   │   ├── locations/
│   │   ├── ships_and_minds/
│   │   ├── technology/
│   │   ├── factions/
│   │   └── events/
│   │
│   ├── entities/               # Merged entity lists (cross-book)
│   │   ├── characters.json
│   │   ├── locations.json
│   │   └── ...
│   │
│   └── articles/               # Synthesized wiki articles (JSON)
│       ├── characters/
│       │   ├── zakalwe.json
│       │   └── sma.json
│       └── ...
│
├── wiki/                       # Output: Generated HTML website
│   ├── index.html
│   ├── characters/
│   │   ├── index.html
│   │   └── zakalwe.html
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   └── citations.js
│   └── assets/
│
├── src/
│   ├── __init__.py
│   ├── chunker.py              # Load .md chapter files
│   ├── extractor.py            # Chapters → entity extractions
│   ├── merger.py               # Deduplicate entities across books
│   ├── synthesizer.py          # Extractions → wiki articles
│   ├── renderer.py             # JSON articles → HTML
│   ├── gemini_client.py        # API wrapper with retry/caching
│   └── pipeline.py             # Orchestrator
│
├── templates/
│   ├── base.html               # Jinja2 base template
│   ├── article.html
│   └── index.html
│
├── config.py                   # Settings (chunk size, model, etc.)
├── requirements.txt
└── run.py                      # Entry point
```

---

## Data Schemas

### Chunk (data/chunks/{book}/chunk_NNN.json)

```json
{
  "chunk_id": "consider-phlebas_ch03",
  "book_slug": "consider-phlebas",
  "book_title": "Consider Phlebas",
  "chapter_index": 3,
  "chapter_title": "Chapter 3: Clear Air Turbulence",
  "section_type": "narrative",
  "is_split": false,
  "split_part": null,
  "token_count": 28500,
  "text": "The full text of this chapter..."
}
```

### Entity Extraction (data/extractions/{type}/{book}/chunk_NNN_{type}.json)

```json
{
  "chunk_id": "consider-phlebas_ch03",
  "chapter_title": "Chapter 3: Clear Air Turbulence",
  "entity_type": "characters",
  "entities": [
    {
      "name": "Bora Horza Gobuchul",
      "aliases": ["Horza", "the Changer"],
      "information": [
        {
          "fact": "Horza is a Changer, able to physically transform to mimic other humanoids",
          "quote": "He was a Changer, one of the species who could...",
          "location": "Chapter 3, during his escape from the Idirans"
        },
        {
          "fact": "He harbors deep resentment toward the Culture",
          "quote": "The Culture, he thought, with its smug Minds...",
          "location": "Chapter 3, internal monologue while imprisoned"
        }
      ]
    }
  ]
}
```

### Merged Entity (data/entities/{type}.json)

```json
{
  "entities": [
    {
      "canonical_name": "Bora Horza Gobuchul",
      "aliases": ["Horza", "the Changer", "Kraiklyn"],
      "appears_in": ["consider-phlebas"],
      "extraction_refs": [
        "consider-phlebas_001",
        "consider-phlebas_042",
        "consider-phlebas_043"
      ]
    }
  ]
}
```

### Wiki Article (data/articles/{type}/{slug}.json)

```json
{
  "entity_name": "Bora Horza Gobuchul",
  "entity_type": "character",
  "slug": "bora-horza-gobuchul",
  "aliases": ["Horza", "the Changer"],
  "appears_in": ["Consider Phlebas"],
  "summary": "A Changer who...",
  "sections": [
    {
      "title": "Background",
      "content": "Horza was born on... [1] His species possessed the rare ability to... [2]",
      "citations": [1, 2]
    },
    {
      "title": "Role in the Idiran War",
      "content": "During the Idiran-Culture War, Horza sided with... [3]",
      "citations": [3]
    }
  ],
  "citations": [
    {
      "id": 1,
      "book": "Consider Phlebas",
      "chapter": "Chapter 3: Clear Air Turbulence",
      "quote": "He was a Changer, one of the species who could alter their appearance...",
      "chunk_id": "consider-phlebas_ch03"
    },
    {
      "id": 2,
      "book": "Consider Phlebas",
      "chapter": "Chapter 5: The Ends of Invention",
      "quote": "The Changers were a dying race, their homeworld destroyed...",
      "chunk_id": "consider-phlebas_ch05"
    }
  ],
  "related_entities": ["Perosteck Balveda", "The Idirans", "Schar's World"],
  "generated_at": "2024-12-23T10:30:00Z"
}
```

---

## Pipeline Stages

### Stage 1: Load Chapters (`chunker.py`)

**Input**: Folder of `.md` files in `books/{book_name}/` (one file per chapter)  
**Output**: JSON chunks in `data/chunks/{book}/`

Each markdown file is already a chapter. Just load them in order.

```python
from pathlib import Path
import re

def load_chapters(book_dir: Path) -> list[Chapter]:
    """Load chapter markdown files from a book directory."""
    chapters = []
    
    # Sort by the numeric part of filename (e.g., Excession_split_006.md → 6)
    md_files = sorted(
        book_dir.glob("*.md"),
        key=lambda f: int(re.search(r'(\d+)', f.stem).group(1))
    )
    
    for idx, md_file in enumerate(md_files):
        text = md_file.read_text(encoding='utf-8')
        
        # Extract title from first header, or use filename
        title_match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
        title = title_match.group(1) if title_match else f"Chapter {idx + 1}"
        
        # Skip very short files (title pages, copyright, etc.)
        if len(text) < 500:
            continue
        
        chapters.append(Chapter(
            index=len(chapters) + 1,
            title=title,
            text=text,
            token_count=count_tokens(text),
            source_file=md_file.name
        ))
    
    return chapters
```

#### Handling Long Chapters (>50k tokens)

If a chapter exceeds the token limit, split at scene breaks (`***` or `---`):

```python
def split_long_chapter(chapter: Chapter, max_tokens: int = 50000) -> list[Chapter]:
    if chapter.token_count <= max_tokens:
        return [chapter]
    
    # Split at scene breaks
    parts = re.split(r'\n\s*[\*\-_]{3,}\s*\n', chapter.text)
    # ... recombine into chunks under max_tokens
```
5. Classify sections (narrative, appendix, frontmatter, etc.)
6. Write chunk JSON files with chapter metadata

```python
def chunk_book(book_path: Path, max_chapter_tokens: int = 50000) -> list[Chapter]:
    ...
```

### Stage 2: Extraction (`extractor.py`)

**Input**: Chunks from Stage 1  
**Output**: Entity extractions in `data/extractions/{type}/{book}/`

For each entity type, for each chunk:
1. Call Gemini with extraction prompt
2. Parse structured JSON response
3. Save extraction

**Key prompts** (should be in separate prompt files):

```
SYSTEM: You are an expert literary analyst extracting structured information 
from Iain M. Banks' Culture novels. Extract ONLY information explicitly 
stated in the text. Every fact must include a direct quote as evidence.

USER: Extract all CHARACTERS mentioned in this passage. For each character:
- name: The character's primary name
- aliases: Any other names/titles they're called
- information: Array of facts, each with:
  - fact: A single piece of information about them
  - quote: The EXACT text (verbatim) that supports this fact
  - quote_context: Brief description of when/where this appears

Return valid JSON only. If no characters appear, return {"entities": []}.

PASSAGE:
{chunk_text}
```

**Entity Types & Their Extraction Focus**:

| Type | What to Extract |
|------|-----------------|
| `characters` | Names, roles, species, relationships, actions, fates |
| `ships_and_minds` | Ship names, classes, Mind personalities, notable actions |
| `locations` | Orbitals, planets, habitats, specific places within ships |
| `technology` | Devices, weapons, capabilities, how things work |
| `factions` | Civilizations, organizations, their traits and conflicts |
| `events` | Wars, incidents, historical occurrences with dates if given |

### Stage 3: Merging (`merger.py`)

**Input**: All extractions  
**Output**: Deduplicated entity lists in `data/entities/`

Challenge: The same entity appears across books and chunks with different names.
- "Cheradenine Zakalwe" = "Zakalwe" = "Staberinde" (spoiler!)
- Ship names vs. their avatars

Algorithm:
1. Collect all extracted entities of a type
2. Use fuzzy matching + LLM assistance for deduplication
3. Create canonical names with alias lists
4. Track which chunks/books each entity appears in

### Stage 4: Synthesis (`synthesizer.py`)

**Input**: Merged entity list + all relevant extractions  
**Output**: Wiki articles in `data/articles/{type}/`

For each entity:
1. Gather ALL extractions mentioning this entity (across all books/chunks)
2. Call Gemini with synthesis prompt
3. Require inline citations for every claim
4. Validate that all citations trace to actual quotes

**Synthesis Prompt**:

```
SYSTEM: You are writing a wiki article for a Culture series encyclopedia. 
Write in an encyclopedic tone. EVERY factual claim must have an inline 
citation in the format [N] where N is the citation number.

You will be given extracted facts with their source quotes. Use ONLY these 
facts. Do not add any information not present in the extractions.

Structure the article with clear sections. Common sections:
- Summary (2-3 sentences, no spoilers for first appearance)
- Background/Origins
- Appearances (chronological by book)
- Personality/Characteristics
- Relationships
- For ships: Specifications, Notable Actions
- Fate (if known)

USER: Write a wiki article for: {entity_name}
Type: {entity_type}
Appears in: {book_list}

EXTRACTED INFORMATION:
{formatted_extractions}

Return a JSON object with:
- summary: Brief intro
- sections: Array of {title, content} with [N] citations inline
- citations: Array mapping citation numbers to source quotes and books
```

### Stage 5: Rendering (`renderer.py`)

**Input**: JSON articles  
**Output**: HTML files in `wiki/`

1. Load Jinja2 templates
2. For each article, render HTML with:
   - Citation markers as `<cite data-id="N">` elements
   - Full citation data embedded as JSON in a `<script>` tag
3. Generate index pages (main index, per-type indices)
4. Copy CSS/JS assets

---

## Gemini API Client (`gemini_client.py`)

Requirements:
- Rate limiting (respect API limits)
- Exponential backoff on failures
- Response caching (don't re-call for same input)
- Structured output parsing with validation
- Cost tracking (log token usage)

```python
class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-3-flash"):
        ...
    
    def extract(self, prompt: str, chunk: Chunk, schema: dict) -> dict:
        """Call API with structured output, cache result"""
        ...
    
    def synthesize(self, prompt: str, extractions: list) -> dict:
        """Longer-form generation for wiki articles"""
        ...
```

Use Gemini's native JSON mode / structured output when available.

---

## Configuration (`config.py`)

```python
from pathlib import Path

# Paths
BOOKS_DIR = Path("books")           # Input epub files
DATA_DIR = Path("data")
MARKDOWN_DIR = DATA_DIR / "markdown"  # Converted markdown
CHUNKS_DIR = DATA_DIR / "chunks"
EXTRACTIONS_DIR = DATA_DIR / "extractions"
ARTICLES_DIR = DATA_DIR / "articles"
WIKI_DIR = Path("wiki")

# Chapter Detection
MAX_CHAPTER_TOKENS = 50000          # Split chapters larger than this
MIN_CHAPTER_TOKENS = 500            # Warn about chapters smaller than this

# API
GEMINI_MODEL = "gemini-3-flash"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"  # Read from environment
MAX_RETRIES = 3
REQUESTS_PER_MINUTE = 60  # Adjust based on your tier

# Entity types to extract
ENTITY_TYPES = [
    "characters",
    "ships_and_minds", 
    "locations",
    "technology",
    "factions",
    "events",
]

# Processing
PARALLEL_WORKERS = 4  # For async API calls
CACHE_DIR = DATA_DIR / ".cache"
```

---

## Entry Point (`run.py`)

```python
import argparse
from src.pipeline import Pipeline

def main():
    parser = argparse.ArgumentParser(description="Generate Culture Wiki")
    parser.add_argument("--stage", choices=["chunk", "extract", "merge", "synthesize", "render", "all"], default="all")
    parser.add_argument("--book", help="Process specific book only")
    parser.add_argument("--entity-type", help="Process specific entity type only")
    parser.add_argument("--force", action="store_true", help="Reprocess even if cached")
    args = parser.parse_args()
    
    pipeline = Pipeline()
    pipeline.run(stage=args.stage, book=args.book, entity_type=args.entity_type, force=args.force)

if __name__ == "__main__":
    main()
```

---

## HTML/CSS/JS Requirements

See the provided `templates/` directory for the base template and `wiki/css/style.css` and `wiki/js/citations.js` for the citation hover system.

Key features:
- Clean, readable typography (wiki-appropriate)
- Sidebar navigation
- Citation markers `[1]` that show source quote on hover
- Responsive design
- Dark/light mode toggle

---

## Error Handling & Edge Cases

1. **Chunks with no entities**: Normal, just save empty extraction
2. **API failures**: Retry with backoff, log failures, continue with other chunks
3. **Malformed LLM output**: Log and skip, don't crash pipeline
4. **Entity deduplication uncertainty**: Flag for manual review, include both
5. **Missing citations in synthesis**: Reject article, re-prompt with stricter instructions
6. **Very long articles**: Split into sub-pages if needed

---

## Cost Estimation

Rough estimate for one book (~150k tokens):
- Chunking: 0 API cost (local)
- Extraction: 6 chunks × 6 entity types = 36 calls
  - Input: ~25k tokens each = 900k input tokens
  - Output: ~2k tokens each = 72k output tokens
- Synthesis: ~50 entities × 1 call each = 50 calls  
  - Input: ~10k tokens each = 500k input tokens
  - Output: ~2k tokens each = 100k output tokens

Per book: ~1.4M input, ~172k output ≈ $0.70 + $0.52 = **~$1.22 per book**

10 Culture novels ≈ **$12-15 total** (well within your $280 budget)

---

## Future Enhancements (v2)

- Cross-references between wiki articles (auto-link entity mentions)
- Timeline view for events
- Relationship graphs
- Search functionality
- Spoiler tags based on book appearance order
- Multi-book character arcs
