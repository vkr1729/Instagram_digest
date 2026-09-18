# Comprehensive Final Audit & Fix Progress (Muse Spark 1.3 Contributor via cmd1)

## Status: COMPLETE
- **Harness**: `cmd1` (command-code)
- **Model**: `meta/muse-spark-1.3-contributor` (effort: `max`)
- **Scope**:
  1. UAT Bug Fixes Review & Validation:
     - Launch auto-immersive top/bottom chrome auto-hide.
     - Gallery / Grid selection auto-immersive auto-hide.
     - Bookmarks unsave playback freeze fix (seamless advance to next bookmark with audio and video playback).
  2. iOS App (`Sources/InstagramDigest/`, `Tests/`): Player pool, concurrency, memory, SwiftData, audio session, cache.
  3. Weekly Digest Generator on Laptop (`main.py`, `extractor.py`, `ranker.py`, `site_builder.py`, `storage_r2.py`, `scripts/topup_digest.py`, `cookie_exporter.py`, `local_server.py`, `config.py`, `atomic_io.py`): Scraper resilience, rate limiting, manifest generation, R2 uploads, error recovery.

---

## 1. UAT Bug Fixes Review — VALIDATED (2026-09-18, `git diff Sources/InstagramDigest/`)

### Fix 1 (Launch Auto-Immersive) — PASS
- `InstagramDigestApp.swift`: `loadManifest` sets `isChromeVisible = !pool.isPlaying` after `setReels`
  (L475-479); new `.onChange(of: pool.isPlaying)` (L354-359) keeps chrome in sync for ALL playback
  transitions (autoplay start, stall resume, watchdog rebuild); tap handlers (L176-180, L194-198,
  L235-239) toggle chrome. All guarded by `-ui-testing`.
- Edge: `isPlaying` flips async inside `configureCurrentSlot`; the synchronous read in `loadManifest`
  may see `false`, but the new `onChange` hides chrome when playback actually starts. Pause restores,
  resume re-hides. Covered.

### Fix 2 (Grid Selection Auto-Immersive) — PASS
- `jumpToReel` (L661-669) sets `wasPlayingBeforeSheet = true` + hides chrome; `resumeFeedAfterSheet`
  (L547-560) re-plays (idempotent) on dismiss. No conflict between the two paths. Covered.

### Fix 3 (Bookmarks Unsave Freeze) — PASS
- `BookmarksSheet.swift handleUnsave` (L606-626): empty-remaining tears down + closes; else loads the
  next bookmark BEFORE `onDeleteBookmark`, preserving scroll anchor. `currentBookmark` resolves by
  `scrolledReelID` first (L309-315); ForEach container matches by `reelID` (L327). `loadVideo`
  activates audio session + re-arms end observer; `updateUIView` guards `!==` + `setNeedsLayout`.
- Edges verified: 1 remaining (targetIndex clamps to 0), first/last delete (`min()` clamp),
  external shrink (backstop `onChange(of: bookmarks.count)` L486-499). No freeze path remains.

---

## 2. iOS App — Fixes Applied

### P0
- **IOS-P0-1** `Engine/MediaCacheManager.swift` — 8 SwiftData-touching methods annotated `@MainActor`
  (`reconcileBookmarkStorageLedger`, `computeLivePinSet`, `purgeOldWeekDirectory`,
  `ensureSpaceForBookmark`, `keepBookmarkOffline`, `syncRemoteBookmarks`, `deleteBookmarkFile`,
  `freeBookmarkStorage`); pure file-I/O helpers stay on the actor. Fixes background-actor
  `ModelContext` confinement violation (crash/corruption risk).
- **IOS-P0-2** `Engine/DownloadAllCoordinator.swift:203-209` — `task.cancel{}` closure now captures
  `self` weakly and hops to `Task { @MainActor }` before `persistResumeData` (was: MainActor state
  touched from URLSession delegate queue).
- **IOS-P0-3** `Models/DigestManifest.swift:60-80` — decode-time dedupe by reel ID (keep first);
  duplicate IDs crashed `ForEach(id:)` in GridView. Verified: bundled `Resources/data.json` has
  300 unique IDs (no behavior change on current data).

