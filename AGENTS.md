# Agent Instructions

**Before starting work on this project, read the following context documents:**

1. **[ARCHITECTURE.md](./ARCHITECTURE.md)** — **START HERE.** Human-readable overview of the full pipeline, data flow, and known issues. This is the primary source of truth.
2. **[culture-wiki-design-context.md](./culture-wiki-design-context.md)** — Detailed agent-focused architecture, pipeline stages, design decisions
3. **[culture-wiki-technical-context.md](./culture-wiki-technical-context.md)** — Implementation details, model fields, conventions, config
4. **[commands.txt](./commands.txt)** — Common CLI commands for running the pipeline
5. **[TODO.md](./TODO.md)** — Current roadmap and task list

> ⚠️ **When making architectural changes, always update ARCHITECTURE.md first.** IMPORTANT: This ensures human collaborators stay informed.

## Quick Summary

This is an LLM-powered wiki generator for Iain M. Banks' Culture novels. The pipeline:
1. **Extract** entities from chapter text (Gemini API)
2. **Merge** duplicates across chapters
3. **Synthesize** wiki articles with inline citations
4. **Render** static HTML

Current state: Working end-to-end on "Excession" (first book).

## Key Files

- `src/extractor.py` — Entity extraction prompts (recently improved with relationships, prominence)
- `src/models.py` — Pydantic models (`Entity`, `EntityRelationship`, etc.)
- `src/synthesizer.py` — Article synthesis prompts
- `run.py` — CLI entry point
