"""Render JSON articles to HTML using Jinja templates."""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, List

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import config
from .models import Article
from .utils import ensure_dir, read_json, slugify

logger = logging.getLogger(__name__)

CITATION_PATTERN = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

# Global entity lookup: maps slugified names to (entity_type, slug)
# Built during render_all() and used for cross-entity linking
_ENTITY_LOOKUP: Dict[str, tuple] = {}


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(config.TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["citations"] = _link_citations
    env.filters["slugify"] = slugify
    env.filters["entity_link"] = _entity_link_filter
    return env


def _entity_link_filter(entity_name: str, root_path: str = "") -> str:
    """
    Resolve an entity name to its correct URL path.
    Returns the href path, or empty string if entity not found.
    """
    if not entity_name:
        return ""
    
    # Try multiple variations of the name
    slug = slugify(entity_name)
    lookup_keys = [slug, entity_name.lower().strip()]
    
    for key in lookup_keys:
        if key in _ENTITY_LOOKUP:
            entity_type, entity_slug = _ENTITY_LOOKUP[key]
            return f"{root_path}{entity_type}/{entity_slug}.html"
    
    # Not found - return empty string
    return ""


def _root_path_for(output_path: Path) -> str:
    try:
        rel = output_path.parent.relative_to(config.WIKI_DIR)
    except ValueError:
        return ""
    depth = len(rel.parts)
    return "../" * depth if depth else ""


def _copy_assets() -> None:
    css_src = config.PROJECT_ROOT / "initial files" / "style.css"
    js_src = config.PROJECT_ROOT / "initial files" / "citations.js"
    css_dest = ensure_dir(config.WIKI_DIR / "css") / "style.css"
    js_dest = ensure_dir(config.WIKI_DIR / "js") / "citations.js"

    if css_src.exists():
        shutil.copy2(css_src, css_dest)
    if js_src.exists():
        shutil.copy2(js_src, js_dest)


def _move_citations_after_punctuation(text: str) -> str:
    """Move citation markers from before punctuation to after.
    
    Transforms: 'sentence [5].' -> 'sentence.' [5]
    Handles: .!? followed by citation markers
    """
    if not text:
        return text
    # Pattern: match citation(s) followed by punctuation
    # [5]. or [5,6]. or [5][6]. etc
    pattern = re.compile(r'(\[[\d,\s\]\[]+\])([.!?])')
    return pattern.sub(r'\2 \1', text)


def _link_citations(text: str) -> Markup:
    """Convert [N] markers to clickable citation links."""
    if not text:
        return Markup("")
    
    # First, move citations after punctuation
    text = _move_citations_after_punctuation(text)

    def repl(match: re.Match[str]) -> str:
        numbers = [n.strip() for n in match.group(1).split(",") if n.strip()]
        return ", ".join(
            f'<a href="#cite-{num}" class="citation-link" data-citation-id="{num}">[{num}]</a>'
            for num in numbers
        )

    return Markup(CITATION_PATTERN.sub(repl, text))


def _base_chunk_id(chunk_id: str) -> str:
    if not chunk_id:
        return ""
    return chunk_id.split("_p", 1)[0]


def _compute_prominence_score(article: Article) -> int:
    unique_chapters = {
        _base_chunk_id(c.chunk_id) for c in article.citations if c.chunk_id
    }
    unique_chapter_count = len({c for c in unique_chapters if c})
    citation_count = len(article.citations)
    related_set = {
        name.strip().lower() for name in article.related_entities if name and name.strip()
    }
    cross_reference_count = len(related_set)
    return unique_chapter_count * 10 + citation_count + cross_reference_count


def _load_articles(entity_type: str) -> List[Article]:
    directory = config.ARTICLES_DIR / entity_type
    if not directory.exists():
        return []
    articles: List[Article] = []
    for path in sorted(directory.glob("*.json")):
        article = Article(**read_json(path))
        article.prominence_score = _compute_prominence_score(article)
        articles.append(article)
    articles.sort(
        key=lambda art: (-art.prominence_score, art.entity_name.lower())
    )
    return articles


def render_articles(entity_type: str) -> List[Article]:
    """Render all articles for an entity type."""
    env = _env()
    template = env.get_template("article.html")
    output_dir = ensure_dir(config.WIKI_DIR / entity_type)
    articles = _load_articles(entity_type)

    for article in articles:
        out_path = output_dir / f"{article.slug}.html"
        html = template.render(
            title=article.entity_name,
            article=article,
            entity_type=entity_type,
            root_path=_root_path_for(out_path),
            citations_json=json.dumps(
                [c.model_dump() for c in article.citations], ensure_ascii=False
            ),
        )
        out_path.write_text(html, encoding="utf-8")
    return articles


def _render_articles_with_lookup(entity_type: str, articles: List[Article]) -> None:
    """Render articles with cross-entity linking enabled (uses global lookup)."""
    env = _env()
    template = env.get_template("article.html")
    output_dir = ensure_dir(config.WIKI_DIR / entity_type)

    for article in articles:
        out_path = output_dir / f"{article.slug}.html"
        root = _root_path_for(out_path)
        html = template.render(
            title=article.entity_name,
            article=article,
            entity_type=entity_type,
            root_path=root,
            citations_json=json.dumps(
                [c.model_dump() for c in article.citations], ensure_ascii=False
            ),
        )
        out_path.write_text(html, encoding="utf-8")


def render_type_index(entity_type: str, articles: List[Article]) -> None:
    env = _env()
    template = env.get_template("type_index.html")
    out_path = ensure_dir(config.WIKI_DIR / entity_type) / "index.html"
    html = template.render(
        title=entity_type.replace("_", " ").title(),
        entity_type=entity_type,
        articles=articles,
        root_path=_root_path_for(out_path),
    )
    out_path.write_text(html, encoding="utf-8")


def render_root_index(type_map: Dict[str, List[Article]]) -> None:
    """Legacy: renders the old-style index. Kept for backwards compatibility."""
    env = _env()
    template = env.get_template("index.html")
    out_path = ensure_dir(config.WIKI_DIR) / "old_index.html"
    context = {
        "title": "Culture Wiki",
        "sections": [
            {"type": et, "count": len(arts), "articles": arts}
            for et, arts in type_map.items()
        ],
        "root_path": _root_path_for(out_path),
    }
    html = template.render(**context)
    out_path.write_text(html, encoding="utf-8")


def _get_books() -> List[Dict]:
    """Get list of books from the books directory."""
    books = []
    if not config.BOOKS_DIR.exists():
        return books
    for book_dir in sorted(config.BOOKS_DIR.iterdir()):
        if book_dir.is_dir() and not book_dir.name.startswith("."):
            chapter_count = len(list(book_dir.glob("*.md")))
            books.append({
                "slug": slugify(book_dir.name),
                "title": book_dir.name.replace("-", " ").replace("_", " ").title(),
                "path": book_dir,
                "chapter_count": chapter_count,
            })
    return books


def _filter_articles_by_book(articles: List[Article], book_slug: str) -> List[Article]:
    """Filter articles to only those appearing in a specific book."""
    return [a for a in articles if book_slug in a.appears_in]


def render_books_landing(books: List[Dict], type_map: Dict[str, List[Article]]) -> None:
    """Render the main landing page showing all books."""
    env = _env()
    template = env.get_template("books_index.html")
    out_path = ensure_dir(config.WIKI_DIR) / "index.html"
    
    # Compute stats per book
    for book in books:
        book_slug = book["slug"]
        type_counts = {}
        entity_count = 0
        for et, arts in type_map.items():
            book_arts = _filter_articles_by_book(arts, book_slug)
            if book_arts:
                type_counts[et] = len(book_arts)
                entity_count += len(book_arts)
        book["type_counts"] = type_counts
        book["entity_count"] = entity_count
    
    total_entities = sum(len(arts) for arts in type_map.values())
    
    html = template.render(
        title="Culture Wiki",
        books=books,
        total_books=len(books),
        total_entities=total_entities,
        root_path=_root_path_for(out_path),
    )
    out_path.write_text(html, encoding="utf-8")


def render_book_index(book: Dict, type_map: Dict[str, List[Article]]) -> None:
    """Render the index page for a single book."""
    env = _env()
    template = env.get_template("book_index.html")
    book_slug = book["slug"]
    out_path = ensure_dir(config.WIKI_DIR / "books" / book_slug) / "index.html"
    
    sections = []
    total_entities = 0
    for et, arts in type_map.items():
        book_arts = _filter_articles_by_book(arts, book_slug)
        if book_arts:
            sections.append({
                "type": et,
                "count": len(book_arts),
                "articles": book_arts,
            })
            total_entities += len(book_arts)
    
    html = template.render(
        title=f"{book['title']} — Culture Wiki",
        book_title=book["title"],
        book_slug=book_slug,
        sections=sections,
        total_entities=total_entities,
        chapter_count=book["chapter_count"],
        root_path=_root_path_for(out_path),
    )
    out_path.write_text(html, encoding="utf-8")


def render_book_type_index(book: Dict, entity_type: str, articles: List[Article]) -> None:
    """Render the entity type list for a specific book."""
    env = _env()
    template = env.get_template("book_type_index.html")
    book_slug = book["slug"]
    book_arts = _filter_articles_by_book(articles, book_slug)
    
    if not book_arts:
        return
    
    out_path = ensure_dir(config.WIKI_DIR / "books" / book_slug / entity_type) / "index.html"
    html = template.render(
        title=f"{entity_type.replace('_', ' ').title()} in {book['title']}",
        book_title=book["title"],
        book_slug=book_slug,
        entity_type=entity_type,
        articles=book_arts,
        root_path=_root_path_for(out_path),
    )
    out_path.write_text(html, encoding="utf-8")


def render_book_articles(book: Dict, entity_type: str, articles: List[Article]) -> None:
    """Render individual article pages within a book's section."""
    env = _env()
    template = env.get_template("article.html")
    book_slug = book["slug"]
    book_arts = _filter_articles_by_book(articles, book_slug)
    
    output_dir = ensure_dir(config.WIKI_DIR / "books" / book_slug / entity_type)
    
    for article in book_arts:
        out_path = output_dir / f"{article.slug}.html"
        html = template.render(
            title=article.entity_name,
            article=article,
            entity_type=entity_type,
            root_path=_root_path_for(out_path),
            citations_json=json.dumps(
                [c.model_dump() for c in article.citations], ensure_ascii=False
            ),
        )
        out_path.write_text(html, encoding="utf-8")


def render_all() -> None:
    global _ENTITY_LOOKUP
    _copy_assets()
    
    # First pass: Load all articles and build the global entity lookup
    # This must happen BEFORE rendering so cross-entity links can be resolved
    type_map: Dict[str, List[Article]] = {}
    _ENTITY_LOOKUP = {}  # Reset lookup
    
    for entity_type in config.ENTITY_TYPES:
        articles = _load_articles(entity_type)
        type_map[entity_type] = articles
        
        # Build lookup for this entity type
        for article in articles:
            # Map by slug (primary)
            _ENTITY_LOOKUP[article.slug] = (entity_type, article.slug)
            # Map by entity name (lowercase)
            _ENTITY_LOOKUP[article.entity_name.lower().strip()] = (entity_type, article.slug)
            # Map by aliases
            for alias in article.aliases:
                if alias:
                    alias_slug = slugify(alias)
                    if alias_slug not in _ENTITY_LOOKUP:
                        _ENTITY_LOOKUP[alias_slug] = (entity_type, article.slug)
                    if alias.lower().strip() not in _ENTITY_LOOKUP:
                        _ENTITY_LOOKUP[alias.lower().strip()] = (entity_type, article.slug)
    
    logger.info("Built entity lookup with %d entries for cross-linking", len(_ENTITY_LOOKUP))
    
    # Second pass: Render all articles (now with working cross-entity links)
    for entity_type in config.ENTITY_TYPES:
        articles = type_map[entity_type]
        if articles:
            _render_articles_with_lookup(entity_type, articles)
            render_type_index(entity_type, articles)
    
    # Get books and render book-based structure
    books = _get_books()
    
    if books:
        # Render landing page with book selection
        render_books_landing(books, type_map)
        
        # Render per-book pages
        for book in books:
            render_book_index(book, type_map)
            for entity_type, articles in type_map.items():
                render_book_type_index(book, entity_type, articles)
                render_book_articles(book, entity_type, articles)
    else:
        # Fallback to old index if no books found
        render_root_index(type_map)

