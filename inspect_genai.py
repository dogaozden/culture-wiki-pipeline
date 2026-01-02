
from src.gemini_client import GeminiClient
import logging

logging.basicConfig(level=logging.INFO)
client = GeminiClient()
try:
    print("Listing models...")
    for m in client.client.models.list():
        print(f"Model: {m.name}")
except Exception as e:
    print(f"Error: {e}")
