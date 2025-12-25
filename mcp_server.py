#!/usr/bin/env python3
"""
Culture Wiki MCP Server

Exposes the Culture Wiki as tools for Claude Desktop.
"""

import json
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("culture-wiki")

# Paths
PROJECT_ROOT = Path(__file__).parent
ARTICLES_DIR = PROJECT_ROOT / "data" / "articles"
ENTITIES_DIR = PROJECT_ROOT / "data" / "entities"

# In-memory cache
_articles: dict[str, list[dict]] = {}  # type -> [articles]
_search_index: list[dict] = []  # flat list for searching


def _load_articles() -> None:
    """Load all articles into memory on startup."""
    global _articles, _search_index

    if _articles:  # Already loaded
        return

    for type_dir in ARTICLES_DIR.iterdir():
        if not type_dir.is_dir():
            continue

        entity_type = type_dir.name
        _articles[entity_type] = []

        for article_file in type_dir.glob("*.json"):
            try:
                with open(article_file, "r", encoding="utf-8") as f:
                    article = json.load(f)
                    _articles[entity_type].append(article)

                    # Add to search index
                    _search_index.append({
                        "name": article.get("entity_name", ""),
                        "type": entity_type,
                        "slug": article.get("slug", ""),
                        "aliases": article.get("aliases", []),
                        "summary": article.get("summary", ""),
                        "content": _extract_full_text(article),
                        "prominence_score": article.get("prominence_score", 0),
                    })
            except Exception as e:
                print(f"Error loading {article_file}: {e}")

    # Sort each type by prominence
    for entity_type in _articles:
        _articles[entity_type].sort(
            key=lambda a: a.get("prominence_score", 0),
            reverse=True
        )


def _extract_full_text(article: dict) -> str:
    """Extract all searchable text from an article."""
    parts = [
        article.get("entity_name", ""),
        article.get("summary", ""),
    ]
    parts.extend(article.get("aliases", []))

    for section in article.get("sections", []):
        parts.append(section.get("title", ""))
        parts.append(section.get("content", ""))

    return " ".join(parts).lower()


