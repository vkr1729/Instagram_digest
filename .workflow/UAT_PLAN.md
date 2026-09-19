# UAT Plan — Instagram Digest v5.1.0 (250-Reel Expansion & Similar Creators)

Official Stage 4 user-acceptance plan. Single-user scope: Linux overnight pipeline + iOS native app (SideStore → LiveContainer) + desktop web dashboard for curation/monitoring.

## 0. Test environment & preconditions

- Linux host, project venv (`.venv/bin/python`), Playwright Chromium installed, `agy` CLI on PATH or `~/.local/bin/agy`, Instagram Chrome session valid (`data/cookies.json` fresh).
- Baseline fixtures: `sources.json` (enabled channels across 6 categories), `data/blacklist.json`, prior live digest in `data/top100_digest.json` (to exercise the ≥150 shortfall gate).
- Record week_id under test: `date -u +%F` (UTC). All checkpoint files are `data/sync_progress_{week_id}.json`.
- Entry criteria: `pytest tests/test_ranker.py tests/test_recommendations.py tests/test_scraper_isolation.py tests/test_dashboard.py` green; `python main.py --dry-run` exits 0 without mutating the live digest.

## A. Pipeline ingestion tiers & pacing (Req 1, 4)

| ID | Covers | Steps | Expected / pass criteria |
|----|--------|-------|--------------------------|
| A1 | TOP 250 / MIN 150 config | 1. `grep -n TOP_DIGEST_COUNT config.py main.py`; 2. Run ranking unit test. | `TOP_DIGEST_COUNT=250`, `MIN_DEPLOY_ITEMS=int(250*0.6)=150`. `test_rank_top_reels_per_creator_cap_dict` passes. |
| A2 | Tier 1 caps (4/creator, unpinned, 7-day) | 1. Dry-run sync; 2. Inspect Tier 1 logs + `discover_creator_reel_urls` calls (no `include_pinned`). | Followed creators yield ≤4 final slots each; pinned reels skipped in Tier 1 logs; enrichment discards `timestamp < cutoff` for unpinned. |
| A3 | Tier 2 quota (≤8/creator, pinned allowed) | 1. Seed `data/recommended_creators.json` with 2 test recs; 2. Dry-run; 3. Check logs for `Tier 2: Extracting reels for recommended`. | `extract_creator_reels(..., max_reels=8, include_pinned=True)` per rec; final per-rec count ≤8; pinned old-timestamp rec retained (cf. `test_extract_creator_reels_exempts_pinned_reels_from_cutoff`). |
| A4 | Tier 3 trigger & deficit fill | 1. Force small Tier 1+2 (e.g. temp `limit-per-creator 1`); 2. Observe `Channels produced N reels (D below target 250). Discovering external high-signal reels from feed...`. | Tier 3 runs with `target_count == deficit`, `max_evaluations=2000`; harvested externals appended and re-numbered `#01..#N`. |
| A5 | Pacing / anti-detection | 1. Observe `CREATOR_PAUSE`, `FEED_COOLDOWN_SECS`, `CREATOR_BREAK_EVERY` sleeps in an overnight run log. | No tight loops; cooldown lines present (`Anti-detection cooldown: resting ...`). |
| A6 | Byte-budget guard | 1. Code-check `budget_capped` in `main.py`; 2. (Optional) stub oversized files to trip `MAX_FEED_BATCH_BYTES`. | Manifest from `save_digest_batch(..., extra_manifest={"budget_capped": True})` contains `budget_capped:true` when tripped; batch still ≥150. |

## B. Checkpoint shortfall preservation & 1-click top-up (Req 5)

| ID | Covers | Steps | Expected / pass criteria |
|----|--------|-------|--------------------------|
| B1 | Shortfall gate (<150 playable) | 1. With healthy live digest (≥150), run sync in conditions yielding <150 playable (e.g. block R2 or stub downloads); 2. Check exit code, digest file mtime, checkpoint. | Exit 2; live `data/top100_digest.json` byte-identical (never overwritten); `data/sync_progress_{week}.json` has `stage:"shortfall_paused"`, `ranked[]`, `recommended_creators[]`, `downloaded_paths{}`, `uploaded_url_map{}`, `deficit == 250-len(ranked)`; failure alert email attempted (`digest shortfall paused`). |
| B2 | Dashboard shortfall banner | 1. Leave B1 checkpoint in place; 2. `GET /dashboard`, `GET /api/resume-state`. | Banner reads **Shortfall Paused (Ready to Top-Up)**, shows banked playable count + `< 150 required threshold`; button text **Resume Sync / Top-Up** with `data-resume-shortfall="1"`. |
| B3 | 1-click resume → Tier 3 only | 1. `POST /api/sync/resume`; 2. Tail logs for `Resuming shortfall-paused weekly sync: N reels banked. Proceeding directly to Tier 3 feed top-up...`; 3. Confirm no `Extracting candidate reels for @<followed>` lines. | HTTP 200 `{success:true, status:"running"}`; resume skips Tier 1/2 scraping, runs feed top-up, reuses `downloaded_paths`/`uploaded_url_map` (`Reusing N already ... from banked progress`); frozen `recommended_creators` reused from checkpoint. |
| B4 | Resume completion clears checkpoint | 1. Let B3 run to success (≥150 playable + deploy). | Both `sync_checkpoint` and `sync_read_path` unlinked; new digest saved; `save_last_run_info` written. |
| B5 | Tier 3 hard failure path | 1. Simulate feed login wall during Tier 3 (expire cookies mid-feed). | Cookie alert email + dashboard cookie-attention popup; partial externals preserved; run exits without clobbering live digest. |
| B6 | Login auto-resume sees shortfall file | 1. Run `resume_pending.sh` with only a `shortfall_paused` file present and no live pipeline. | Script launches `main.py --sync --deploy`, which auto-resumes the banked work (single-flight + busy guards hold). |

