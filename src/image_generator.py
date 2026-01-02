"""Image generation pipeline for Culture Wiki entities."""

import base64
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from . import config
from .gemini_client import GeminiClient
from .utils import read_json, write_json, slugify
from .validator import load_chunk_text, normalize_for_match

logger = logging.getLogger(__name__)


# Prompts
DESCRIPTION_PROMPT = """You are analyzing text from Iain M. Banks' Culture series.

Find the BEST physical/visual description of "{entity_name}" in this text.

Look for:
- Physical appearance (height, build, features, skin, hair, eyes)
- Clothing or distinctive attire
- For ships/drones: shape, size, color, distinctive features
- For locations: architecture, landscape, atmosphere

Return a JSON object:
{{
  "has_description": true/false,
  "description_quote": "exact quote from text describing appearance (or null)",
  "visual_details": ["list", "of", "key", "visual", "elements"]
}}

If there is NO physical description, return:
{{"has_description": false, "description_quote": null, "visual_details": []}}

TEXT TO ANALYZE:
{text}
"""
SELECTION_PROMPT = """You are analyzing visual descriptions of {entity_name} from Iain M. Banks' Culture series.

Here are {count} candidate descriptions extracted from different chapters:

{candidates_text}

Task:
1. Identify the most detailed, iconic, and representative description of the character.
2. Avoid descriptions that seem transitional (e.g. temporary disguises) unless that's their main look.
3. Culture characters often change bodies; prefer the form they are most commonly associated with in this book.

Return the INDEX (0-based) of the best description.

JSON Response:
{{
  "best_index": 0,
  "reasoning": "This description contains the most specific details about facial features/attire..."
}}
"""

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
{{
  "image_prompt": "your detailed prompt here",
  "negative_prompt": "things to avoid",
  "style_tags": ["tag1", "tag2"]
}}
"""

SHIP_CLASS_PROMPTS = {
    "GSV": "Massive Culture General Systems Vehicle, kilometers long, elegant curved hull, living ecosystem inside, docked smaller ships, orbital view, science fiction, detailed digital art",
    "GCU": "Culture General Contact Unit spacecraft, sleek diplomatic vessel, advanced technology, orbiting alien world, science fiction illustration",
    "ROU": "Culture Rapid Offensive Unit warship, aggressive angular design, weapons systems visible, military vessel, dark space background, science fiction",
    "LOU": "Culture Limited Offensive Unit, compact warship, tactical vessel, science fiction military spacecraft",
    "MSV": "Culture Medium Systems Vehicle, mid-size spacecraft, elegant design, orbital habitat, science fiction",
    "GCV": "Culture General Contact Vehicle, diplomatic spacecraft, sleek design, science fiction illustration",
}


def get_ship_class(entity_name: str) -> Optional[str]:
    """Extract ship class prefix from name."""
    prefixes = ["GSV", "GCU", "ROU", "LOU", "MSV", "GCV", "xGSV", "xGCU", "xROU"]
    name_upper = entity_name.upper()
    for prefix in prefixes:
        # Check start of string or surrounded by spaces
        if name_upper.startswith(prefix + " ") or f" {prefix} " in name_upper:
            return prefix.lstrip("x")
    return None


class ImageGenerator:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.client = None
        if not dry_run:
            self.client = GeminiClient()
            self.client.preflight()
            # Ensure output directory exists
            config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            for et in config.ENTITY_TYPES:
                (config.IMAGES_DIR / et).mkdir(exist_ok=True)

    def extract_description(self, entity_name: str, extraction_refs: List[str]) -> Optional[str]:
        """Extract visual description from source text."""
        if not extraction_refs:
            return None

        # Load chunks (we only need a few good ones)
        # Limit to 10 references to get better coverage for curation
        refs_to_check = extraction_refs[:10]
        
        candidates = []

        for ref in refs_to_check:
            # Extract book slug from ref
            parts = ref.split("_")
            book_slug = parts[0]
            
            chunk_text = load_chunk_text(ref, book_slug)
            if not chunk_text:
                continue

            # Skip small chunks
            if len(chunk_text) < 500:
                continue

            prompt = DESCRIPTION_PROMPT.format(
                entity_name=entity_name, 
                text=chunk_text[:30000] # Context limit
            )

            if self.dry_run:
                logger.info(f"[DRY RUN] Would extract description from {ref}")
                continue

            try:
                response = self.client.client.models.generate_content(
                    model=config.GEMINI_MODEL, # Use cheaper model for extraction
                    contents=prompt,
                    config={"response_mime_type": "application/json"}
                )
                result = json.loads(response.text)
                
                if result.get("has_description") and result.get("description_quote"):
                    # Validate quote exists in text
                    quote = result["description_quote"]
                    normalized_quote = normalize_for_match(quote)
                    normalized_text = normalize_for_match(chunk_text)
                    
                    if normalized_quote in normalized_text:
                        candidates.append((ref, quote))  # Store (source_ref, quote) tuple
                    else:
                        logger.warning(f"Hallucinated quote rejected for {entity_name}")
                        
            except Exception as e:
                logger.error(f"Error extracting description for {entity_name}: {e}")

        if not candidates:
            return None

        logger.info(f"Curator found {len(candidates)} valid description candidates")
        # Select the best one using Curator
        selected = self.select_best_description(entity_name, candidates)
        return selected

    def select_best_description(self, entity_name: str, candidates: List[tuple]) -> Optional[str]:
        """Use LLM to select the most suitable description from candidates.
        
        Args:
            candidates: List of (source_ref, quote) tuples
        """
        if not candidates:
            return None
        if len(candidates) == 1:
            ref, quote = candidates[0]
            logger.info(f"Single candidate from {ref}")
            logger.info(f"Source quote: {quote}")
            return quote
            
        # Build text showing only quotes (not refs) to LLM
        candidates_text = ""
        for i, (ref, quote) in enumerate(candidates):
            candidates_text += f"\n--- CANDIDATE {i} ---\n{quote}\n"
            
        prompt = SELECTION_PROMPT.format(
            entity_name=entity_name,
            count=len(candidates),
            candidates_text=candidates_text
        )
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Would select best description from {len(candidates)} candidates")
            return candidates[0][1]  # Return quote from first tuple
            
        try:
            response = self.client.client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            result = json.loads(response.text)
            best_idx = result.get("best_index", 0)
            
            if 0 <= best_idx < len(candidates):
                ref, quote = candidates[best_idx]
                logger.info(f"Curator selected candidate {best_idx}: {result.get('reasoning')}")
                logger.info(f"Source file: {ref}.md")
                logger.info(f"Source quote: {quote}")
                return quote
            else:
                logger.warning(f"Invalid selection index {best_idx}, defaulting to 0")
                return candidates[0][1]
        except Exception as e:
            logger.error(f"Error selecting description: {e}")
            return candidates[0][1]

    def generate_image_prompt(self, entity_name: str, entity_type: str, description: str) -> Optional[str]:
        """Convert description into image generation prompt."""
        
        # Check for ship class shortcut
        if entity_type == "ships_and_minds":
            ship_class = get_ship_class(entity_name)
            if ship_class and ship_class in SHIP_CLASS_PROMPTS:
                logger.info(f"Using predefined prompt for {ship_class} class")
                return SHIP_CLASS_PROMPTS[ship_class]

        prompt_text = IMAGE_PROMPT_TEMPLATE.format(
            entity_name=entity_name,
            entity_type=entity_type,
            description=description
        )

        if self.dry_run:
            logger.info(f"[DRY RUN] Would generate image prompt from: {description[:50]}...")
            return "dry_run_prompt"

        try:
            response = self.client.client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt_text,
                config={"response_mime_type": "application/json"}
            )
            result = json.loads(response.text)
            image_prompt = result.get("image_prompt")
            logger.info(f"Generated image prompt: {image_prompt}")
            return image_prompt
        except Exception as e:
            logger.error(f"Error generating image prompt: {e}")
            return None

    def generate_image(self, entity_name: str, entity_type: str, prompt: str) -> bool:
        """Call Nano Banana Pro API to generate image."""
        if not prompt:
            return False

        slug = slugify(entity_name)
        output_path = config.IMAGES_DIR / entity_type / f"{slug}.png"
        
        if output_path.exists():
            logger.info(f"Image already exists for {entity_name}, skipping")
            return True

        if self.dry_run:
            logger.info(f"[DRY RUN] Would generate image for {entity_name} with prompt: {prompt}")
            return True

        try:
            logger.info(f"Generating image for {entity_name} with {config.GEMINI_IMAGE_MODEL}...")
            
            # Use generate_content for Gemini image models (gemini-3-pro-image-preview)
            # rather than generate_images which is for Imagen models
            response = self.client.client.models.generate_content(
                model=config.GEMINI_IMAGE_MODEL,
                contents=prompt,
                config={
                    "response_modalities": ["IMAGE", "TEXT"],
                }
            )
            
            # Extract image from response
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        image_bytes = part.inline_data.data
                        with open(output_path, "wb") as f:
                            f.write(image_bytes)
                        logger.info(f"Saved image to {output_path}")
                        return True
            
            logger.error("No image generated in response")
            return False
                
        except Exception as e:
            logger.error(f"Error generating image: {e}")
            return False

    def process_entity(self, entity: dict, entity_type: str) -> bool:
        """Run full image pipeline for a single entity."""
        name = entity.get("canonical_name")
        refs = entity.get("extraction_refs", [])
        
        logger.info(f"Processing image for {name}...")
        
        # 1. Extract Description
        description = self.extract_description(name, refs)
        
        # Fallback for ships if no description found, check class
        if not description and entity_type == "ships_and_minds":
            if get_ship_class(name):
                description = "Ship class fallback"
        
        if not description:
            logger.warning(f"No visual description found for {name}")
            return False
            
        # 2. Generate Prompt
        prompt = self.generate_image_prompt(name, entity_type, description)
        if not prompt:
            return False
            
        # 3. Generate Image
        return self.generate_image(name, entity_type, prompt)


def generate_images(
    entity_name: str = None,
    book_slug: str = None,
    entity_type: str = None,
    ship_class: str = None,
    min_citations: int = 10,
    dry_run: bool = False
):
    """Main entry point for image generation stage."""
    generator = ImageGenerator(dry_run=dry_run)
    
    # Mode 1: Ship Class Test
    if ship_class:
        if not ship_class in SHIP_CLASS_PROMPTS:
            logger.error(f"Unknown ship class: {ship_class}")
            return
        
        prompt = SHIP_CLASS_PROMPTS[ship_class]
        dummy_name = f"Test {ship_class}"
        generator.generate_image(dummy_name, "ships_and_minds", prompt)
        return

    # Mode 2: Single Entity
    if entity_name:
        # Find entity data
        found = False
        search_types = [entity_type] if entity_type else config.ENTITY_TYPES
        
        for et in search_types:
            # Try merged files
            # Note: We really should look in all files, but let's try book-specific first if we can guess
            # For now, just scan the main merged files or recent book files
            files = list(config.ENTITIES_DIR.glob(f"{et}*.json"))
            for f in files:
                data = read_json(f)
                for entity in data.get("entities", []):
                    # Check canonical name
                    if entity.get("canonical_name", "").lower() == entity_name.lower():
                        generator.process_entity(entity, et)
                        found = True
                        break
                    # Check aliases
                    aliases = [a.lower() for a in entity.get("aliases", []) if a]
                    if entity_name.lower() in aliases:
                        generator.process_entity(entity, et)
                        found = True
                        break
                if found: break
            if found: break
            
        if not found:
            logger.error(f"Entity not found: {entity_name}")
        return

    # Mode 3: Batch Process Book
    if book_slug:
        logger.info(f"Processing major entities for {book_slug} (min citations: {min_citations})")
        normalized_book = slugify(book_slug).replace("-", "_")
        
        for et in config.ENTITY_TYPES:
            # Look for book-specific merged file
            # e.g. characters_excession.json
            files = list(config.ENTITIES_DIR.glob(f"{et}*{normalized_book}*.json"))
            if not files:
                continue
                
            for f in files:
                data = read_json(f)
                entities = data.get("entities", [])
                
                # Filter for major entities
                major_entities = [
                    e for e in entities 
                    if len(e.get("extraction_refs", [])) >= min_citations
                ]
                
                logger.info(f"Found {len(major_entities)} major entities in {f.name}")
                
                for entity in major_entities:
                    generator.process_entity(entity, et)