### P1
- **IOS-P1-1** `MediaCacheManager.evictLocalFeedFile` — skips reels in the active pool pin set.
- **IOS-P1-2** `keepBookmarkOffline` (3 branches) — ledger increments only after `context.save()`
  succeeds; rollback no longer leaves phantom bytes.
- **IOS-P1-3** `promotePartFile` — 10%/1MB tolerance instead of exact size equality (manifest sizes
  are estimates; strict equality discarded valid downloads).
- **IOS-P1-4** `promotePartFile` — failed `replaceItemAt` now throws instead of silent success.
- **IOS-P1-5** `deleteBookmarkFile` — syncs the `BookmarkItem` row to `.evicted`/`sizeBytes=0`.
- **IOS-P1-6** `freeBookmarkStorage` — `defer { isPurging = false }` + resume on every exit path.
- **IOS-P1-7** `freeBookmarkStorage` — rows marked `.evicted` only when the file is actually gone.
- **IOS-P1-8** `DownloadAllCoordinator` — resume data deleted only after atomic promotion succeeds.
- **IOS-P1-9** `LossyBookmarkList.decode` — wrapped `{bookmarks:[...]}` shape now lossy per-element
  (isolated re-encode; one bad entry no longer wipes the list).
- **IOS-P1-10** `ReelCardView` — verified no container-level blocker (VStack has no background; only
  controls/caption claim touches; progress bar already passthrough). No behavior change needed.
- **IOS-P1-11** `DownloadAllSheet` — `.failed` now shows Retry (`startDownloadAll`) + Done.

### P2
- Ledger cleared on missing-file eviction; detached copies use injected `pathResolver`; bounded
  15s/60s ephemeral session for remote bookmark fetch; `isPurging` guards on
  `keepBookmarkOffline`/`ensureSpaceForBookmark`; `fileExistsAndNonEmpty` returns false on attr
  error; manifest cache dir ensured before write; bookmarks offline disk fallback
  (`bookmarks_cache.json`); http(s)-only URL validation (`httpURL(from:)`) in `ReelItem` +
  `BookmarkRemoteDTO`; observer tokens stored + `deinit` in coordinator; `.paused` state actually
  emitted/restored; duplicate `BookmarkItem` rows deleted in sync; feed reload compares IDs only;
  `PlayerLayerView.updateUIView` guarded attach; `require(toFail:)` removed (suppressNextTap already
  disambiguates); scroll reattach throttled to page-boundary crossing; pending-guard consumes +
  processes settle; SeekPan uses window coordinates.
- **Declined**: sanitization-collision hardening (pinned by `ResolverTests` expectations);
  AVPlayerPool/FeedPagerView already verified sound (rotation, observer cleanup, layer attach
  discipline, thermal collapse, single-flight generation fencing).

---

## 3. Weekly Digest Generator — Fixes Applied

### P0
- **PY-P0-1** `main.py:895-915` + `storage_r2.purge_previous_weeks_videos(keep_week_ids:)` — quota check
  runs FIRST; JIT purge is fallback-only on quota failure and always keeps the week the persisted
  live digest points at (`_persisted_digest_week()`). Post-publish rolling purges reclaim the old
  week once the new batch is durable. Before: purge-then-crash = live-feed 404.

### P1
- **PY-P1-1** `main.py` — `--build-only`/`--deploy-only` now run under `_pipeline_file_lock()`
  (new `_build_only`/`_deploy_only` helpers); busy → exit 3 like sync/expand.
- **PY-P1-2** `local_server.py` — `_cross_process_pipeline_busy()` probes `data/.pipeline.lock`;
  both dashboard triggers check it BEFORE acquiring the in-process lock (ownership unambiguous)
  and report `already_running` instead of a false `started`.
- **PY-P1-3** `extractor.load_sources` — corrupt file quarantined (`sources.json.corrupt-<ts>`);
  non-list payloads quarantined; handle-less entries skipped via `clean_handle`.
