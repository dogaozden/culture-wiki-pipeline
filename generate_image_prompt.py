#!/usr/bin/env python3
"""
Generate image prompts from entity descriptions.

Usage:
  # From description file
  python generate_image_prompt.py --input descriptions.json --output prompts.json
  
  # Single entity inline
  python generate_image_prompt.py --description "A tall woman with dark hair..." --entity "Dajeil Gelian"
"""

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config
from src.utils import read_json, write_json

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Prompt for generating image prompts
IMAGE_PROMPT_TEMPLATE = """You are creating an image generation prompt for an AI art tool.

Entity: {entity_name}
Type: {entity_type}
Description from book: "{description}"

Create a detailed image generation prompt that:
1. Captures the key visual elements from the description
2. Uses "Culture universe" sci-fi aesthetic
3. Is suitable for a character portrait or scene

Art style: Detailed digital illustration, science fiction, cinematic lighting

Return JSON:
```json
{{
  "image_prompt": "your detailed prompt here",
  "negative_prompt": "things to avoid",
  "style_tags": ["tag1", "tag2"]
}}
```
"""

SHIP_CLASS_PROMPTS = {
    "GSV": "Massive Culture General Systems Vehicle, kilometers long, elegant curved hull, living ecosystem inside, docked smaller ships, orbital view, science fiction, detailed digital art",
    "GCU": "Culture General Contact Unit spacecraft, sleek diplomatic vessel, advanced technology, orbiting alien world, science fiction illustration",
    "ROU": "Culture Rapid Offensive Unit warship, aggressive angular design, weapons systems visible, military vessel, dark space background, science fiction",
    "LOU": "Culture Limited Offensive Unit, compact warship, tactical vessel, science fiction military spacecraft",
    "MSV": "Culture Medium Systems Vehicle, mid-size spacecraft, elegant design, orbital habitat, science fiction",
    "GCV": "Culture General Contact Vehicle, diplomatic spacecraft, sleek design, science fiction illustration",
}


def get_ship_class(entity_name: str) -> str | None:
    """Extract ship class prefix from name."""
    prefixes = ["GSV", "GCU", "ROU", "LOU", "MSV", "GCV", "xGSV", "xGCU", "xROU"]
    name_upper = entity_name.upper()
    for prefix in prefixes:
        if name_upper.startswith(prefix + " ") or f" {prefix} " in name_upper:
            return prefix.lstrip("x")
    return None


def generate_prompt_llm(client, entity_name: str, entity_type: str, description: str) -> dict:
    """Use LLM to generate image prompt from description."""
    prompt = IMAGE_PROMPT_TEMPLATE.format(
        entity_name=entity_name,
        entity_type=entity_type,
        description=description
    )
    
    try:
        response = client.client.models.generate_content(
            model=client.model_name,
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return None


def generate_prompt_for_entity(
    entity_name: str,
    entity_type: str,
    description: str,
    client=None,
    dry_run: bool = False
) -> dict:
    """Generate image prompt for an entity."""
    
    # Special handling for ships - use class prompts
    if entity_type == "ships_and_minds":
        ship_class = get_ship_class(entity_name)
        if ship_class and ship_class in SHIP_CLASS_PROMPTS:
            return {
                "entity": entity_name,
                "entity_type": entity_type,
                "ship_class": ship_class,
                "image_prompt": SHIP_CLASS_PROMPTS[ship_class],
                "use_class_image": True
            }
    
    if dry_run:
        logger.info(f"[DRY RUN] Would generate prompt for {entity_name}")
        return {"entity": entity_name, "dry_run": True}
    
    result = generate_prompt_llm(client, entity_name, entity_type, description)
    if result:
        result["entity"] = entity_name
        result["entity_type"] = entity_type
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate image prompts from descriptions")
    parser.add_argument("--input", help="Input JSON file with descriptions")
    parser.add_argument("--description", help="Single description text")
    parser.add_argument("--entity", help="Entity name (required with --description)")
    parser.add_argument("--entity-type", default="characters")
    parser.add_argument("--output", help="Output JSON file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    if not args.input and not args.description:
        parser.error("Must specify --input or --description")
    
    client = None
    if not args.dry_run:
        from src.gemini_client import GeminiClient
        client = GeminiClient()
        client.preflight()
    
    results = []
    
    if args.description:
        result = generate_prompt_for_entity(
            args.entity or "Unknown",
            args.entity_type,
            args.description,
            client=client,
            dry_run=args.dry_run
        )
        if result:
            results.append(result)
            print(json.dumps(result, indent=2))
    
    elif args.input:
        descriptions = read_json(Path(args.input))
        for desc in descriptions:
            if not desc.get("has_description"):
                continue
            result = generate_prompt_for_entity(
                desc["entity"],
                desc.get("entity_type", "characters"),
                desc["description_quote"],
                client=client,
                dry_run=args.dry_run
            )
            if result:
                results.append(result)
    
    if args.output and results:
        write_json(results, Path(args.output))
        logger.info(f"Saved {len(results)} prompts to {args.output}")


if __name__ == "__main__":
    main()
