# PART 1: STAGE 4 CODE & ARCHITECTURE AUDIT REPORT

I audited `config.py`, `extractor.py`, `recommendations.py`, `main.py`, `ranker.py`, `local_server.py`, `templates/dashboard.html`, `HeaderBarView.swift`, `InstagramDigestApp.swift`, `AudioSessionCoordinator.swift`, `LibraryPathResolver.swift`, `MediaCacheManager.swift` plus `tests/` (50 passed via `.venv/bin/python -m pytest tests/test_ranker.py tests/test_recommendations.py tests/test_scraper_isolation.py tests/test_dashboard.py`).

## Req 1–8 compliance

1. **250 / 150 scale:** PASS. `TOP_DIGEST_COUNT=250`, `MIN_DEPLOY_ITEMS=int(250*0.6)=150`.
2. **Scraper hijack fix:** PASS. `new_isolated_page()` doesn't bump `_nav_count`, used only for single-reel enrichment, closed in `finally`; feed `page` asserted to contain `/reels/` every iteration with reload recovery.
3. **AI discovery:** PASS with gap. 6x `agy -p --json-schema --print-timeout 600s`, auth preflight, fence-strip + `^[A-Za-z0-9._]{1,30}$` + dedupe vs `sources.json`, atomic write, quarantine on <3 categories.
4. **Tier pipeline:** PASS. T1 followed ≤4 unpinned, T2 rec ≤8 pinned+new, T3 feed `target=deficit`, `max_evaluations=min(2000,…)`, early exit at 250.
5. **Checkpoint/resume:** PASS with gaps. `shortfall_paused` in `RESUMABLE_SYNC_STAGES`, preserves `ranked + recommended_creators + downloaded_paths + uploaded_url_map + deficit`, `POST /api/sync/resume` resumes Tier-3-only without re-scraping channels.
6. **Dashboard Recommended tab:** PASS. `recommendedSection` + chips + reason/scale + Add to Channels, Refresh button, shortfall banner with `Resume Sync / Top-Up`.
7. **iOS watch timer:** PASS. `watchSeconds_{weekID}` in `UserDefaults`, 1s `pool.isPlaying` timer, `%.1f hrs` pill with `WatchTimerPill` ID, ≥44pt, weekly purge + restore.
8. **LiveContainer hardening:** PASS. `MediaCache/` under `Application Support`, `ensureDirectoryExists` + backup-exclusion + `completeUntilFirstUserAuthentication`, rollover purges week dir + `ResumeData` preserving `Bookmarks/`, `.mixWithOthers` present.

## Specific evaluations

- **250/150 gate:** correct; shortfall writes `shortfall_paused`, emails alert, never overwrites live digest.
- **Resume:** `is_shortfall_resume` skips T1/T2 extraction+ranking, reuses banked downloads/uploads + frozen recs.
- **Isolation:** primary never passed to metadata path; secondary closed in `finally`.
- **Pinned exemption:** `include_pinned` + `is_pinned` bypass of `timestamp < cutoff`; unpinned still 7-day filtered at enrichment.
- **agy engine:** preflight/timeout/validation/quarantine present; stale restamp missing on partial-category failure (P1).
- **Dashboard:** all 4 endpoints + UI present.
- **iOS timer/audio/persistence:** all present as specified.

## Issues

**P0 (blocker): none.**

**P1 (important):**
- `config.APP_VERSION` still `"5.0.0"`, docs say v5.1.0.
- No `tier3_failed` flag anywhere; T3-only failure with ≥150 publishes silently without flagging.
- Quarantine path (`<3` categories) returns cache without restamping `recommendations_stale:true` — dashboard can't tell recs are stale.

**P2 (minor):**
- Checkpoint uses `candidates/enriched/ranked` keys, not spec literal `tier1/2/3_reels`.
- `--resume` flag accepted but resume is auto-detected; flag is effectively no-op.
- `resume_pending.sh` launches `--sync --deploy` without `--resume` and doesn't name `shortfall_paused`.
- Shortfall resume passes `active_sources` (not `all_sources`) to T3 top-up; rec handles not excluded as followed.
- Plan said `--print-timeout 180s`, code uses 600s (fine overnight, slow for manual refresh).
- Dashboard "tab" is a card section + anchor, not tabs; `player.js` duration guard + unused `time` import are out-of-scope creep; shortfall resume appends T3 finds without second viral re-rank.

**VERDICT: APPROVED FOR UAT** — zero blockers; P1/P2 accepted as known gaps.

# PART 2: OFFICIAL UAT PLAN (`.workflow/UAT_PLAN.md`)

Exhaustive plan written to `.workflow/UAT_PLAN.md` covering:

- **A. Tiers & pacing:** 250/150 config, T1 ≤4 unpinned 7-day, T2 ≤8 pinned, T3 deficit fill, anti-detection sleeps, byte-budget `budget_capped`.
- **B. Shortfall & top-up:** induce <150 → exit 2 + live digest untouched + `shortfall_paused` with deficit; banner shows `Resume Sync / Top-Up`; `POST /api/sync/resume` logs Tier-3-only top-up, reuses banked paths/URLs; completion clears checkpoint; cookie-death path; login auto-resume.
- **C. Tab isolation @2000:** `_nav_count` unchanged, `finally` close, `/reels/` anchor + recovery, `min(2000,…)` ceiling, pinned-exemption unit tests.
- **D. Recs & UI:** `agy -p` schema/timeout args, auth-fail stale fallback, sanitization, quarantine, Recommended cards/chips, Add-to-Channels blacklist removal, refresh concurrency.
- **E. iOS & LiveContainer:** pill render `X.X hrs`, active-play-only counting, weekly key rollover, reopen persistence, `.mixWithOthers` mixing, `MediaCache/` persist/purge preserving `Bookmarks/`.
- **F. Verification commands:** pytest suites, `--dry-run`, grep checks for 250/shortfall/isolation/pinned/caps/endpoints/timer/audio/paths, `bash -n resume_pending.sh`.
