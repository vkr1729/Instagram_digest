#!/usr/bin/env bash
# ==============================================================================
# run_weekly.sh — Automated Friday Weekly Sync for Instagram Digest v1.0
# Runs extraction, fair-share viral ranking, media download, R2 upload, and deployment.
# ==============================================================================

set -euo pipefail

APP_DIR="/home/kedarnath-reddy-vallaboina/Instagram_digest"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/weekly_sync.log"

mkdir -p "$LOG_DIR"

echo "=================================================================" >> "$LOG_FILE"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Starting Weekly Friday Instagram Digest..." >> "$LOG_FILE"
echo "=================================================================" >> "$LOG_FILE"

cd "$APP_DIR"

# 1. Refresh cookies from Chrome if exporter is available
if [ -f "cookie_exporter.py" ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Refreshing Chrome cookies..." >> "$LOG_FILE"
    /usr/bin/python3 cookie_exporter.py >> "$LOG_FILE" 2>&1 || true
fi

# 2. Run full sync and deployment
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Running full pipeline (sync + deploy)..." >> "$LOG_FILE"
set +e
.venv/bin/python main.py --sync --deploy >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Weekly Friday Digest completed successfully!" >> "$LOG_FILE"
elif [ $EXIT_CODE -eq 2 ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Sync aborted by viability gate or block detection; preserved previous working digest." >> "$LOG_FILE"
else
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Sync failed with exit code $EXIT_CODE!" >> "$LOG_FILE"
    # Exit 2 (blocked / viability-gate aborts) already emailed from main.py;
    # any other failure gets its alert here. Exit code is preserved.
    .venv/bin/python notifier.py --failure-alert --context "Weekly Friday sync (main.py --sync --deploy)" --exit-code $EXIT_CODE >> "$LOG_FILE" 2>&1 || true
    exit $EXIT_CODE
fi
echo "=================================================================" >> "$LOG_FILE"