## C. Tab-isolated scraper under high evaluation count (Req 2)

| ID | Covers | Steps | Expected / pass criteria |
|----|--------|-------|--------------------------|
| C1 | Isolated secondary tab | 1. Run `test_new_isolated_page_does_not_bump_recycle_counter`; 2. Code-read `extract_external_reels_from_feed` enrichment block. | `_nav_count` unchanged by `new_isolated_page()`; metadata fetch uses `page=isolated_tab`, closed in `finally`; `session.get_page()` never called for single-reel enrichment in feed path. |
| C2 | Primary tab anchored on /reels/ | 1. Overnight Tier 3 run (or mocked `page.url` drift test); 2. Grep logs for `Feed page navigated away`. | After every evaluation, `page.url` asserted to contain `/reels/`; on violation, `page.goto("https://www.instagram.com/reels/")` recovery fires and discovery continues. |
| C3 | 2,000-evaluation ceiling | 1. Call `extract_external_reels_from_feed(..., max_evaluations=99999)` in a stubbed harness; 2. Observe stop at 2000 or at target first. | `max_evaluations = min(2000, ...)`; loop `while len < target and eval_count < max_evaluations`; early termination once 250-target deficit filled. |
| C4 | Pinned exemption vs unpinned cutoff | 1. Run `test_extract_creator_reels_exempts_pinned_reels_from_cutoff` + `test_extract_single_reel_metadata_uses_provided_page`. | Old-timestamp pinned reel retained with `is_pinned:true`; old unpinned reel discarded; caller-supplied `page` used (no `session.get_page()` call). |

## D. Recommendations engine & UI (Req 3, 6)

| ID | Covers | Steps | Expected / pass criteria |
|----|--------|-------|--------------------------|
| D1 | `agy -p` guardrails | 1. `python recommendations.py --refresh --timeout 600` (or dashboard Refresh); 2. Inspect process args in logs. | 6 per-category invocations with `--json-schema`, `--print-timeout 600s`, `--output-format json`; per-category timeout honored; non-zero exit / timeout → category yields `[]` with warning, never raises. |
| D2 | Auth preflight + stale fallback | 1. Break `agy` auth (rename binary); 2. Refresh. | `check_agy_auth` fails fast; prior cache preserved; rewritten envelope carries `recommendations_stale:true`; Tier 2 proceeds from cache (or skips cleanly when empty). |
| D3 | Sanitization | 1. Run `test_sanitize_and_validate_recommendations`. | Markdown fences stripped; handle regex `^[A-Za-z0-9._]{1,30}$` enforced; `@`-prefix lowercased; dedupe vs `sources.json` + cross-category; cap 10/category with `recommended_at` stamp. |
| D4 | Quarantine on <3 categories | 1. Run quarantine unit test; 2. Force 1-category success in staging. | Partial results → `data/recommended_creators.quarantine.json`; prior `recommended_creators.json` untouched; pipeline continues on cache. (Known gap: quarantine path does not re-stamp `recommendations_stale:true` — verify manually, P2.) |
| D5 | Dashboard Recommended tab | 1. `GET /api/recommended-creators`; 2. Open `/dashboard#recommendedSection`; 3. Cycle filter chips (All + 6 categories). | Cards show `@handle`, name, `follower_scale`, reason, category badge; chips filter client-side; Refresh button shows `Launching…/Refreshing…` disabled state and polls every 30 s. |
| D6 | Add to Channel List | 1. Click `+ Add to Channel List`; 2. `POST /api/channels/add` asserted; 3. Check `sources.json` + blacklist. | `{success:true, handle}`; new entry `{handle,name,category,enabled:true}` appended (or existing re-enabled); handle removed from `data/blacklist.json`; button flips to `Added ✓`. |
| D7 | Refresh endpoint concurrency | 1. Double-click Refresh; 2. Second `POST /api/recommendations/refresh`. | Second call returns `{status:"already_running"}`; single `RecommendationsWorker` thread. |