- **PY-P1-4** `scripts/topup_digest.py` (digest + candidates cache) and `site_builder.build_site`
  — corrupt JSON quarantined instead of crashing (site builder falls back to empty digest).
- **PY-P1-5** `main.py` viability gate — banked checkpoints retired (`_retire_sync_file`), not
  deleted; retry-safe, invisible to `resume_pending.sh`.
- **PY-P1-6** `local_server.refresh_cookies_or_abort()` — validates returncode + `sessionid`;
  both workers abort early with cookie-attention banner instead of burning hours.
- **PY-P1-7** `extractor._downloaded_mp4_is_playable()` — ffprobe duration gate on both download
  paths (fails open when ffprobe absent); truncated/HTML payloads deleted, never uploaded.
- **PY-P1-8** `scripts/topup_digest.py` — pre-enrich cutoff filter + post-enrich drop with rank
  recompaction; unranked/undated/stale reels never backfill (UAT-2.3).
- **PY-P1-9** `site_builder._pages_bundle_is_remote()` — `deploy_to_gh_pages` refuses all-local or
  empty bundles (Pages 404 prevention). `upload_reel_to_r2` fallback path unchanged (pinned by
  `test_uat_4_6_local_fallback_mode`).
- **PY-P1-10** `cookie_exporter` — `_secure_write_text` is now atomic (temp + fsync + replace +
  dir fsync, 0600 from first byte); sqlite `conn.close()` in `finally` (double-close removed).
- **PY-P2-1** non-POSIX lock fallback logs a loud warning instead of silent no-op.
- **PY-P2-2** corrupt `candidates_cache.json` quarantined + unlinked in `main.py`.
- **PY-P2-4** CDN loop: 429 honors `Retry-After` else jittered exponential backoff; 403/404/410
  fall through to yt-dlp immediately; no sleep after final attempt.
- **PY-P2-5** thumbnails: newest-by-mtime on duplicate rank prefixes; ffmpeg failures at warning.
- **PY-P2-6** `ranker.save_digest_batch` emits `generated_at` (= `created_at`, legacy kept).
- **PY-P2-7** R2 upload: 3× jittered retry loop with client refresh on connection errors.
- **PY-P2-8** icons copied atomically (temp + replace); archive written before live batch.
- **Declined**: `peanuts` DBus-fallback removal (would break headless runs; failure already
  surfaces via `refresh_cookies_status` validation).

---

## 4. Verification Results
- **Python**: isolated venv (boto3/playwright installed; system env lacks them — pre-existing).
  Full suite `tests/ --ignore=tests/e2e`: **292 passed** (pristine: 281; +11 new audit tests),
  **9 failed + 27 errors — byte-identical set to the pristine tree** (all: missing Playwright
  browser binary, missing `InstagramDigest.desktop`, i.e. environment-only, untouched by this audit).
  - New: 9 tests in `tests/test_teardown_final.py` (`test_audit_*`: purge keep-live-week, sources
    quarantine ×3, topup corrupt/stale, deploy-gate, generated_at, cache quarantine) +
    2 in `tests/test_adhoc_sync.py` (cookie early-abort, cross-process busy).
  - `test_adhoc_sync.py` trigger tests hardened with `_drain_trigger_state()` (module-global lock
    flake under suite ordering).
- **Swift**: Linux `swiftc -parse` clean on all 12 edited files. Full type-check/build requires
  macOS Xcode (UIKit/SwiftUI); new XCTest cases added (`ModelTests`: dedupe, relative-URL reject;
  `AuditFixesTests`: wrapped-lossy, relative-bookmark skip) — **must run on macOS CI before release**.
- **Not run**: `tests/e2e/` (needs Playwright browsers + network); iOS simulator tests.

## 5. Release Checklist (for owner)
1. Run `xcodebuild test` on macOS (new XCTest cases + full `InstagramDigestTests`/`UITests`).
2. Run one `--dry-run` sync, then a real weekly sync; confirm quota/JIT log lines.
3. `main.py --build-only` while a sync runs → expect exit 3 (lock verified by code path).
4. Dashboard trigger during CLI run → expect `already_running`.
