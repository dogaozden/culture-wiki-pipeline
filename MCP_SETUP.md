# Culture Wiki MCP Server Setup

This MCP server exposes the Culture Wiki to Claude Desktop, allowing Claude to search and retrieve information about characters, ships, locations, and more from Iain M. Banks' Culture novels.

## Installation

1. Install the MCP package (if not already done):
   ```bash
   pip install mcp
   ```

2. Verify the server works:
   ```bash
   python3 mcp_server.py
   ```
   (Press Ctrl+C to stop)

## Configure Claude Desktop

Add this to your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "culture-wiki": {
      "command": "/path/to/culture-wiki-pipeline/venv/bin/python3",
      "args": ["/path/to/culture-wiki-pipeline/mcp_server.py"]
    }
  }
}
```

Then restart Claude Desktop.

## Available Tools

Once connected, Claude will have access to these tools:

| Tool | Description |
|------|-------------|
| `search_wiki` | Search for entities by name, alias, or content |
| `get_entity` | Get full article about a specific character, ship, etc. |
| `list_entities` | Browse entities by type (characters, ships_and_minds, etc.) |
| `get_relationships` | See who/what an entity is connected to |
| `get_random_entity` | Serendipitous discovery |
| `get_wiki_stats` | Overview of wiki coverage |
| `get_quote` | Get source quotes about an entity |
| `get_citation` | Get a specific citation by ID with full source info |

## Example Prompts for Claude

Once configured, you can ask Claude things like:

- "Who is Dajeil Gelian?"
- "Tell me about the Sleeper Service"
- "What ships appear in Excession?"
- "What's the relationship between Genar-Hofoen and Dajeil?"
- "Give me a random Culture character"
- "What quotes do we have about the Grey Area?"

## Entity Types

- `characters` — People, drones, Mind avatars
- `ships_and_minds` — Culture ships, Minds, vessels
- `locations` — Orbitals, planets, habitats
- `technology` — Weapons, devices, systems
- `factions` — Civilizations, organizations
- `events` — Battles, incidents, historical events

## Troubleshooting

**Server doesn't start**: Make sure you've run the wiki pipeline first (`python3 run.py --stage all`) to generate article data.

**No entities found**: Check that `data/articles/` contains JSON files.

**Module not found**: Run `pip install mcp` to install the MCP package.