## E. iOS watch timer & LiveContainer hardening (Req 7, 8)

| ID | Covers | Steps | Expected / pass criteria |
|----|--------|-------|--------------------------|
| E1 | Header timer render | 1. Build app, open feed; 2. Assert `WatchTimerPill` visible next to Grid / Download / Bookmarks. | Pill shows `0.0 hrs` initially, format `%.1f hrs`; stopwatch icon; ≥44 pt hit target; no layout clipping with brand logo. |
| E2 | Active-playback accounting | 1. Play feed 65 s (visible player `.playing`); 2. Pause 30 s; 3. Read timer. | Timer advances only while `pool.isPlaying` (≈+65 s → `0.0 hrs` still, since 65/3600=0.02); paused time contributes 0. Persist cadence: UserDefaults write every 5 counted seconds + on backgrounding. |
| E3 | Weekly keying & rollover | 1. Note `watchSeconds_{weekA}` > 0; 2. Simulate new-week manifest; 3. Check defaults + timer. | New week starts at `0.0 hrs`; `watchSeconds_{oldWeek}` removed; `purgeOldWeekDirectory(oldWeek)` called; `watchSeconds_{newWeek}` accumulates independently. |
| E4 | Persistence across reopen | 1. Background/foreground the app mid-week; 2. Kill + relaunch same week. | Timer restores from `watchSeconds_{weekId}` on `loadManifest()`; background notification flushes latest value. |
| E5 | `.mixWithOthers` audio | 1. Play background audio (Music/Podcasts); 2. Start reel playback; 3. Code-read `AudioSessionCoordinator.configureAudioSession()`. | Reel audio mixes (background audio not ducked/killed); category `.playback`, mode `.moviePlayback`, options include `.mixWithOthers` + Bluetooth/AirPlay; interruptions/route-change handlers pause safely. |
| E6 | MediaCache persistence & purge | 1. Download offline reels; 2. Force-reopen app (LiveContainer); 3. Trigger weekly rollover. | Files persist under `Library/Application Support/MediaCache/{week}/`; `ensureDirectoryExists` (+`ensureApplicationSupportExists`) creates containers with backup-exclusion + `completeUntilFirstUserAuthentication`; rollover deletes old week dir + stale `ResumeData`, never `Bookmarks/`; LivePinSet (active pool + cached bookmarks) never deleted. |

## F. Verification commands (run in order)

```bash
.venv/bin/python -m pytest tests/test_ranker.py tests/test_recommendations.py tests/test_scraper_isolation.py tests/test_dashboard.py -q
.venv/bin/python -m pytest tests/ -q
.venv/bin/python main.py --dry-run
grep -n 'TOP_DIGEST_COUNT' config.py
grep -n 'shortfall_paused' main.py local_server.py templates/dashboard.html | head -n 20
grep -n 'new_isolated_page\|/reels/' extractor.py | head -n 20
grep -n 'include_pinned\|is_pinned' extractor.py | head -n 30
grep -n 'max_per_creator' ranker.py main.py
grep -n 'recommended-creators\|channels/add\|recommendations/refresh\|sync/resume' local_server.py templates/dashboard.html | head -n 30
grep -n 'watchSeconds_\|watchHoursText\|WatchTimerPill' Sources/InstagramDigest/InstagramDigestApp.swift Sources/InstagramDigest/Views/Feed/HeaderBarView.swift
grep -n 'mixWithOthers' Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift
grep -n 'MediaCache\|ensureDirector\|purgeOldWeekDirectory' Sources/InstagramDigest/Engine/LibraryPathResolver.swift Sources/InstagramDigest/Engine/MediaCacheManager.swift | head -n 20
bash -n resume_pending.sh
```

## G. Exit criteria & sign-off

- All section A–E cases pass; F commands green.
- One full overnight dry-run completes with Tier 1 → Tier 2 → Tier 3 sequencing visible in logs and no live-digest mutation.
- One induced-shortfall drill (B1–B4) completes: checkpoint → banner → 1-click resume → ≥150 digest, checkpoint cleared.
- Known accepted gaps (P2, not blocking): `APP_VERSION` still `5.0.0`; quarantine path doesn't re-stamp `recommendations_stale:true`; checkpoint uses `candidates/enriched/ranked` keys (not literal `tier{1,2,3}_reels`); no literal `tier3_failed` flag; `--resume` flag is accepted but resume is auto-detected from checkpoint stage; shortfall-resume appends Tier 3 finds without a second viral re-rank.
- Sign-off: single-user owner confirms 250-reel weekly digest plays in iOS app, dashboard curation works, and resume top-up recovers a shortfall run.
