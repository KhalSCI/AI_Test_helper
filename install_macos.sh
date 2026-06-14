#!/usr/bin/env bash
set -euo pipefail

# Native macOS app: screenshots use the built-in `screencapture`, clipboard uses
# NSPasteboard, and the overlay is a native AppKit panel (PyObjC). No Homebrew
# packages are required — only Python dependencies.

echo "=== Setting up Python virtual environment ==="
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 -m venv "$SCRIPT_DIR/.venv"
"$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip
"$SCRIPT_DIR/.venv/bin/pip" install \
    pyobjc-framework-Cocoa \
    pynput \
    requests \
    python-dotenv

echo
echo "=== Done! ==="
echo "1. Copy .env.example to .env and set OPENROUTER_API_KEY"
echo "2. Grant permissions (System Settings → Privacy & Security):"
echo "     • Accessibility    — for global keyboard shortcuts (pynput)"
echo "     • Screen Recording — for screenshots (screencapture)"
echo "   Add your terminal app (or the Python binary) to BOTH lists, then restart it."
echo "3. Run: .venv/bin/python3 screen_answer.py"
echo
echo "Skróty:"
echo "  Ctrl+Shift+Q — zaznacz region    Ctrl+Shift+M — przełącz model"
echo "  Ctrl+Shift+W — cały ekran        Ctrl+Shift+H — schowaj/pokaż nakładkę"
