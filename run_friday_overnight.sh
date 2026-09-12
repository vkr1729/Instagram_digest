#!/usr/bin/env bash
# ==============================================================================
# run_friday_overnight.sh — Friday 22:00 overnight chain:
#   1. TubeLM weekly sync (~/.tubelm/run_weekly.sh)
#   2. Instagram Digest weekly sync (run_weekly.sh)
#   3. System poweroff once both are done (whatever their exit codes)
#
# Triggered by the friday-overnight systemd user timer (Persistent: a missed
# Friday 22:00 fires on next boot). Both pipelines are independent, so the
# digest still runs even if TubeLM fails — the night shouldn't be wasted on
# one failure. Shutdown happens only when the chain finishes before 06:00;
# morning catch-up runs leave the machine on.
# ==============================================================================
set -uo pipefail

APP_DIR="/home/kedarnath-reddy-vallaboina/Instagram_digest"
LOG_FILE="$APP_DIR/logs/friday_overnight.log"
mkdir -p "$APP_DIR/logs"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"; }

# Guard: start on Friday evening (normal path) or anywhere over the weekend
# (Persistent catch-up after a missed Friday night). Outside that window the
# trigger is stale — exit quietly; the jobs can be started manually.
dow=$(date +%u)
hr=$((10#$(date +%H)))
if { [ "$dow" = "5" ] && [ "$hr" -ge 21 ]; } || [ "$dow" = "6" ] || [ "$dow" = "7" ]; then
    if [ "$dow" != "5" ]; then
        log "Weekend catch-up run (day=$dow hour=$hr) — shutdown will be skipped."
    fi
else
    log "Stale trigger outside Fri-eve/weekend window (day=$dow hour=$hr) — exiting quietly."
    exit 0
fi

log "=== Friday overnight chain starting: TubeLM first ==="

set +e
"$HOME/.tubelm/run_weekly.sh"
TUBELM_RC=$?
log "TubeLM finished with exit code $TUBELM_RC"

"$APP_DIR/run_weekly.sh"
DIGEST_RC=$?
set -e
log "Instagram Digest finished with exit code $DIGEST_RC"
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
