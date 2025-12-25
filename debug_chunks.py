import sys
import os

# Add src to path
sys.path.append(os.path.abspath('src'))

from src import synthesizer
from src import config
import logging

logging.basicConfig(level=logging.INFO)

print(f"BOOKS_DIR: {config.BOOKS_DIR}")
chunk_map = synthesizer._build_chunk_context_map()

print(f"Total keys in chunk_map: {len(chunk_map)}")

target_keys = ['excession_split_010', 'excession_split_044', 'excession_split_037']
for key in target_keys:
    if key in chunk_map:
        print(f"FOUND: {key} -> {chunk_map[key].source_file}")
    else:
        print(f"NOT FOUND: {key}")

# Print sample keys
print("Sample keys:")
for k in list(chunk_map.keys())[:20]:
    print(f"  {k}")
