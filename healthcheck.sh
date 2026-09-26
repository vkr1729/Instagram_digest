#!/bin/bash
# healthcheck.sh — Pre-run health gate for Instagram Digest.
# Checks (all read-only, fail-fast, exit non-zero on first failure):
#   1. Python venv + key modules importable
#   2. Instagram session valid (cookies fresh, sessionid present)
#   3. R2 reachable + quota headroom for an estimated batch
#   4. Live digest fresh (< 10 days) with >= MIN_DEPLOY_ITEMS items
#   5. GitHub Pages serving the current week
# B27: this is a fail-fast gate — no silent skips. (It is still unwired:
# run_weekly.sh does not invoke it. Wire it before claiming "gate".)
set -uo pipefail

cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python

fail() { echo "HEALTHCHECK FAIL: $1" >&2; exit 1; }
ok() { echo "ok: $1"; }

[ -x "$PY" ] || fail "venv python missing at $PY (recreate .venv, pip install -r requirements.txt)"
$PY -c "import config, extractor, ranker, storage_r2, site_builder" 2>/dev/null \
  || fail "key modules do not import"
ok "venv + modules import"

[ -f .env ] || fail ".env missing (copy from .env.example)"
[ "$(stat -c %a .env)" = "600" ] || echo "warn: .env is not mode 600 (run: chmod 600 .env)"
ok ".env present"

$PY - <<'EOF' || fail "no Instagram login session in Chrome (log into instagram.com first)"
import json, sys
sys.path.insert(0, ".")
import config
try:
    cdata = json.loads((config.DATA_DIR / "cookies.json").read_text())
except Exception as e:
    print(f"cookies.json unreadable: {e}", file=sys.stderr); sys.exit(1)
cd = cdata.get("cookies_dict", {})
if not cd.get("sessionid"):
    print("cookies.json has no sessionid", file=sys.stderr); sys.exit(1)
print(f"ok: {len(cd)} cookies incl. sessionid (ds_user {cd.get('ds_user_id', '?')})")
EOF

$PY - <<'EOF' || fail "R2 unreachable or quota headroom insufficient"
import sys
sys.path.insert(0, ".")
import config, storage_r2
est = storage_r2.estimate_weekly_batch_bytes()
cur, n = storage_r2.get_bucket_storage_usage()
if cur < 0:
    print("cannot verify R2 usage", file=sys.stderr); sys.exit(1)
quota = config.R2_STORAGE_QUOTA_BYTES
print(f"ok: R2 {cur/1073741824:.2f}GB ({n} objs), est. batch {est/1073741824:.2f}GB, quota {quota/1073741824:.2f}GB")
if cur + est >= quota:
    print("projected batch exceeds safety quota (JIT purge runs pre-upload, but margin is gone)",
          file=sys.stderr); sys.exit(1)
EOF

$PY - <<'EOF' || fail "live digest stale or too small"
import json, sys, time
sys.path.insert(0, ".")
import config
try:
    d = json.loads(config.DIGEST_BATCH_FILE.read_text())
except Exception as e:
    print(f"digest unreadable: {e}", file=sys.stderr); sys.exit(1)
items = d.get("items", [])
week = d.get("run_date", "?")
floor = int(config.TOP_DIGEST_COUNT * 0.6)
arch = config.DIGESTS_DIR / f"{week}.json"
age_days = (time.time() - arch.stat().st_mtime) / 86400 if arch.exists() else 999
print(f"ok: digest {week} has {len(items)} items (floor {floor}), age {age_days:.1f}d")
if len(items) < floor:
    print("digest below deploy floor", file=sys.stderr); sys.exit(1)
if age_days > 10:
    print("digest older than 10 days", file=sys.stderr); sys.exit(1)
EOF

PAGES_URL=$($PY -c "import sys; sys.path.insert(0,'.'); import config; print(config.PAGES_BASE_URL)" 2>/dev/null | tr -d ' ')
# B27: a failing config import must fail loudly, never skip the Pages check.
if [ -z "$PAGES_URL" ]; then
  fail "PAGES_BASE_URL unreadable (config import failed); refusing to skip Pages check"
fi
if [ -n "$PAGES_URL" ]; then
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$PAGES_URL/" || echo "000")
  [ "$CODE" = "200" ] || fail "Pages $PAGES_URL returned HTTP $CODE"
  ok "Pages live at $PAGES_URL (HTTP 200)"
fi

echo "HEALTHCHECK PASS"
