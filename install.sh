#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing system dependencies ==="
sudo apt install -y maim xclip python3-pip python3-gi python3-gi-cairo gir1.2-gtk-3.0

echo "=== Setting up Python virtual environment ==="
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 -m venv "$SCRIPT_DIR/.venv"
"$SCRIPT_DIR/.venv/bin/pip" install pynput requests python-dotenv

# Symlink system GTK3 (gi) into venv
GI_PATH=$(python3 -c "import gi; print(gi.__path__[0])")
ln -sf "$GI_PATH" "$SCRIPT_DIR/.venv/lib/python3.12/site-packages/gi"

echo "=== Done! ==="
echo "1. Edit .env and set your API key"
echo "2. Run: .venv/bin/python3 screen_answer.py"
