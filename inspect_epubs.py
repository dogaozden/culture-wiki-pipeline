
import zipfile
import os
from pathlib import Path

books = [
    "books/Look to Windward/Look to Windward.epub",
    "books/The Hydrogen Sonata/The Hydrogen Sonata.epub"
]

output_base = Path("temp_epub_extract")
output_base.mkdir(exist_ok=True)

for book_path in books:
    path = Path(book_path)
    if not path.exists():
        print(f"File not found: {path}")
        continue
        
    print(f"Extracting {path}...")
    try:
        with zipfile.ZipFile(path, 'r') as zip_ref:
            extract_dir = output_base / path.stem
            zip_ref.extractall(extract_dir)
            print(f"Extracted to {extract_dir}")
            
            # List some html files found
            html_files = list(extract_dir.rglob("*.html")) + list(extract_dir.rglob("*.xhtml"))
            print(f"Found {len(html_files)} HTML/XHTML files:")
            for f in html_files[:5]:
                print(f"  - {f.relative_to(extract_dir)}")
    except Exception as e:
        print(f"Failed to extract {path}: {e}")