def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching."""
    return re.sub(r"[^a-z0-9\s]", "", text.lower())


def _search(query: str, limit: int = 10) -> list[dict]:
    """Search articles by query."""
    _load_articles()

    query_normalized = _normalize(query)
    query_words = query_normalized.split()
    results = []

    for item in _search_index:
        # Check name match (highest priority)
        name_normalized = _normalize(item["name"])
        if query_normalized in name_normalized or name_normalized in query_normalized:
            results.append((100, item))
            continue

        # Check alias match
        alias_match = False
        for alias in item["aliases"]:
            if query_normalized in _normalize(alias):
                results.append((80, item))
                alias_match = True
                break
        if alias_match:
            continue

        # Check content match
        content = item["content"]
        word_matches = sum(1 for word in query_words if word in content)
        if word_matches > 0:
            score = (word_matches / len(query_words)) * 50 + item["prominence_score"] / 10
            results.append((score, item))

    # Sort by score descending
    results.sort(key=lambda x: x[0], reverse=True)

    return [
        {"name": r[1]["name"], "type": r[1]["type"], "slug": r[1]["slug"], "summary": r[1]["summary"][:200]}
        for r in results[:limit]
    ]


def _get_article(name: str, entity_type: str | None = None) -> dict | None:
    """Get a specific article by name or slug."""
    _load_articles()

    name_normalized = _normalize(name)

    types_to_search = [entity_type] if entity_type else _articles.keys()

    for etype in types_to_search:
        if etype not in _articles:
            continue

        for article in _articles[etype]:
            # Check name
            if _normalize(article.get("entity_name", "")) == name_normalized:
                return article

            # Check slug
            if article.get("slug", "") == name_normalized.replace(" ", "-"):
                return article

            # Check aliases
            for alias in article.get("aliases", []):
                if _normalize(alias) == name_normalized:
                    return article

    return None


def _format_article(article: dict, include_citations: bool = True) -> str:
    """Format an article for display."""
    parts = []

    # Header
    parts.append(f"# {article['entity_name']}")
    parts.append(f"*Type: {article['entity_type']}*")

    if article.get("aliases"):
        parts.append(f"*Also known as: {', '.join(article['aliases'])}*")

    if article.get("appears_in"):
        parts.append(f"*Appears in: {', '.join(article['appears_in'])}*")

    parts.append("")

    # Summary
    parts.append(article.get("summary", ""))
    parts.append("")

    # Sections
    for section in article.get("sections", []):
        parts.append(f"## {section['title']}")
        parts.append(section.get("content", ""))
        parts.append("")

    # Related entities
    if article.get("related_entities"):
        parts.append("## Related Entities")
        parts.append(", ".join(article["related_entities"]))
        parts.append("")

    # Citations
    if include_citations and article.get("citations"):
        parts.append("## Sources")
        for cit in article["citations"][:10]:  # Limit to first 10 to save space
            parts.append(f"[{cit['id']}] {cit['book']}, {cit.get('chapter', 'Unknown')}: \"{cit['quote'][:100]}...\"")

    return "\n".join(parts)


# ============== MCP Tools ==============

@mcp.tool()
def search_wiki(query: str, limit: int = 10) -> str:
    """
    Search the Culture Wiki for entities matching a query.

    Args:
        query: Search term (character name, ship name, location, etc.)
        limit: Maximum number of results to return (default 10)

    Returns:
        List of matching entities with names, types, and summaries
    """
    results = _search(query, limit)

    if not results:
        return f"No results found for '{query}'"

    output = [f"Found {len(results)} results for '{query}':\n"]
    for r in results:
        output.append(f"- **{r['name']}** ({r['type']})")
        output.append(f"  {r['summary']}")
        output.append("")

    return "\n".join(output)


@mcp.tool()
def get_entity(name: str, entity_type: str | None = None) -> str:
    """
    Get detailed information about a specific entity from the Culture Wiki.

    Args:
        name: The name of the entity (e.g., "Dajeil Gelian", "Sleeper Service")
        entity_type: Optional type filter (characters, ships_and_minds, locations, technology, factions, events)

    Returns:
        Full article about the entity including summary, sections, and citations
    """
    article = _get_article(name, entity_type)

    if not article:
        # Try a search instead
        results = _search(name, 5)
        if results:
            suggestions = ", ".join(r["name"] for r in results)
            return f"Entity '{name}' not found. Did you mean: {suggestions}?"
        return f"Entity '{name}' not found in the Culture Wiki."

    return _format_article(article)


@mcp.tool()
def list_entities(entity_type: str, book: str | None = None, limit: int = 20) -> str:
    """
    List entities of a given type, sorted by prominence.

    Args:
        entity_type: Type of entity (characters, ships_and_minds, locations, technology, factions, events)
        book: Optional book filter (e.g., "excession")
        limit: Maximum number of entities to return (default 20)

    Returns:
        List of entity names with brief summaries
    """
    _load_articles()

    if entity_type not in _articles:
        available = ", ".join(_articles.keys())
        return f"Unknown entity type '{entity_type}'. Available types: {available}"

    articles = _articles[entity_type]

    # Filter by book if specified
    if book:
        book_normalized = book.lower()
        articles = [a for a in articles if book_normalized in [b.lower() for b in a.get("appears_in", [])]]

    if not articles:
        return f"No {entity_type} found" + (f" in {book}" if book else "")

    output = [f"## {entity_type.replace('_', ' ').title()} ({len(articles)} total)\n"]

    for article in articles[:limit]:
        name = article.get("entity_name", "Unknown")
        summary = article.get("summary", "")[:150]
        score = article.get("prominence_score", 0)
        output.append(f"- **{name}** (prominence: {score})")
        output.append(f"  {summary}...")
        output.append("")

    if len(articles) > limit:
        output.append(f"*...and {len(articles) - limit} more*")

    return "\n".join(output)


@mcp.tool()
def get_relationships(entity_name: str) -> str:
    """
    Get the relationships and connections for a specific entity.

    Args:
        entity_name: The name of the entity to look up relationships for

    Returns:
        List of related entities and their relationship to the queried entity
    """
    article = _get_article(entity_name)

    if not article:
        return f"Entity '{entity_name}' not found."

    related = article.get("related_entities", [])

    if not related:
        return f"{article['entity_name']} has no documented relationships."

    output = [f"## Relationships for {article['entity_name']}\n"]
    output.append(f"Connected to {len(related)} entities:\n")

    for rel in related:
        # Try to get more info about the related entity
        rel_article = _get_article(rel)
        if rel_article:
            output.append(f"- **{rel}** ({rel_article['entity_type']})")
            output.append(f"  {rel_article.get('summary', '')[:100]}...")
        else:
            output.append(f"- **{rel}**")
        output.append("")

    return "\n".join(output)


@mcp.tool()
def get_random_entity(entity_type: str | None = None) -> str:
    """
    Get a random entity from the Culture Wiki for serendipitous discovery.

    Args:
        entity_type: Optional type filter (characters, ships_and_minds, locations, technology, factions, events)

    Returns:
        Full article about a random entity
    """
    import random

    _load_articles()

    if entity_type:
        if entity_type not in _articles:
            available = ", ".join(_articles.keys())
            return f"Unknown entity type '{entity_type}'. Available types: {available}"
        pool = _articles[entity_type]
    else:
        pool = [a for articles in _articles.values() for a in articles]

    if not pool:
        return "No entities found in the wiki."

    article = random.choice(pool)
    return _format_article(article, include_citations=False)


@mcp.tool()
def get_wiki_stats() -> str:
    """
    Get statistics about the Culture Wiki.

    Returns:
        Overview of the wiki including entity counts by type and book coverage
    """
    _load_articles()

    output = ["# Culture Wiki Statistics\n"]

    total = 0
    books = set()

    for entity_type, articles in _articles.items():
        count = len(articles)
        total += count
        output.append(f"- **{entity_type.replace('_', ' ').title()}**: {count} entities")

        for article in articles:
            books.update(article.get("appears_in", []))

    output.insert(1, f"**Total entities**: {total}\n")
    output.insert(2, f"**Books covered**: {', '.join(sorted(books))}\n")

    # Top entities by prominence
    all_articles = [a for articles in _articles.values() for a in articles]
    all_articles.sort(key=lambda a: a.get("prominence_score", 0), reverse=True)

    output.append("\n## Top 10 Entities by Prominence")
    for article in all_articles[:10]:
        output.append(f"- {article['entity_name']} ({article['entity_type']}): {article.get('prominence_score', 0)}")

    return "\n".join(output)


@mcp.tool()
def get_quote(entity_name: str, quote_index: int | None = None) -> str:
    """
    Get a specific quote or all quotes about an entity.

    Args:
        entity_name: The name of the entity
        quote_index: Optional specific quote number (1-indexed). If not provided, returns all quotes.

    Returns:
        Quote(s) from the source text about this entity
    """
    article = _get_article(entity_name)

    if not article:
        return f"Entity '{entity_name}' not found."

    citations = article.get("citations", [])

    if not citations:
        return f"No quotes found for {article['entity_name']}."

    if quote_index is not None:
        if quote_index < 1 or quote_index > len(citations):
            return f"Quote index {quote_index} out of range. This entity has {len(citations)} quotes."

        cit = citations[quote_index - 1]
        return f"**Quote [{cit['id']}]** from {cit['book']}, {cit.get('chapter', 'Unknown')}:\n\n\"{cit['quote']}\""

    output = [f"# Quotes about {article['entity_name']}\n"]
    output.append(f"*{len(citations)} quotes from the source text*\n")

    for cit in citations:
        output.append(f"**[{cit['id']}]** {cit['book']}, {cit.get('chapter', 'Unknown')}:")
        output.append(f"\"{cit['quote']}\"\n")

    return "\n".join(output)


@mcp.tool()
def get_citation(entity_name: str, citation_id: int) -> str:
    """
    Get a specific citation by ID from an entity's article.
    
    Args:
        entity_name: The name of the entity
        citation_id: The citation number (e.g., 1, 2, 3)
    
    Returns:
        The full citation with verbatim quote and source info
    """
    article = _get_article(entity_name)
    
    if not article:
        return f"Entity '{entity_name}' not found."
    
    citations = article.get("citations", [])
    
    if not citations:
        return f"No citations found for {article['entity_name']}."
    
    # Find citation by ID
    for cit in citations:
        if cit.get("id") == citation_id:
            output = [
                f"# Citation [{citation_id}] for {article['entity_name']}\n",
                f"**Source**: {cit['book']}",
                f"**Chapter**: {cit.get('chapter', 'Unknown')}",
                f"**Source File**: {cit.get('source_file', 'N/A')}",
                f"**Chunk ID**: {cit.get('chunk_id', 'N/A')}\n",
                f"## Verbatim Quote\n",
                f"> \"{cit['quote']}\""
            ]
            return "\n".join(output)
    
    return f"Citation [{citation_id}] not found for {article['entity_name']}. Available IDs: {[c['id'] for c in citations[:20]]}"


# Run the server
if __name__ == "__main__":
    mcp.run()
