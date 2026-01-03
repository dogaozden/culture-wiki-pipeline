
import zipfile
import shutil
import os
import re
from pathlib import Path

books_to_convert = [
    "books/Look to Windward/Look to Windward.epub",
    "books/The Hydrogen Sonata/The Hydrogen Sonata.epub",
    "books/Inversions/Inversions.epub"
]

def convert_epub(epub_path_str):
    epub_path = Path(epub_path_str)
    if not epub_path.exists():
        print(f"File not found: {epub_path}")
        return

    book_dir = epub_path.parent
    print(f"Converting {epub_path.name} in {book_dir}...")
    
    # Extract to a temp dir inside the book dir
    temp_extract_dir = book_dir / "temp_extracted"
    if temp_extract_dir.exists():
        shutil.rmtree(temp_extract_dir)
    temp_extract_dir.mkdir()
    
    try:
        with zipfile.ZipFile(epub_path, 'r') as zip_ref:
            zip_ref.extractall(temp_extract_dir)
            
        # Find all HTML files
        html_files = list(temp_extract_dir.rglob("*.html")) + list(temp_extract_dir.rglob("*.xhtml"))
        
        # Filter for likely content chapters (optional, but good to avoid spurious files)
        # For now, we take everything that looks like an html file.
        # But we need to be careful about conflicting names if we flatten.
        
        count = 0
        for html_file in html_files:
            # We want to move this to book_dir.
            # We should preserve the name if possible, or prefix it.
            # To ensure sorting works, we rely on the existing filename numbering.
            
            # Skip likely junk
            if html_file.name.lower() in ["toc.html", "nav.html", "content.opf", "toc.ncx"]:
                continue
                
            dest_name = html_file.name
            if destination_exists := (book_dir / dest_name).exists():
                print(f"  Skipping {dest_name}, already exists.")
                continue
            
            shutil.move(str(html_file), str(book_dir / dest_name))
            count += 1
            
        print(f"  Moved {count} HTML files to {book_dir}")
        
    finally:
        # Cleanup temp dir
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)

for book in books_to_convert:
    convert_epub(book)
