#!/usr/bin/env bash
# launch.sh — Launches Instagram Digest local dashboard and opens the default browser.
# Single-instance: if a CURRENT dashboard server is already on :8080, just open
# it. If the port is held by a stale (pre-dashboard) server, reclaim it from
# our own old processes and start fresh — otherwise the browser lands on the
# stale instance's 404 ("Nothing matches the given URI").
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8080
BASE_URL="http://127.0.0.1:${PORT}"
# Overridable for tests/multi-instance use; defaults to the shared path so
# concurrent desktop launches serialize against each other.
LOCK_FILE="${LAUNCH_LOCK_FILE:-/tmp/instagram-digest-launch.lock}"

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

port_answers() {
    # Any HTTP reply (even a 404) means something occupies the port.
    command -v curl >/dev/null 2>&1 || return 1
    [ "$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 2 "${BASE_URL}/" 2>/dev/null)" != "000" ]
}

our_build() {
    local head
    head=$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null) || { echo "unknown"; return; }
    if [ -n "$(git -C "$SCRIPT_DIR" status --porcelain 2>/dev/null)" ]; then
        head="${head}-dirty"
    fi
    echo "$head"
}

serving_build() {
    command -v curl >/dev/null 2>&1 || return 0
    curl -s --noproxy '*' --max-time 2 "${BASE_URL}/api/sync-status" 2>/dev/null |
        python3 -c "import sys,json;print(json.load(sys.stdin).get('server_build',''))" 2>/dev/null || true
}

# Serialize concurrent launches (e.g. rapid double-click) BEFORE deciding
# anything: the check-reclaim-start sequence must be atomic, otherwise a
# second launcher acting on a stale snapshot can kill the first one's fresh
# server. The loser waits for the winner's server, opens it if it answers,
# and exits.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    for _ in $(seq 1 20); do
        if server_up; then break; fi
        sleep 0.5
    done
    if server_up; then
        open_browser
        exit 0
    fi
    exit 1
fi

# Fresh snapshot under the lock: trust the occupant only when it reports this
# checkout's build (stale servers predate the fingerprint entirely).
EXPECTED_BUILD="$(our_build)"
SERVING_BUILD="$(serving_build)"

if [ -n "$SERVING_BUILD" ] && [ "$SERVING_BUILD" = "$EXPECTED_BUILD" ]; then
    # A current server is already up — just open it, never start a second one.
    open_browser
    exit 0
fi

if port_answers; then
    # Stale (or foreign) occupant. Reclaim the port from our own old servers
    # only: the anchored pattern matches a python interpreter running this
    # project's main.py --serve (absolute argv path included), not editors,
    # greps, or unrelated commands that merely mention those words. A foreign
    # process is left alone and the fresh start below fails loudly into the
    # log instead of silently pointing at the stale 404.
    pkill -f "^[^ ]*python[^ ]* .*/main\.py --serve( |$)" 2>/dev/null || true
    for _ in $(seq 1 20); do
        port_answers || break
        sleep 0.5
    done
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
    if server_up; then
        open_browser
    fi
) &

# Capture server output (bind failures included) — the desktop icon hides it.
mkdir -p "$SCRIPT_DIR/logs"
exec >>"$SCRIPT_DIR/logs/launch.log" 2>&1

# Run local dashboard server (inherits fd 9, so the lock is held until it exits).
exec python3 "$SCRIPT_DIR/main.py" --serve
