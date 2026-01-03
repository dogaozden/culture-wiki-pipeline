#!/bin/bash
# Script to re-process books affected by the book slug matching bug

# Books to process (all have multi-word titles)
books=(
    "consider-phlebas"
    "look-to-windward"
    "surface-detail"
    "the-hydrogen-sonata"
    "the-player-of-games"
)

for book in "${books[@]}"; do
    echo "================================================================================"
    echo "PROCESSING BOOK: $book"
    echo "================================================================================"
    
    echo "Step 1: Merging entities (with fixed type voting)..."
    python3 run.py --stage merge --book "$book" --force
    if [ $? -ne 0 ]; then
        echo "Error merging $book. Skipping synthesis."
        continue
    fi
    
    echo "Step 2: Synthesizing articles..."
    python3 run.py --stage synthesize --book "$book" --force
    if [ $? -ne 0 ]; then
        echo "Error synthesizing $book."
    fi
    
    echo "Finished processing $book"
    echo ""
done

echo "================================================================================"
echo "All done! Now run: python3 run.py --stage render"
echo "================================================================================"
