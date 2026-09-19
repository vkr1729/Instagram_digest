# Requirements Specification: Instagram Digest 250-Reel Expansion & Similar Creators

## 1. Project Overview & Target User Anchor
- **Project Name:** Instagram Digest (v5.1.0)
- **Target User & Scale:** Strictly single-person personal use. Runs locally on Linux (overnight unattended Friday weekly sync) and consumed primarily on native iOS app (SideStore -> LiveContainer). Web dashboard on desktop for curation and pipeline monitoring. Strictly personal-scale: zero multi-tenant auth frameworks, zero complex distributed database overhead.
- **Primary Objective:** 
  1. Scale weekly digest target to 250 reels (`TOP_DIGEST_COUNT = 250`, `MIN_DEPLOY_ITEMS = 150`).
  2. Fix scraper session-hijacking bug by using an isolated secondary page for reel metadata extraction so the feed crawler page stays on `https://www.instagram.com/reels/`.
  3. AI Similar Creator Discovery: Unattended per-category `agy -p` deep web-search to identify 10 high-quality similar creators per category. Refreshes weekly; persists to `data/recommended_creators.json`. Also manually triggerable from dashboard.
  4. Scraping Priority Pipeline:
     - Tier 1: Followed Creators (from `sources.json`, up to 4 reels/creator, unpinned only, 7-day cutoff).
     - Tier 2: Recommended Creators (from `data/recommended_creators.json`, up to 8 reels max for high-signal viral reels, 2–3 for moderate signal; includes pinned + new reels).
     - Tier 3: Global `/reels/` feed fallback (up to 2,000 evaluations maximum, stopping as soon as the 250-reel target is reached).
  5. Multi-Tier Checkpointing & Dashboard Resume: If final playable reels < 150 or Tier 3 fails, the pipeline preserves all banked candidates/enriched reels in `data/sync_progress_{week_id}.json` and flags `tier3_failed` / `shortfall_paused`. A 1-click "Resume Sync / Top-Up" button in the dashboard allows resuming directly from Tier 3 without re-scraping existing channels.
  6. Web Dashboard "Recommended This Week" Tab: Browse recommended creators by category, view reasons/stats, and 1-click "Add to Channel List" (appends to `sources.json`).
  7. Native iOS Watch Timer: Display clean hours indicator (`X.X hrs`) in the top navigation header (`HeaderBarView.swift`) alongside Download All & Grid View; tracks actual active playback via AVPlayer, keyed in `UserDefaults` by `watchSeconds_{week_id}` to reset automatically every weekly cycle.
  8. LiveContainer Hardening: Persistent caching under `Library/Application Support/MediaCache/` across reopens, weekly rollover auto-purging of feed media (preserving `Bookmarks/`), directory creation safeguards, and `.mixWithOthers` audio session configuration.

## 2. Functional Requirements (Scope Matrix)
- **Tier 3 Scraper Hardening (`extractor.py`):**
  - Dedicated secondary `Page` via `session.context.new_page()` for single-reel metadata enrichment, closed in `finally`.
  - Primary feed `Page` remains strictly anchored on `https://www.instagram.com/reels/` with post-evaluation assertion.
  - Evaluation ceiling: exactly 2,000 evaluations maximum, terminating early if target count (250) is reached.
- **AI Recommendation Engine (`recommendations.py`, `main.py`):**
  - 6 independent headless `agy -p` invocations with structured schema and timeout.
  - Defensive sanitization (strip markdown fences, regex validate handles, dedupe vs `sources.json`).
  - Atomic write to `data/recommended_creators.json`; fallback to prior cache on failure with `recommendations_stale: true`.
- **Multi-Tier Checkpointing & Resumption (`main.py`, `local_server.py`):**
  - Checkpoint schema records: `tier1_reels`, `tier2_reels`, `tier3_reels`, `enriched`, `stage`.
  - Preserves R2-uploaded videos and local disk caches when aborting under 150 items.
  - Exposes `POST /api/sync/resume` in `local_server.py` to pick up directly from Tier 3.
- **Web Dashboard UI (`templates/dashboard.html`, `templates/channels.html`, `local_server.py`):**
  - "Recommended This Week" tab with filter chips, reason tags, and "Add to Channels" action.
  - "Resume Sync / Top-Up" button in dashboard header when an incomplete sync exists.
  - "Refresh Recommendations" button triggering background `agy -p` generation.
- **iOS App (`Sources/InstagramDigest/`):**
  - Header Watch Timer in `HeaderBarView.swift` (format: `1.5 hrs`), tracking active playback time.
  - `UserDefaults` storage keyed by `watchSeconds_{week_id}`.
  - LiveContainer resilience: safe directory creation in `LibraryPathResolver`, `.mixWithOthers` audio session in `AudioSessionCoordinator`.

## 3. Interview Record & Decision Log
| # | Functional Question | Confirmed Decision / Rationale |
|---|---------------------|--------------------------------|
| 1 | Target Scale | Strictly single-user personal setup on Linux + iOS app. |
| 2 | Tier 3 Scope & Limit | Run up to 2,000 evaluations overnight until 250 reels reached (do not timebox to 150). |
| 3 | Failure & Shortfall Policy | Never discard banked work if < 150. Save checkpoint, mark failed, and provide 1-click "Resume Sync" on dashboard. |
| 4 | AI Recommendation Cadence | Automatic overnight Friday sync + manual dashboard button; refreshed weekly. |
| 5 | Recommended Creators Quota | Viral-weighted 1–8 reels (pinned + new reels allowed). |
| 6 | Watch Timer | Focus strictly on iOS native app header alongside Download All & Grid View; pure hours format. |
| 7 | LiveContainer Optimizations | Downloads persist across reopens in `Library/Application Support/MediaCache/`; purged on weekly rollover. |
