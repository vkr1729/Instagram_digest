#!/usr/bin/env bash
# launch.sh — Launches Instagram Digest local dashboard and opens the default browser
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if present
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# Open browser after 1 second in background
(
    sleep 1
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "http://127.0.0.1:8080/"
    elif command -v google-chrome >/dev/null 2>&1; then
        google-chrome "http://127.0.0.1:8080/"
    fi
) &

# Run local dashboard server
exec python3 "$SCRIPT_DIR/main.py" --serve
