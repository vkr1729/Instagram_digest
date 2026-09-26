#!/usr/bin/env bash
# ==============================================================================
# run_friday_overnight.sh — Friday 18:00 evening chain:
#   1. Instagram Digest weekly sync (run_weekly.sh) — runs first so failures
#      surface before bed (~21:30); TubeLM is near-flawless so it goes second.
#   2. TubeLM weekly sync (~/.tubelm/run_weekly.sh)
#   3. System poweroff once both are done (whatever their exit codes)
#
# Triggered by the friday-overnight systemd user timer (Persistent: a missed
# Friday 18:00 fires on next login/boot). Both pipelines are independent, so
# TubeLM still runs even if the digest fails — the night shouldn't be wasted
# on one failure. Shutdown happens only when the chain finishes before 06:00;
# morning catch-up runs leave the machine on.
#
# Start-of-run Chrome check: pops a desktop OK dialog (10 min timeout,
# auto-continues) so you can verify instagram.com is logged in while awake.
# Unattended/catch-up runs auto-continue without blocking forever.
# ==============================================================================
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$APP_DIR/logs/friday_overnight.log"
mkdir -p "$APP_DIR/logs"

# Quiet-ops hygiene: cap log growth (no logrotate dependency).
if [ -f "$LOG_FILE" ] && [ "$(stat -c %s "$LOG_FILE")" -gt 10485760 ]; then
    tail -c 5242880 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"; }

prompt_chrome_check() {
    log "Chrome login check: open Chrome, verify instagram.com is logged in, leave browser open."
    notify-send "Instagram Digest" "Friday run starting: open Chrome, check instagram.com is logged in, then press OK." 2>/dev/null || true
    # NOTE: zenity --question on this machine has no --timeout flag, so the
    # 10-minute auto-continue is enforced with coreutils `timeout` (exit 124
    # on timeout). Any non-OK outcome proceeds — the job must never hang
    # overnight waiting for a click.
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
        log "Waiting for Chrome confirmation (OK = continue now, auto-continues after 10 min)..."
        if timeout 600 zenity --question --title="Instagram Digest" --text="Friday run starting.\n\n1. Open Chrome\n2. Check instagram.com is logged in\n3. Leave browser open\n\nPress OK to continue now. Auto-continues after 10 min." 2>/dev/null; then
            log "Chrome check confirmed by user."
        else
            rc=$?
            log "Chrome check auto-continued (rc=$rc: 124 = 10-min timeout, 1 = cancel — proceeding)."
        fi
    else
        log "No desktop popup available (headless/catch-up) — proceeding without confirmation."
    fi
}

# Guard: start on Friday evening (normal path) or anywhere over the weekend
# (Persistent catch-up after a missed Friday evening). Outside that window the
# trigger is stale — exit quietly; the jobs can be started manually.
dow=$(date +%u)
hr=$((10#$(date +%H)))
if { [ "$dow" = "5" ] && [ "$hr" -ge 18 ]; } || [ "$dow" = "6" ] || [ "$dow" = "7" ]; then
    if [ "$dow" != "5" ]; then
        log "Weekend catch-up run (day=$dow hour=$hr) — shutdown will be skipped."
    fi
else
    # B25: a missed week must not vanish with only a log line — notify loudly
    # (best-effort; never fails the script).
    log "Stale trigger outside Fri-eve/weekend window (day=$dow hour=$hr) — alerting and exiting quietly."
    notify-send "Instagram Digest" "Scheduled run skipped (stale trigger) — no digest this week unless run manually." 2>/dev/null || true
    "$APP_DIR/.venv/bin/python" "$APP_DIR/notifier.py" --failure-alert --context "Friday chain skipped (stale trigger day=$dow hour=$hr)" --exit-code 0 >>"$LOG_FILE" 2>&1 || true
    exit 0
fi

log "=== Friday evening chain starting: Instagram Digest first ==="
prompt_chrome_check

set +e
"$APP_DIR/run_weekly.sh"
DIGEST_RC=$?
log "Instagram Digest finished with exit code $DIGEST_RC"

"$HOME/.tubelm/run_weekly.sh"
TUBELM_RC=$?
set -e
log "TubeLM finished with exit code $TUBELM_RC"
log "Chain summary: tubelm=$TUBELM_RC digest=$DIGEST_RC"

# Shutdown only for genuine overnight finishes (before 06:00). A chain that
# runs into the morning — weekend catch-up, or a slow Friday night — leaves
# the machine on so the user finds it awake with results waiting.
now_hr=$((10#$(date +%H)))
if [ "$now_hr" -ge 6 ]; then
    log "Finished at hour=$now_hr (>= 06:00) — leaving machine ON by design."
    if [ "$TUBELM_RC" -eq 0 ] && [ "$DIGEST_RC" -eq 0 ]; then
        exit 0
    fi
    log "Chain summary: tubelm=$TUBELM_RC digest=$DIGEST_RC (non-zero; service will show failed)."
    exit 1
fi

log "Requesting system poweroff..."
if systemctl poweroff >>"$LOG_FILE" 2>&1; then
    exit 0
fi
log "systemctl poweroff failed; trying sudo -n /sbin/poweroff"
if sudo -n /sbin/poweroff >>"$LOG_FILE" 2>&1; then
    exit 0
fi
log "ERROR: poweroff refused (need polkit/sudo rights for poweroff). Machine left ON."
exit 1
