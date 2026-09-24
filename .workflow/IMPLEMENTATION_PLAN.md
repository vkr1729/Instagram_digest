# Implementation Plan: Instagram Digest 250-Reel Expansion & Similar Creators

## 1. Architectural Blueprint
- **Runtime & Stack:** Python 3.12+ (Linux server/workstation, Playwright, Requests, yt-dlp, native ThreadingHTTPServer) + Swift 5.9+ / SwiftUI (iOS Native App).
- **Component Boundaries:**
  - `config.py`: Single source of truth for `TOP_DIGEST_COUNT = 250`, `MIN_DEPLOY_ITEMS = 150`.
  - `extractor.py`: `InstagramSession.new_isolated_page()` for secondary tab; `is_pinned` exemption from 7-day cutoff; 2,000 max feed evaluations.
  - `recommendations.py`: Standalone service running 6 headless `agy -p` calls with `--json-schema`, `--print-timeout 180s`, auth preflight, JSON validation, and atomic file caching (`data/recommended_creators.json`).
  - `ranker.py`: Support per-creator cap mapping (`max_per_creator: int | dict[str, int]`) so Tier 2 recommended creators can fill up to 8 slots while Tier 1 followed creators remain strictly capped at 4.
  - `main.py`: 3-Tier sequential ingestion (Followed -> Recommended -> Reels Feed), granular stage checkpointing (`shortfall_paused`), frozen recommendations in checkpoint, shortfall alert email, and `--resume` support.
  - `local_server.py` & `templates/dashboard.html`: Curation UI with "Recommended This Week" section, 1-click "Add to Channels", and 1-click "Resume Sync / Top-Up".
  - `Sources/InstagramDigest/`: iOS app header watch timer (`X.X hrs`) keyed by `watchSeconds_{week_id}`, AVPlayer playback observer, `.mixWithOthers` audio session, and LiveContainer path resilience.

---

## 2. Component Breakdown & Execution Order

### Phase 1: Core Configuration, Ranker Caps & Checkpoint Contract
- **Files to modify:**
  - `config.py`: Update `TOP_DIGEST_COUNT = _env_int("TOP_DIGEST_COUNT", 250, 1, 1000)` and derive `MIN_DEPLOY_ITEMS = int(config.TOP_DIGEST_COUNT * 0.6)`.
  - `ranker.py`:
    - Extend `rank_top_reels` to accept `max_per_creator: int | dict[str, int] = 4`.
    - Apply per-creator cap (4 for followed, up to 8 for recommended).
  - `main.py`:
    - Add `--resume` CLI flag.
    - Add `shortfall_paused` to `RESUMABLE_SYNC_STAGES`.
    - Checkpointing: Bank `tier1_reels`, `tier2_reels`, `tier3_reels`, and freeze `recommended_creators` into checkpoint.
    - On shortfall (< 150 playable reels): write checkpoint with `stage: "shortfall_paused"`, send failure alert email, and preserve existing live digest.
  - `resume_pending.sh` & `local_server.py`:
    - Update `resume_pipeline_state()` and `live_progress_state()` to recognize `shortfall_paused`.
    - Expose `POST /api/sync/resume` in `local_server.py`.
- **Verification check:**
  - `pytest tests/test_ranker.py` + new ranker cap mapping test.
  - Checkpoint serialization and resume stage roundtrip tests.

### Phase 2: Scraper Tab Isolation & Pinned Reel Cutoff Exemption (`extractor.py`)
- **Files to modify:**
  - `extractor.py`:
    1. Add `InstagramSession.new_isolated_page()`: Creates a fresh `Page` in the existing context without polluting `_recycle_counter`. Caller closes in `finally`.
    2. In `extract_external_reels_from_feed`:
       - Use `session.new_isolated_page()` when extracting single-reel metadata. Primary feed page is NEVER passed to metadata extraction and stays anchored on `https://www.instagram.com/reels/`.
       - Add assertion: check `page.url` contains `/reels/` after each iteration; reload `/reels/` if violated.
       - Cap evaluations at 2,000 max (stopping as soon as 250 target is filled).
    3. In `discover_creator_reel_urls`: Add `include_pinned: bool = False`. If `True`, extract pinned reels and mark `is_pinned: True`.
    4. In `enrich_shortlist_metadata` / date filter: If `reel.get("is_pinned")`, exempt from `timestamp < cutoff_ts` filter so high-value pinned reels are preserved.
- **Verification check:**
  - Unit tests verifying `new_isolated_page` lifecycle and `is_pinned` date filter bypass.

