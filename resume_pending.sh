#!/usr/bin/env bash
# =============================================================================
# resume_pending.sh — Resume interrupted Instagram Digest pipelines at login.
#
# Picks up work banked by the crash-safe pipelines and finishes it in the
# background:
#   data/sync_progress_*.json    ->  main.py --sync --deploy
#   data/expand_checkpoint_*.json -> main.py --expand <target> --deploy
#     (target is read from the checkpoint envelope; defaults to 100)
#
# Safety properties (do not weaken without updating the tests in
# tests/test_expand_resume.py and tests/test_sync_resume.py):
#   - Single-flight via flock: a second copy exits immediately.
#   - Never starts while another pipeline is alive (server thread, cron
#     weekly job, or manual CLI) — checked with pgrep before launching.
#   - Never deletes progress itself: only a pipeline that integrates the
#     banked work into the digest clears its own checkpoint, so a failed
#     resume is always retryable (manually or at the next login).
#   - Quiet no-op when nothing is pending (no desktop spam every login).
#
# Install as a login-time systemd user service (run once, as yourself):
#   mkdir -p ~/.config/systemd/user
#   cp ~/Instagram_digest/systemd/instagram-digest-resume.service ~/.config/systemd/user/
#   systemctl --user daemon-reload
#   systemctl --user enable instagram-digest-resume.service
# Test it any time (safe: exits quietly when nothing is pending):
#   ~/Instagram_digest/resume_pending.sh; tail -n 20 ~/Instagram_digest/logs/resume.log
# =============================================================================

set -uo pipefail

APP_DIR="${INSTAGRAM_DIGEST_DIR:-$HOME/Instagram_digest}"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/resume.log"
LOCK_FILE="$APP_DIR/data/.resume.lock"

mkdir -p "$LOG_DIR" "$APP_DIR/data"

log() {
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" >>"$LOG_FILE"
}

notify() {
    notify-send "Instagram Digest" "$1" 2>/dev/null || true
}

# Single-flight: another resume copy is already working.
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    log "Another resume is already running; exiting."
    exit 0
fi

# Never collide with a live pipeline (local-server thread, cron job, manual CLI).
# pgrep below only sees separate CLI processes: pipelines run by the dashboard
# server live in worker threads under `main.py --serve`, so ask the server
# itself first (quiet when it is not running).
if command -v /usr/bin/python3 >/dev/null 2>&1; then
    if http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= /usr/bin/python3 -c "
import json, urllib.request
def get(path):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8080' + path, timeout=3) as r:
            return json.load(r)
    except Exception:
        return {}
s = get('/api/sync-status')
e = get('/api/expand/status')
raise SystemExit(0 if (s.get('is_running') or (e.get('state') or {}).get('is_running')) else 1)
" 2>/dev/null; then
        log "Dashboard server reports a running pipeline; skipping auto-resume."
        exit 0
    fi
fi
# (pgrep patterns are ERE already; it has no -E flag — one would make this
# guard silently always-false.)
# pgrep scans full command lines, so exclude this script and its ancestors: a
# wrapper's own command line may mention these words without running a pipeline.
_pipeline_pids=$(pgrep -f "python.*main\.py --(sync|expand|ad-hoc)|run_weekly\.sh" 2>/dev/null || true)
_exclude=" $$ "
_ppid="$PPID"
while [ -n "$_ppid" ] && [ "$_ppid" != "0" ]; do
    _exclude="$_exclude$_ppid "
    _next=$(ps -o ppid= -p "$_ppid" 2>/dev/null | tr -d '[:space:]') || break
    [ "$_next" = "$_ppid" ] && break # safety against PID cycles
    _ppid="$_next"
done
_busy=0
for _pid in $_pipeline_pids; do
    case "$_exclude" in
        *" $_pid "*) ;;
        *) _busy=1; break ;;
    esac
done
if [ "$_busy" -eq 1 ]; then
    log "A pipeline is already running; skipping auto-resume."
    exit 0
fi

shopt -s nullglob
SYNC_PROGS=("$APP_DIR"/data/sync_progress_*.json)
EXPAND_CKPTS=("$APP_DIR"/data/expand_checkpoint_*.json)
shopt -u nullglob

if [ "${#SYNC_PROGS[@]}" -eq 0 ] && [ "${#EXPAND_CKPTS[@]}" -eq 0 ]; then
    exit 0
fi

log "Auto-resume at login: ${#SYNC_PROGS[@]} sync progress file(s), ${#EXPAND_CKPTS[@]} expand checkpoint(s)."
notify "Resuming interrupted Instagram Digest run in the background…"

# Weekly sync first: it rebuilds the digest the expansion then appends to.
if [ "${#SYNC_PROGS[@]}" -gt 0 ]; then
    log "Resuming weekly sync: main.py --sync --deploy"
    "$APP_DIR/.venv/bin/python" "$APP_DIR/main.py" --sync --deploy >>"$LOG_FILE" 2>&1
    log "Sync resume exited with code $?."
fi

# Then any pending +100 top-ups (each run prunes stale checkpoints itself).
for ckpt in "${EXPAND_CKPTS[@]}"; do
    [ -e "$ckpt" ] || continue
    TARGET=$(/usr/bin/python3 -c \
        "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('target_count', 100) if isinstance(d, dict) else 100)" \
        "$ckpt" 2>/dev/null || echo 100)
    case "$TARGET" in '' | *[!0-9]*) TARGET=100 ;; esac
    [ "$TARGET" -gt 500 ] && TARGET=500
    [ "$TARGET" -lt 1 ] && TARGET=1
    log "Resuming expansion from $(basename "$ckpt"): main.py --expand $TARGET --deploy"
    "$APP_DIR/.venv/bin/python" "$APP_DIR/main.py" --expand "$TARGET" --deploy >>"$LOG_FILE" 2>&1
    log "Expand resume exited with code $?."
done

log "Auto-resume finished."
notify "Instagram Digest resume finished — see logs/resume.log."
