#!/usr/bin/env bash
# launch.sh — Launches Instagram Digest local dashboard and opens the default browser.
# Single-instance: if the dashboard is already serving on :8080, just open it
# instead of starting a second server (a second server would fail to bind and
# leave the browser pointed at a stale instance).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8080
BASE_URL="http://127.0.0.1:${PORT}"
LOCK_FILE="/tmp/instagram-digest-launch.lock"

open_browser() {
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "${BASE_URL}/dashboard"
    elif command -v google-chrome >/dev/null 2>&1; then
        google-chrome "${BASE_URL}/dashboard"
    fi
}

server_up() {
    # --noproxy: localhost health checks must never go through a proxy.
    command -v curl >/dev/null 2>&1 &&
        curl -sf --noproxy '*' --max-time 2 "${BASE_URL}/api/sync-status" >/dev/null 2>&1
}

# Another copy already serving? Just open it — never start a second server.
if server_up; then
    open_browser
    exit 0
fi

# Serialize concurrent launches (e.g. rapid double-click): the loser waits for
# the winner's server to answer, then opens the browser and exits.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    for _ in $(seq 1 20); do
        if server_up; then break; fi
        sleep 0.5
    done
    open_browser
    exit 0
fi

# Activate virtual environment if present
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# Open the browser once the server accepts connections (not a fixed sleep).
(
    for _ in $(seq 1 30); do
        if server_up; then break; fi
        sleep 0.5
    done
    open_browser
) &

# Run local dashboard server (inherits fd 9, so the lock is held until it exits).
exec python3 "$SCRIPT_DIR/main.py" --serve
