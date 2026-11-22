# Culture Wiki Generator

Generate a comprehensive, citation-backed wiki for a novel series using LLM-assisted extraction. Built around Iain M. Banks' Culture novels.

> **No book text is included in this repository.** Bring your own legally owned books (epub converted to markdown chapters). The `sample/` folder holds a tiny made-up story so you can try the offline stages.

## Quick Start

1. **Setup**
   ```bash
   pip install -r requirements.txt
   export GEMINI_API_KEY="your-api-key-here"
   ```

2. **Prepare Books**
   
   Extract chapters from your epub and convert to markdown:
   ```bash
   # Extract HTML chapters from epub (using Calibre or similar)
   # Then convert to markdown:
   mkdir books/excession
   for f in htmls/*.html; do markitdown "$f" > "books/excession/$(basename "$f" .html).md"; done
   ```
   
   Each book should be a folder containing one `.md` file per chapter.

3. **Run the Pipeline**
   ```bash
   python run.py --stage all
   ```

4. **View the Wiki**
   ```bash
   cd wiki && python -m http.server 8000
   # Open http://localhost:8000
   ```

## Try it without any books

```bash
pip install -r requirements.txt
sample/run_sample.sh /tmp/culture-wiki-sample
cd /tmp/culture-wiki-sample/wiki && python -m http.server 8000
```

This chunks the sample story, shows the first extraction prompt (`--dry-run`, no API call) and renders pre-made sample articles. Extraction, merging and synthesis need a `GEMINI_API_KEY` and your own books.

## Project Structure

```
culture-wiki/
├── books/              # Input: One folder per book
│   └── excession/
│       ├── Excession_split_006.md
│       ├── Excession_split_007.md
│       └── ...
├── data/               # Intermediate processing data
├── wiki/               # Output: Generated HTML website
├── src/                # Python source code
├── templates/          # Jinja2 HTML templates (provided)
└── docs/
    └── SPEC.md         # Full technical specification
```

## Pipeline Stages

1. **Chunk**: Load .md chapter files from book folders
2. **Extract**: Use Gemini to extract entities (characters, ships, locations, etc.)
3. **Merge**: Deduplicate entities across books
4. **Synthesize**: Generate wiki articles with citations
5. **Render**: Convert JSON articles to HTML

## Citation System

Every fact in the wiki includes a citation linking back to the source text. Hover over any `[N]` marker to see the original quote. This serves as both a UX feature and a hallucination check.

## Cost Estimate

With Gemini 3 Flash pricing ($0.50/1M input, $3/1M output):
- ~$1-2 per book
- Full Culture series: ~$12-15

---

Built with ◈ for the Culture
