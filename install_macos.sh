#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing system dependencies (Homebrew) ==="
brew install maim xclip tesseract tesseract-lang pygobject3 gtk+3

echo "=== Setting up Python virtual environment ==="
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 -m venv "$SCRIPT_DIR/.venv"
"$SCRIPT_DIR/.venv/bin/pip" install pynput requests python-dotenv pytesseract Pillow PyGObject

echo "=== Done! ==="
echo "1. Copy .env.example to .env and set your API key"
echo "2. Run: .venv/bin/python3 screen_answer.py"
