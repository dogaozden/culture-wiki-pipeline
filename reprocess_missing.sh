#!/bin/bash
# Script to process books that were missing extractions

# Books that need full processing (extract -> merge -> synthesize)
books=(
    "Look to Windward"
    "The Hydrogen Sonata"
)

for book in "${books[@]}"; do
    echo "================================================================================"
    echo "PROCESSING MISSING BOOK: $book"
    echo "================================================================================"
    
    echo "Step 1: Extracting entities..."
    python3 run.py --stage extract --book "$book" --force
    if [ $? -ne 0 ]; then
        echo "Error extracting $book. Skipping subsequent steps."
        continue
    fi

    echo "Step 2: Merging entities..."
    python3 run.py --stage merge --book "$book" --force
    if [ $? -ne 0 ]; then
        echo "Error merging $book. Skipping synthesis."
        continue
    fi
    
    echo "Step 3: Synthesizing articles..."
    python3 run.py --stage synthesize --book "$book" --force
    if [ $? -ne 0 ]; then
        echo "Error synthesizing $book."
    fi
    
    echo "Finished processing $book"
    echo ""
done
