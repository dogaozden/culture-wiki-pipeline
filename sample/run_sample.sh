#!/usr/bin/env bash
# Runs the offline stages of the pipeline on the made-up sample story in a scratch copy of the repo.
# chunk + extract --dry-run need no API key; render uses the pre-made sample articles.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
work="${1:-$(mktemp -d)}"
mkdir -p "$work"
cp -r "$repo/src" "$repo/templates" "$repo/initial files" "$repo/run.py" "$work/"
mkdir -p "$work/data"
cp "$repo/data/common_words.txt" "$work/data/"
cp -r "$here/books" "$work/books"
cp -r "$here/data/articles" "$work/data/articles"
cd "$work"
python run.py --stage chunk --book the-lamplighters-tide
GEMINI_API_KEY=dry-run python run.py --stage extract --book the-lamplighters-tide --dry-run
python run.py --stage render
echo "Open $work/wiki/index.html (or: cd $work/wiki && python -m http.server 8000)"