### Phase 3: AI Recommendation Engine with CLI Guardrails (`recommendations.py`)
- **Files to create/modify:**
  - `recommendations.py` (New):
    1. Auth Preflight: Run quick smoke prompt with `agy -p` (10s timeout); if auth fails, log warning, skip Tier 2, and mark `recommendations_stale: True`.
    2. Per-Category Discovery: For each category with active channels, invoke `agy -p` with `--json-schema` (enforcing handle, name, category, reason, follower_scale) and `--print-timeout 180s`.
    3. Defensive Validation: Strip markdown fences, validate handle regex `^[A-Za-z0-9._]{1,30}$`, deduplicate against `sources.json`.
    4. Atomic Persistence: Write to `data/recommended_creators.json` via `atomic_io.durable_write_json`. If < 3 categories succeed, retain previous week's file and mark `recommendations_stale: True`.
    5. CLI support: `python recommendations.py --refresh`.
- **Verification check:**
  - `pytest tests/test_recommendations.py` covering schema validation, markdown stripping, timeout handling, and fallback behavior.

### Phase 4: Multi-Tier Ingestion & Resumption Integration (`main.py`)
- **Files to modify:**
  - `main.py`:
    1. Sequenced Ingestion Pipeline:
       - Refresh recommendations (or load cached `data/recommended_creators.json`).
       - Tier 1: Scrape followed creators (`sources.json`) -> up to 4 unpinned reels.
       - Tier 2: If candidates < 250, scrape recommended creators -> up to 8 reels (pinned + unpinned, viral-weighted).
       - Tier 3: If still < 250, run isolated feed discovery up to 2,000 evaluations.
    2. Resumption Logic:
       - If resuming from `shortfall_paused`, load banked Tier 1 and Tier 2 reels directly from checkpoint; only run Tier 3 top-up and publishing.
    3. Byte-budget guard: Add `budget_capped: True` flag in manifest if `MAX_FEED_BATCH_BYTES` is reached.
- **Verification check:**
  - Run end-to-end dry-run test verifying tier sequence and checkpoint reuse.

### Phase 5: Web Dashboard Curation & Resume UI (`local_server.py`, `templates/`)
- **Files to modify:**
  - `local_server.py`:
    - Expose `GET /api/recommended-creators` returning current recommendations.
    - Support `POST /api/channels/add` (or leverage existing channel restore path) to append approved recommended creators to `sources.json`.
    - Expose `POST /api/recommendations/refresh` to trigger background `recommendations.py`.
    - Expose `POST /api/sync/resume` to resume paused sync runs.
  - `templates/dashboard.html` & `templates/channels.html`:
    - Add "Recommended This Week" section with category filter chips, follower count, recommendation reason, and "Add to Channels" action.
    - Add alert banner with 1-click "Resume Sync / Top-Up" button when `shortfall_paused` checkpoint exists.
    - Add "Refresh Recommendations" button with loading state.
- **Verification check:**
  - `pytest tests/test_dashboard.py` testing new endpoints and UI templates.

### Phase 6: Native iOS Watch Timer & LiveContainer Hardening (`Sources/`)
- **Files to modify:**
  - `Sources/InstagramDigest/Views/Feed/HeaderBarView.swift`:
    - Add watch timer pill (`X.X hrs`) next to Category bar, Download All, and Grid View buttons (maintaining $\ge 44$pt hit target).
  - `Sources/InstagramDigest/Models/DigestState.swift`:
    - Track active playback time when visible video player status is `.playing`.
    - Persist to `UserDefaults` under `watchSeconds_\(weekID)`.
    - Auto-resets on new `week_id` manifest rollover.
  - `Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift`:
    - Add `.mixWithOthers` option to `setCategory(.playback, mode: .moviePlayback, options: [.mixWithOthers])`.
  - `Sources/InstagramDigest/Engine/LibraryPathResolver.swift`:
    - Ensure container directory safety for LiveContainer sandboxes.
- **Verification check:**
  - Verify Swift code compilation, path isolation, and UserDefaults keying.

---

## 3. Frontier Architectural Review & Enhancements
- **Blockers Addressed:**
  1. Ranker cap: Added per-creator cap mapping (`max_per_creator: int | dict[str, int]`) so Tier 2 can use 8 while Tier 1 retains 4.
  2. Pinned cutoff: Pinned reels tagged with `is_pinned: True` and exempted from `timestamp < cutoff_ts` filter.
  3. `agy -p` guardrails: Enforce `--json-schema` and `--print-timeout 180s`, auth preflight, and atomic caching.
- **Sequencing Risks Mitigated:**
  1. Checkpoint contract unified across `main.py`, `local_server.py`, `resume_pending.sh`, and `dashboard.html`.
  2. Playwright tab isolation via dedicated `InstagramSession.new_isolated_page()`.
  3. Shortfall email alerts hooked up for unattended overnight failures.

---

## 4. Verification & Guardrails
- **Automated Test Suites:**
  - `pytest tests/test_pacing.py tests/test_ranker.py tests/test_dashboard.py tests/test_recommendations.py tests/test_scraper_isolation.py`
  - Dry-run verification: `python main.py --dry-run`
- **Circuit Breaker Rule:** If 2 consecutive failed attempts occur during implementation, invoke frontier model stuck guidance immediately.
