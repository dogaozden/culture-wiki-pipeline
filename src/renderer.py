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


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(config.TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["citations"] = _link_citations
    env.filters["slugify"] = slugify
    return env


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


def _link_citations(text: str) -> Markup:
    """Convert [N] markers to clickable citation links."""
    if not text:
        return Markup("")

    def repl(match: re.Match[str]) -> str:
        numbers = [n.strip() for n in match.group(1).split(",") if n.strip()]
        return ", ".join(
            f'<a href="#cite-{num}" class="citation-link" data-citation-id="{num}">[{num}]</a>'
            for num in numbers
        )

    return Markup(CITATION_PATTERN.sub(repl, text))


def _load_articles(entity_type: str) -> List[Article]:
    directory = config.ARTICLES_DIR / entity_type
    if not directory.exists():
        return []
    articles: List[Article] = []
    for path in sorted(directory.glob("*.json")):
        articles.append(Article(**read_json(path)))
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
    env = _env()
    template = env.get_template("index.html")
    out_path = ensure_dir(config.WIKI_DIR) / "index.html"
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


def render_all() -> None:
    _copy_assets()
    type_map: Dict[str, List[Article]] = {}
    for entity_type in config.ENTITY_TYPES:
        articles = render_articles(entity_type)
        if articles:
            render_type_index(entity_type, articles)
        type_map[entity_type] = articles
    render_root_index(type_map)
