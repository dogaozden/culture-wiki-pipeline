# Culture Wiki — Improvement Roadmap

## ✅ Completed
- [x] Full pipeline working end-to-end on Excession
- [x] Synthesis prompts updated with entity-type-aware guidance
- [x] Prominence-based sorting
- [x] Citation verification (quote existence + fact-quote alignment checks)
- [x] **Fix alias hallucinations** — Tightened extraction prompts + merge filter (Dec 24)
- [x] **Synthesis uses ALL facts** — Removed editorial discretion, 92% fact coverage (Dec 24)
- [x] **Clickable citations** — Smooth scroll + highlight animation (Dec 24)
- [x] **Citation placement** — Post-processor moves citations after punctuation (Dec 24)

## 🔄 In Progress
- [ ] Re-run full pipeline with updated prompts to verify alias fix
  ```bash
  python run.py --clean cache --yes
  python run.py --stage all --book excession --force
  ```

## 📋 Near-Term

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

### Citation Verification (Already Implemented)
The synthesizer validates quotes in two places:
1. `_format_extractions_for_prompt()` — drops facts with quotes not found in source
2. `_synthesize_one()` — drops citations with invalid quotes, skips articles with dangling refs

### Cross-Book Linking Strategy
Current merge algorithm already handles this:
- Entities match by slug (name/alias)
- `appears_in` tracks which books
- `extraction_refs` tracks all source chapters
- Canonical name = longest string among matches

To test: Process a second book with overlapping characters.
