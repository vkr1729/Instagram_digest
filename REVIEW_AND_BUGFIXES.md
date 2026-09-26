# Review & Bug Fixes — Instagram Digest

Static review of the working tree as of 2026-09-26 (branch `main`, with
uncommitted local modifications — see Assumptions). All cited `file:line`
values were verified against the tree before writing. No pipeline, test,
or network command was executed; `cookies.txt`, `.env`, and all credential
material were never opened.

**Assumptions (stated explicitly):**
1. The working tree (including uncommitted changes) is the review target,
   not a fresh clone of the last commit. Line numbers match the working tree.
2. Nothing was built or run — findings come from reading code. Fix diffs are
   written against the verified current text but have not been compiled or
   test-run.
3. Severity scale follows the brief: **P0** breaks the weekly run or risks
   data/account — extended to "breaks the app build/verification pipeline",
   because the macOS GitHub Actions workflow is the *only* way this app can
   be built or verified (no local Xcode) and the app is the primary surface.
4. Single-owner tuning throughout: no multi-user machinery proposed.

**Totals:** 1 × P0, 6 × P1, 26 × P2. App findings are listed first within
each severity, per the brief's weighting.

---

## P0

### A1 — `testBottomHudActions` taps through the owner-key alert; the UAT CI step cannot pass on a clean runner

- **Location:** `Tests/InstagramDigestUITests/InstagramDigestUITests.swift:113` → `:120` (tap save, then tap `BookmarksChipButton`), caused by UI behavior at `Sources/InstagramDigest/InstagramDigestApp.swift:740-744` (first bookmark with no `digest_owner_key` sets `showOwnerKeyAlert = true`), alert declared at `Sources/InstagramDigest/InstagramDigestApp.swift:394-411`.
- **Trigger:** Any fresh simulator — no `digest_owner_key` in UserDefaults, true on every CI runner and never seeded by any test. `saveButton.tap()` takes the "add" branch (`InstagramDigestApp.swift:735-744`), the `.alert("Link Cloudflare Owner Key")` modal appears, and the test never dismisses it before `bookmarksChip.tap()`. No XCUITest interruption monitor exists anywhere in `Tests/` (verified: zero matches for `addUIInterruptionMonitor`).
- **Impact:** `BookmarksChipButton` is behind the alert window; the tap at `:120` fails hit-testing (thrown error) or taps the alert, and `:123`'s `navigationBars["Saved Bookmarks"]` assertion then fails because the sheet never opened. `testBottomHudActions` is red on every clean run → `Run Deep UAT (XCUITest Suite)` fails → the job aborts before `Build Release App` / `Package Unsigned IPA`, so **no IPA artifact is ever produced and no app change can be verified**. This is also a real UI/test mismatch: the test assumes a bookmark tap leads straight to the sheet; the UI interposes a modal prompt.
- **Severity: P0** (dead verification + delivery pipeline for the app).
- **Fix** (dismiss the interposed alert in the test and make the assertions real):

```diff
--- a/Tests/InstagramDigestUITests/InstagramDigestUITests.swift
+++ b/Tests/InstagramDigestUITests/InstagramDigestUITests.swift
@@ -110,10 +110,19 @@ final class InstagramDigestUITests: XCTestCase {
         XCTAssertTrue(saveButton.waitForExistence(timeout: 8.0))
 
         // Save bookmark via bottom HUD button
         saveButton.tap()
 
+        // The first bookmark prompts for the Cloudflare owner key; dismiss it
+        // so it cannot swallow the chip tap below (clean-run hit-test failure).
+        let ownerKeyAlert = app.alerts["Link Cloudflare Owner Key"]
+        if ownerKeyAlert.waitForExistence(timeout: 2.0) {
+            ownerKeyAlert.buttons["Later"].tap()
+        }
+
         let bookmarkIndicator = app.descendants(matching: .any)["BookmarkIndicator"]
-        _ = bookmarkIndicator.waitForExistence(timeout: 2.0)
+        XCTAssertTrue(bookmarkIndicator.waitForExistence(timeout: 2.0), "Bookmark pop indicator must appear after saving")
+        XCTAssertTrue(saveButton.label.contains("Saved"), "Save button must flip to 'Saved' after bookmarking")
 
         // Open Bookmarks sheet from header chip
         let bookmarksChip = app.buttons["BookmarksChipButton"]
```

(Alternative: suppress the alert under the `-ui-testing` launch argument. The
test-side dismissal above is preferred — it exercises the alert instead of
hiding it.)

---

## P1

### A2 — `cancelAll()` / `startDownloadAll()` never reset `suspensionSource`; a stale `.user` freeze permanently wedges later download batches

- **Location:** `Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift:110` and `:168` (both set `isSuspended = false` but leave `suspensionSource`, declared at `:186`, untouched), consumed by the sticky-user branch at `:199-204`.
- **Trigger:** Tap **Pause** (`suspensionSource = .user`) → tap **Cancel** (state → `.idle`, source stays `.user`) → tap **Start Download** → background the app or let the 10 s stall watchdog fire. `suspendQueue(source: .background)` hits `:199-204`: sets `isSuspended = true` and returns **without suspending in-flight tasks and without setting `state = .paused`**. On foreground, the lifecycle resume (`:505` area) `guard suspensionSource != .user else { return }` skips resuming; the watchdog auto-resume (`:245` area) is likewise dead because the source is never `.watchdog`.
- **Impact:** "Download All" silently freezes mid-batch after any background/foreground cycle (or any local-stall watchdog hit). `drainQueue()` is gated on `isSuspended` forever, so queued reels never download; the sheet shows a confusing "Resume" button in a `.downloading` state the user never paused. Manual Resume is the only recovery. Directly undermines the offline library.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
@@ public func startDownloadAll(reels: [ReelItem], weekID: String) {
         self.watchdogResumeTask?.cancel()
         self.watchdogResumeTask = nil
         self.isSuspended = false
+        self.suspensionSource = .none   // a prior user pause must not stick to the new batch
@@ public func cancelAll() {
         watchdogResumeTask?.cancel()
         watchdogResumeTask = nil
         isSuspended = false
+        suspensionSource = .none        // otherwise the sticky-user branch (l.199) misfires forever
         for (_, entry) in inFlightTasks {
```

### A3 — Bookmark cap check races actor re-entrancy: in-flight admissions are invisible to `ensureSpaceForBookmark`, so the 1.5 GB cap can be blown by concurrent bookmarks

- **Location:** `Sources/InstagramDigest/Engine/MediaCacheManager.swift:327` (`try await ensureSpaceForBookmark(incomingBytes: fileSize)`) → `:333` (`try await Task.detached { … }.value` — **suspends the actor**) → `:350` (`self.totalBookmarkBytes += fileSize`). The remote branch has a wider window: check at `:357`, up to 60 s of `boundedSession.download` at `:365`, commit at `:386`.
- **Trigger:** Bookmark several reels in quick succession (each `BookmarkController.add` fires its own `keepBookmarkOffline`) while the store is near the cap. Each call passes the cap check, then suspends at the copy/download; the next call starts during the suspension and passes the check against a ledger that still excludes the first admission. Both commit.
- **Impact:** The hard 1.5 GB cap is exceeded by up to N × file size for N near-simultaneous bookmarks (worse for the remote branch's long window), and the inflated ledger triggers spurious LRU evictions of older "Saved offline" favorites.
- **Fix:** reserve the bytes at admission time so concurrent checks see them:

```diff
--- a/Sources/InstagramDigest/Engine/MediaCacheManager.swift
+++ b/Sources/InstagramDigest/Engine/MediaCacheManager.swift
@@ public private(set) var totalBookmarkBytes: Int64 = 0
+    /// Bytes admitted but not yet committed (copy/download in flight).
+    /// Included in every cap check so concurrent admissions cannot overshoot.
+    private var reservedBookmarkBytes: Int64 = 0
@@ public func ensureSpaceForBookmark(incomingBytes: Int64) async throws {
-        if totalBookmarkBytes + incomingBytes <= Self.maxBookmarkStorageBytes {
-            return
-        }
+        if totalBookmarkBytes + reservedBookmarkBytes + incomingBytes <= Self.maxBookmarkStorageBytes {
+            reservedBookmarkBytes += incomingBytes
+            return
+        }
@@ (eviction loop condition)
-            if totalBookmarkBytes + incomingBytes <= Self.maxBookmarkStorageBytes {
+            if totalBookmarkBytes + reservedBookmarkBytes + incomingBytes <= Self.maxBookmarkStorageBytes {
                 break
             }
@@ (final guard)
-        if totalBookmarkBytes + incomingBytes > Self.maxBookmarkStorageBytes {
+        if totalBookmarkBytes + reservedBookmarkBytes + incomingBytes > Self.maxBookmarkStorageBytes {
             throw CacheError.insufficientStorage("Cannot evict enough storage: active reels are currently bound.")
         }
+        reservedBookmarkBytes += incomingBytes
     }
```
Then at each commit/failure site in `keepBookmarkOffline` release the reservation:
```diff
-            try await Task.detached {
+            do {
+                try await Task.detached {
                     ...
-            }.value
+                }.value
+            } catch {
+                reservedBookmarkBytes = max(0, reservedBookmarkBytes - fileSize)
+                throw error
+            }
@@
-                self.totalBookmarkBytes += fileSize
+                reservedBookmarkBytes = max(0, reservedBookmarkBytes - fileSize)
+                self.totalBookmarkBytes += fileSize
```
(and likewise in the remote branch and the already-on-disk branch).

### A4 — Owner-key alert "Save & Sync" forwards the current reel even after it was un-bookmarked (remote/local drift)

- **Location:** `Sources/InstagramDigest/InstagramDigestApp.swift:396-407`.
- **Trigger:** Rapid double-tap on `SaveBookmarkButton` (toggle on → off within the same runloop turn, before the alert modal blocks input): `toggleBookmarkCurrentReel` adds then removes, but `showOwnerKeyAlert = true` from the first tap still fires. "Save & Sync" then calls `saveRemoteBookmark(reel:)` for `pool.currentItems[activeIndex]` unconditionally (`:400-405`).
- **Impact:** A reel the HUD shows as **unsaved** gets a remote/Cloudflare/Telegram bookmark. Remote state permanently diverges from the local list (the tombstone blocks local re-sync, so it can never be reconciled from the device UI), and the user is prompted to link a key for an action that netted to nothing.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/InstagramDigestApp.swift
+++ b/Sources/InstagramDigest/InstagramDigestApp.swift
@@ -397,11 +397,13 @@ struct FeedMainView: View {
                 let trimmed = ownerKeyInput.trimmingCharacters(in: .whitespacesAndNewlines)
                 if !trimmed.isEmpty {
                     UserDefaults.standard.set(trimmed, forKey: "digest_owner_key")
                     if !pool.currentItems.isEmpty, activeIndex >= 0, activeIndex < pool.currentItems.count {
                         let reel = pool.currentItems[activeIndex]
-                        Task {
-                            _ = try? await DigestDataService.shared.saveRemoteBookmark(reel: reel)
+                        if bookmarkedReelIDs.contains(reel.id) {
+                            Task {
+                                _ = try? await DigestDataService.shared.saveRemoteBookmark(reel: reel)
+                            }
                         }
                     }
                 }
```

### P1-5 — Shortfall top-up mathematically cannot reach the deploy floor → permanent `shortfall_paused` loop

- **Location:** `main.py:1041-1045` (resume top-up cap), `main.py:1664-1666` (first-pass top-up cap), `main.py:39` (`MIN_DEPLOY_ITEMS = int(250 * 0.6) = 150`; `TOP_DIGEST_COUNT=250` at `config.py:112`, `MAX_EXTERNAL_SHARE=0.40` at `config.py:118` → `max_external = 100`), and the 5C shortfall gate `main.py:1900-1919` which re-parks `shortfall_paused` and returns 2.
- **Trigger:** Any run whose banked channel reels total **< 50**: Tier 3 is capped at 100 externals, so the reachable maximum is `banked + 100 < 150`. Realistic triggers: (a) a weekly run where enrichment/date/attrition leaves < 50 playable channel reels; (b) **any dashboard "Sync now" / `/retrigger` ad-hoc run with a small delta window** (clicked twice in one day: ~1–2 h window → 110–120 max).
- **Impact:** The 5C gate re-parks `shortfall_paused` and returns 2 **forever**: every resume re-runs, re-sends the `"digest shortfall paused"` alert (`main.py:1915`), and can never clear the gate (after the first top-up `already_external == max_external`, so `tier3_target == 0` and the run aborts faster). The week's digest never ships and content ages out of the 7-day window. Clears only via dashboard "discard" or `MAX_SYNC_RESUME_AGE_DAYS = 3` aging.
- **Fix:** let the external share cap yield to the deploy floor at both top-up sites:

```diff
--- a/main.py
+++ b/main.py
@@ -1042,7 +1042,10 @@
                 already_external = sum(1 for r in ranked_reels if r.get("is_external"))
                 max_external = int(config.TOP_DIGEST_COUNT * config.MAX_EXTERNAL_SHARE)
                 tier3_room = max(0, max_external - already_external)
-                tier3_target = min(deficit, tier3_room)
+                # The share cap must never make MIN_DEPLOY_ITEMS unreachable:
+                # banked < MIN_DEPLOY_ITEMS - max_external would otherwise
+                # re-park shortfall_paused forever.
+                floor_room = max(0, MIN_DEPLOY_ITEMS - len(ranked_reels))
+                tier3_target = min(deficit, max(tier3_room, floor_room))
@@ -1664,3 +1667,4 @@
                 deficit = config.TOP_DIGEST_COUNT - len(ranked_reels)
                 max_external = int(config.TOP_DIGEST_COUNT * config.MAX_EXTERNAL_SHARE)
-                tier3_target = min(deficit, max_external)
+                tier3_target = min(deficit, max(max_external,
+                                                MIN_DEPLOY_ITEMS - len(ranked_reels)))
```

### P1-6 — The weekly run adopts a parked **ad-hoc** checkpoint and skips extraction entirely

- **Location:** `main.py:222-237` (`_sync_progress_usable` — no run-kind in the gate; `:234`'s `resume or …` makes adoption unconditional on the resume path), `main.py:928-935` (checkpoint payload stores no `kind`), `main.py:2722-2732` (ad-hoc and weekly both anchor `since_ts = int(last_run["timestamp"])`), `main.py:996-1040` (shortfall resume **skips candidate extraction and Pass-1/2 ranking completely**).
- **Trigger:** A dashboard ad-hoc sync (`POST /api/sync-adhoc` — exactly what the cookie-alert "Retrigger" link fires) or a midweek run aborts and parks a checkpoint. The next weekly run computes the **same** `since_timestamp`, same `limit_per_creator=15`, stage resumable, banked age ≤ 3 days → the checkpoint is adopted.
- **Impact:** Friday's run treats the midweek ad-hoc banked work as its own: it **skips all creator extraction** (`main.py:1040`), tops up with Tier-3 feed externals, and publishes that mix as the weekly digest — missing every channel reel posted between the ad-hoc run and Friday, with the ad-hoc's date as `run_date` (week-drift adoption, `main.py:904-907`). This is the "resume is not the same operation" mixing the code comment at `main.py:864-869` says it prevents.
- **Fix:** stamp and gate on run kind (`kind` is already a parameter of `_run_full_sync`):

```diff
--- a/main.py
+++ b/main.py
@@ def _sync_progress_usable(loaded_sync, limit_per_creator, since_timestamp,
-                            resume, banked_age_days) -> bool:
+                            resume, banked_age_days, kind=None) -> bool:
     return (
         isinstance(loaded_sync, dict)
         and loaded_sync.get("version") == 1
         and loaded_sync.get("limit_per_creator") == limit_per_creator
         and (resume or loaded_sync.get("since_timestamp") == since_timestamp)
+        # Checkpoints from a different run kind are a different operation
+        # (ad-hoc delta vs full weekly window) — never adopt them.
+        and (kind is None or loaded_sync.get("kind") in (None, kind))
         and loaded_sync.get("stage") in RESUMABLE_SYNC_STAGES
         and banked_age_days <= MAX_SYNC_RESUME_AGE_DAYS
     )
@@ _write_sync_progress payload (line ~931):
-            "since_timestamp": since_timestamp, "stage": stage,
+            "since_timestamp": since_timestamp, "stage": stage, "kind": kind,
@@ call site (line ~897):
-            if _sync_progress_usable(loaded_sync, limit_per_creator, since_timestamp,
-                                       resume, banked_age_days):
+            if _sync_progress_usable(loaded_sync, limit_per_creator, since_timestamp,
+                                       resume, banked_age_days, kind=kind):
```

### P1-7 — Follow-cooldown abort exits 2 with **no failure alert** (silent week skip)

- **Location:** `main.py:2738-2739` (the only weekly exit-2 site that never calls `_alert_sync_abort`), combined with `run_weekly.sh:51-57` whose exit-2 branch deliberately sends no alert (comment at `run_weekly.sh:55-56`: "Exit 2 … already emailed from main.py"). `_check_follow_cooldown` (`main.py:733-761`) only logs and returns `False`; `tests/test_failure_alerts.py:68-74` checks only `_run_full_sync`'s source, so this site escapes the invariant.
- **Trigger:** `data/follow_progress.json` records ≥ `config.FOLLOW_BURST_THRESHOLD` follows within `FOLLOW_COOLDOWN_HOURS` (a mass-follow earlier in the week), then Friday's `main.py --sync --deploy` starts.
- **Impact:** The run aborts seconds after starting and *nobody is told loudly*: `run_weekly.sh` suppresses its own alert for exit 2 assuming main.py emailed. Only a log line and the best-effort health-report email show "exit code 2". A missed week with no failure notification.
- **Fix:**

```diff
--- a/main.py
+++ b/main.py
@@ -2735,8 +2735,11 @@
-    if not args.resume and not _check_follow_cooldown(force=args.force):
-        return 2
+    if not args.resume and not _check_follow_cooldown(force=args.force):
+        _alert_sync_abort(
+            "follow cooldown active",
+            "recent mass-follow burst; refusing scrape. Re-run with --force to override.")
+        return 2
```

---

## P2 — iOS app

### A5 — Remote bookmark admission reserves an *estimate* but commits the *actual* size with no second cap check

- **Location:** `Sources/InstagramDigest/Engine/MediaCacheManager.swift:357` (admits `estimatedBytes` — `item.sizeBytes`, else `fallbackSizeBytes`, else a 10 MB default at `:356`) vs `:386` (`totalBookmarkBytes += actualBytes`, never re-validated).
- **Trigger:** "Keep Offline" for a bookmark with no local file where `item.sizeBytes` is 0/stale (e.g. remote-synced rows with `sizeBytes == 0`) while near the cap.
- **Impact:** The 1.5 GB cap is silently exceeded by (actual − estimate) per remote admission (a 40 MB file admitted as 10 MB overshoots 30 MB); the storage gauge lies until the next-launch reconcile.
- **Fix:** gate on the real bytes before promoting:

```diff
--- a/Sources/InstagramDigest/Engine/MediaCacheManager.swift
+++ b/Sources/InstagramDigest/Engine/MediaCacheManager.swift
@@
             let (tempURL, _) = try await boundedSession.download(from: remoteURL)
             let dest = bookmarkDestURL
             let actualBytes: Int64 = Self.diskFileSize(atPath: tempURL.path) ?? estimatedBytes
+            // Manifest sizes are estimates — enforce the cap on the real bytes.
+            if actualBytes > estimatedBytes {
+                try await ensureSpaceForBookmark(incomingBytes: actualBytes - estimatedBytes)
+            }
             let resolver = self.pathResolver
```

### A6 — Already-on-disk "Keep Offline" path double-counts bytes the reconcile ledger already counted

- **Location:** `Sources/InstagramDigest/Engine/MediaCacheManager.swift:303` and `:309` (admission + `totalBookmarkBytes += size` for a file already at the bookmark destination), while `reconcileBookmarkStorageLedger` already counts on-disk files at `:99` (`computedBytes += size`). The orphan state comes from the save-failure paths at `:351-353` / `:387-389` (row rolled back, copied file left on disk).
- **Trigger:** A bookmark row with `localStatus != .cached` whose file exists in `MediaCache/Bookmarks/` after a failed `context.save()` post-copy, then a Keep-Offline / re-add after a reconcile has run.
- **Impact:** The ledger inflates by the file size (counted twice); the cap check treats already-on-disk bytes as new admission → unnecessary LRU eviction of genuinely cached bookmarks ("Saved offline" checkmarks vanish although disk is not full).
- **Fix:** re-derive the ledger instead of adding the bytes again:

```diff
--- a/Sources/InstagramDigest/Engine/MediaCacheManager.swift
+++ b/Sources/InstagramDigest/Engine/MediaCacheManager.swift
@@
         if fm.fileExists(atPath: bookmarkDestURL.path) {
             if item.localStatus != .cached {
-                let size = Self.diskFileSize(atPath: bookmarkDestURL.path) ?? 0
-                try await ensureSpaceForBookmark(incomingBytes: size)
                 item.localStatus = .cached
-                if let size = Self.diskFileSize(atPath: bookmarkDestURL.path) {
-                    item.sizeBytes = size
-                    do {
-                        try context.save()
-                        self.totalBookmarkBytes += size
-                    } catch {
-                        context.rollback()
-                    }
-                } else {
-                    try? context.save()
-                }
+                item.sizeBytes = Self.diskFileSize(atPath: bookmarkDestURL.path) ?? item.sizeBytes
+                try? context.save()
+                // Bytes already occupy disk (and reconcile counts them):
+                // re-derive the ledger instead of adding them a second time.
+                await reconcileBookmarkStorageLedger()
             }
             return
         }
```

### A7 — "Free Local Storage" resumes the download queue behind an explicit user pause

- **Location:** `Sources/InstagramDigest/InstagramDigestApp.swift:42` (`DownloadAllCoordinator.shared.resumeQueue()` in the purge suspension handler), executed by `MediaCacheManager.freeBookmarkStorage` (`MediaCacheManager.swift:568` → `downloadSuspensionHandler?(false)`); `resumeQueue()` (`DownloadAllCoordinator.swift:220-221`) clears `suspensionSource = .none` regardless of who paused.
- **Trigger:** Tap **Pause** on the Download All sheet, then **Free Local Storage** in the Bookmarks sheet.
- **Impact:** Downloads resume and the sheet flips to "Downloading" although the user explicitly paused them — bandwidth/battery use behind the user's back, pause state lost.
- **Fix:** give the purge its own suspension pair that never lifts a user pause:

```diff
--- a/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
@@
     public func resumeQueue() {
         suspensionSource = .none
         isSuspended = false
         ...
     }
+
+    // MARK: - Purge-linked suspension (must never lift an explicit user pause)
+    private var suspendedForPurge = false
+
+    public func suspendQueueForPurge() {
+        guard suspensionSource != .user else { suspendedForPurge = false; return }
+        suspendedForPurge = true
+        suspendQueue(source: .background)
+    }
+
+    public func resumeQueueAfterPurge() {
+        guard suspendedForPurge else { return }
+        suspendedForPurge = false
+        resumeQueue()
+    }
```
```diff
--- a/Sources/InstagramDigest/InstagramDigestApp.swift
+++ b/Sources/InstagramDigest/InstagramDigestApp.swift
@@                 await MediaCacheManager.shared.setDownloadSuspensionHandler { suspend in
                     await MainActor.run {
                         if suspend {
-                            DownloadAllCoordinator.shared.suspendQueue()
+                            DownloadAllCoordinator.shared.suspendQueueForPurge()
                         } else {
-                            DownloadAllCoordinator.shared.resumeQueue()
+                            DownloadAllCoordinator.shared.resumeQueueAfterPurge()
                         }
                     }
                 }
```

### A8 — Audio-interruption auto-resume plays in the background (ghost audio + `isPlaying` desync)

- **Location:** `Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift:117-119` (`AVPlayerPool.shared.play()` on `.ended`/`.shouldResume` with no application-state check; contrast `DownloadAllCoordinator.suspendForWatchdog`, which does guard on it).
- **Trigger:** Watching a reel when a call/Siri interruption begins (pool pauses, `wasPlayingBeforeInterruption = true`); user switches away during the call; the call ends with `.shouldResume` while the app is backgrounded.
- **Impact:** Ghost audio — reel audio blares from the background/lock screen until iOS suspends the app (no `UIBackgroundModes/audio`, verified). `wantsPlayback`/`isPlaying` stay `true`, so on return the HUD/watch-timer can show "playing" while the player is stopped.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift
 import Foundation
 import AVFoundation
+import UIKit
@@
-                if options.contains(.shouldResume) {
-                    AVPlayerPool.shared.play()
-                }
+                // Never restart audio from the background (ghost audio); the
+                // pool's foreground handler restores playback on return.
+                if options.contains(.shouldResume),
+                   UIApplication.shared.applicationState == .active {
+                    AVPlayerPool.shared.play()
+                }
```

### A9 — Batch reports `.completed` / 100% while some downloads permanently failed

- **Location:** `Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift:295-300` (`.completed` in the `else` of `if failedInBatch > 0 && completedInBatch == 0`; verified) with failures also counted toward the bar at `:306` (`finishedCount = completedInBatch + failedInBatch`).
- **Trigger:** Any batch where ≥ 1 reel fails all 3 retries (dead R2 link, size mismatch) but others succeed.
- **Impact:** The sheet shows "Completed" and a full bar while N reels are silently missing offline; a 3×-retried dead reel also pushes the bar to 100% mid-run.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
@@
-            if failedInBatch > 0 && completedInBatch == 0 {
-                state = .failed("All downloads failed in this batch.")
-            } else {
-                state = .completed
-                overallProgress = 1.0
-            }
+            if failedInBatch > 0 {
+                state = failedInBatch >= totalInBatch
+                    ? .failed("All downloads failed in this batch.")
+                    : .failed("\(failedInBatch) of \(totalInBatch) downloads failed — tap Retry.")
+            } else {
+                state = .completed
+                overallProgress = 1.0
+            }
```
(The existing `.failed` UI already offers Retry + Done, so the partial state is actionable.)

### A10 — `deleteBookmarkFile` unlinks a bookmark file the AVPlayer pool is actively streaming

- **Location:** `Sources/InstagramDigest/Engine/MediaCacheManager.swift:482` (`try? fm.removeItem(at: bookmarkDestURL)`) with no active-pool guard — unlike `ensureSpaceForBookmark` (`:243`) and `freeBookmarkStorage` (`:542`), which both skip `activeVideoPoolReelIDs`.
- **Trigger:** The feed is playing a reel whose resolved local source is the *bookmark* copy (common for the current week) and the user un-bookmarks it (`BookmarkController.remove` → `deleteBookmarkFile`).
- **Impact:** Eviction race with active playback: the file backing the playing item is unlinked mid-play. Usually masked by the open fd, but scrub-back/end-replay can stall or black-frame (remote fallback only after two stalls).
- **Fix:** defer the unlink until the reel leaves the active pool:

```diff
--- a/Sources/InstagramDigest/Engine/MediaCacheManager.swift
+++ b/Sources/InstagramDigest/Engine/MediaCacheManager.swift
@@
     private var activeVideoPoolReelIDs: Set<String> = []
+    private var pendingDeletions: Set<String> = []
@@
     public func setActiveVideoPoolReelIDs(_ ids: Set<String>, generation: UInt64 = 0) {
         ...
         self.activeVideoPoolReelIDs = ids
+        // Deferred unlinks: run once the reel is no longer bound to a slot.
+        for reelID in pendingDeletions where !ids.contains(reelID) {
+            pendingDeletions.remove(reelID)
+            deleteBookmarkFile(reelID: reelID)
+        }
     }
@@
     public func deleteBookmarkFile(reelID: String) {
         let bookmarkDestURL = pathResolver.bookmarkFileURL(for: reelID)
         let fm = FileManager.default
-        if fm.fileExists(atPath: bookmarkDestURL.path) {
+        if activeVideoPoolReelIDs.contains(reelID) {
+            // Never unlink a file the pool is streaming right now.
+            pendingDeletions.insert(reelID)
+        } else if fm.fileExists(atPath: bookmarkDestURL.path) {
```
(The existing DB update below still runs immediately so the UI flips off "Saved offline" right away.)

### A11 — Resume data is deleted when a download *starts*, not when it promotes

- **Location:** `Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift:313-315` (verified: `removePersistedResumeData(for: item.id)` immediately after `downloadTask(withResumeData:)`). The comment at `:403-405` ("IOS-P1-8: delete resume data only after the atomic promotion succeeds") describes an invariant that does not exist — by line 406 the bytes were long deleted.
- **Trigger:** App crash/jetsam while a resumed download is mid-transfer (250-reel batches make this likely eventually).
- **Impact:** Interrupted reels restart from 0 bytes instead of resuming — large redundant re-download after a crash.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
@@
         if let resumeData = loadPersistedResumeData(for: item.id) {
             task = urlSession.downloadTask(withResumeData: resumeData)
-            removePersistedResumeData(for: item.id)
+            // Keep the .dat until promotion succeeds (IOS-P1-8): a crash
+            // mid-download must be resumable from the persisted bytes.
         } else {
```
(Fresh errors/cancel overwrite it at `:434`, success removes it at `:406`, so no stale-file growth.)

---

## P2 — iOS UI & tests

### A12 — No empty-feed state: a zero-item digest renders a permanent black screen

- **Location:** `Sources/InstagramDigest/InstagramDigestApp.swift:141-309` (branch chain `isLoading` → `errorMessage` → `!pool.currentItems.isEmpty`, no fourth state), `:512` (`if !fetched.items.isEmpty { pool.setReels(...) }`), and `DigestDataService.swift:55` (an empty manifest still overwrites the on-disk cache).
- **Trigger:** Manifest decodes with 0 items (e.g. a bad publish), or a stale/empty fetch overwrites the cache before an offline launch.
- **Impact:** Pure black screen — no header, no grid, no Retry. The app is a brick until a good manifest lands; the overwritten cache also defeats the bundle fallback (`DigestDataService.swift:74`), so it stays black offline too.
- **Fix:** add the missing state (and never cache an empty manifest — see `DigestDataService.swift:55`, guard with `if !fetched.items.isEmpty`):

```swift
            } else {
                // Empty digest must never be a dead black screen.
                VStack(spacing: 16) {
                    Image(systemName: "tray")
                        .font(.system(size: 40))
                        .foregroundColor(.white.opacity(0.5))
                    Text("No reels in this week's digest yet.")
                        .font(.system(size: 15))
                        .foregroundColor(.white.opacity(0.8))
                    Button("Retry") { loadManifest() }
                        .padding(.horizontal, 24)
                        .padding(.vertical, 12)
                        .background(Color.white)
                        .foregroundColor(.black)
                        .clipShape(Capsule())
                }
                .accessibilityIdentifier("EmptyDigestView")
            }
```

### A13 — `testCaptionExpansionToggle` can never fail

- **Location:** `Tests/InstagramDigestUITests/InstagramDigestUITests.swift:201-211`.
- **Trigger:** (a) If the caption is missing the `if caption.waitForExistence(...)` block silently skips; (b) the assertion compares height *after two taps* to height *before both taps* — if the toggle is completely broken (both taps do nothing) the test **passes**.
- **Impact:** Caption expansion can regress to zero functionality with a green suite.
- **Fix:** require the element and assert each direction with `XCTNSPredicateExpectation` (frame grows on tap 1, shrinks on tap 2). If the seeded caption is too short to clamp, expose `isCaptionExpanded` as an accessibility value on `ReelCaptionText` and assert on that instead.

### A14 — Feed-navigation XCUITests assert only element existence

- **Location:** `Tests/InstagramDigestUITests/InstagramDigestUITests.swift:102-103` (`testGridNavigationToReel` asserts `ReelRankBadge` exists — true before the jump too), `:66-82` (`testStoryCategoryFiltering` asserts `creatorHandle.exists`), `:164-180` (`testVerticalFeedPaging` asserts `creatorHandle.exists` after swipes — always true), plus `Thread.sleep(0.5)` timing at `:72`, `:78`, `:173`, `:177`.
- **Impact:** Paging, grid-jump, and category-filter regressions pass silently; the suite is brittle where it should be behavioral.
- **Fix:** assert state transitions instead of existence — capture `ReelRankBadge.label` before each gesture and `XCTNSPredicateExpectation`-wait for it to change (and change back); for category filtering expose `isSelected` as an accessibility value on the chip.

### A15 — `Tests/InstagramDigestTests/UITests.swift` tests re-implemented copies of logic

- **Location:** `UITests.swift:8-25` (`shouldFailVertical` re-declared), `:37-70` (`resolveZone` mirror of `FeedPagerView.handleLongPress`, `FeedPagerView.swift:136-164`), `:74-87` (seek math mirror of `FeedPagerView.swift:189-194`), `:91-97` (`allIndices.filter` instead of `WatchedRules.unrecordedPredecessorIDs`), `:101-114` (`testMindfulSnoozeValidation` is tautological), `:199-221` (slot-index mirror).
- **Impact:** Change thresholds in `SeekPanGestureRecognizer.swift:36-45` or zone bounds in `FeedPagerView.swift:141-161` and every test stays green — they test local closures, not shipped code. False confidence.
- **Fix:** extract the production logic into testable statics (e.g. `FeedPagerView.Coordinator.resolveSpatialZone(x:y:width:height:)` and `shouldFailSeekForVerticalDominance(deltaX:deltaY:)`) and have both the gesture handlers and the tests route through them; delete `testJumpToNPredecessors` (already covered properly in `ModelTests.swift:117`) and replace `testMindfulSnoozeValidation` with an assertion against the persisted `DailyProgress.snoozeUntil`.

### A16 — EngineTests slot-rotation assertions are gated behind `if` — they can never fail

- **Location:** `Tests/InstagramDigestTests/EngineTests.swift:233-236` and `:318-321` (assertions inside `if slotItem?.reel.id == "r1"`), preceded by `Task.sleep(nanoseconds: 50_000_000)` at `:225` / `:312`.
- **Trigger:** If the async slot load hasn't populated after 50 ms (routinely slower for remote `.mp4` metadata), the `if` body is skipped and the test passes having asserted nothing; timing skew flips between vacuous pass and spurious failure.
- **Fix:** poll for the precondition with a bounded wait, then assert unconditionally:

```swift
        for _ in 0..<50 where pool.slotNext.slotItem?.reel.id != "r1" {
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        XCTAssertEqual(pool.slotNext.slotItem?.reel.id, "r1", "slotNext must preload the next reel")
```
(and the analogous change in `testSlotRotationBackwardContinuous`).

### A17 — `test300ReelsBundledManifestDecoding` hard-codes "exactly 300 reels" against live-changing content

- **Location:** `Tests/InstagramDigestTests/ModelTests.swift:181`.
- **Impact:** The weekly volume is not fixed (~250 reels is the norm; `scripts/topup_digest.py` only guards a *minimum*). A content refresh whose lossy-decoded count isn't exactly 300 turns CI red for a non-bug; today it's green for the wrong reason (it measures content volume, not decode health).
- **Fix:** assert the contract — `XCTAssertGreaterThanOrEqual(manifest.items.count, 100)` plus `XCTAssertEqual(manifest.items.count, manifest.count)` ("every bundled reel must decode").

### A18 — The produced IPA is unsigned — not installable as delivered

- **Location:** `.github/workflows/build-ipa.yml:72-82` (`CODE_SIGNING_ALLOWED=NO CODE_SIGN_IDENTITY="" CODE_SIGNING_REQUIRED=NO` for the `-sdk iphoneos` build) and `:84-89` (packaging).
- **Impact:** The workflow builds, runs both suites, and produces a structurally valid `Payload/*.app` zip, but iOS refuses to install it until a manual re-sign (AltStore/Sideloadly/TrollStore) that is neither automated nor documented.
- **Fix (either):** sign with `CODE_SIGN_STYLE=Manual`, `CODE_SIGN_IDENTITY=${{ secrets.IOS_SIGNING_IDENTITY }}`, `PROVISIONING_PROFILE_SPECIFIER=${{ secrets.IOS_PROVISIONING_PROFILE }}`; or if staying unsigned is intentional, rename the artifact and document the re-sign step in the workflow so "installable" is not implied.

### A19 — Missing XCUITest coverage for bookmarking persistence, the bookmark player, and playback behavior

- **Location:** `Tests/InstagramDigestUITests/InstagramDigestUITests.swift` (whole suite); dead test hooks at `Sources/InstagramDigest/InstagramDigestApp.swift:61-71` and `:531-546` (`-ui-testing-seed-mindful` — no test ever passes it). All identifiers exist in the UI but are unused by tests: `BookmarkGridItem_0`, `BookmarkPlayerCloseButton`, `BookmarkPlayerUnsaveButton`, `BookmarkPlayerShareButton`, `MindfulDailyModalTitle`.
- **Impact:** The most stateful surfaces (bookmark toggle → sheet → player → unsave, tap-to-pause, seek) have zero behavioral protection; regressions ship green.
- **Fix (minimal skeletons):** `testBookmarkPersistsIntoBookmarksSheet` (save → dismiss owner-key alert → chip → assert `BookmarkGridItem_0`), `testBookmarkPlayerUnsaveClosesWhenLastBookmarkRemoved`, and a playback test exposing `pool.isPlaying` as an accessibility value on `FeedCollectionView`.

---

## P2 — Python pipeline

### P2-10 — Cookie death in the shortfall top-up path is silent and discards discovered reels

- **Location:** `main.py:1086-1091` (shortfall resume feed top-up handler) vs the main Tier-3 path `main.py:1748-1771` (salvages `exc.partial`, sends cookie alert email + popup).
- **Trigger:** Session cookies die (login redirect) during the shortfall-resume Tier-3 top-up — the path login-time `resume_pending.sh` takes.
- **Impact:** `CookieExpiredException.partial` (externals already discovered) is dropped, no cookie alert is raised, and the run silently continues to the 5C gate (which, per P1-5, usually re-parks shortfall). The owner gets a vague "shortfall" alert instead of the actionable cookie alert, delaying the fix by days.
- **Fix:** mirror the main-path handler before `break` — salvage `exc.partial` into `ranked_reels` (re-ranking the combined list), then `notifier.send_cookie_alert_email()` in a try/except (see `main.py:1748-1771` for the exact pattern).

### P2-11 — JIT previous-week purge loses live-week protection when the digest is unreadable

- **Location:** `main.py:1929-1933` (verified: `keep_weeks = {live_week} if live_week and live_week != week_id else set()`; `purge_previous_weeks_videos` then deletes everything under `videos/` except the keep prefixes, `storage_r2.py:295-358`), contrast `main.py:240-254` where `_current_week_stray_keep_ids` returns `None` → skip purge.
- **Trigger:** `data/top100_digest.json` exists but is unreadable/missing `run_date` at the moment a weekly run reaches 5D.
- **Impact:** `_persisted_digest_week()` returns `""` → `keep_weeks = set()` → the purge deletes **the live digest week's videos** — the keys the Pages feed and iOS app are playing. If the run then aborts at the pre-flight quota check (`main.py:1939-1942`) or crashes before `save_digest_batch`, the live digest points at deleted keys: total playback loss for the week. The stray purger guards this case; the JIT purger does not.
- **Fix:** skip rather than purge unprotected:

```diff
--- a/main.py
+++ b/main.py
@@ -1929,7 +1929,11 @@
         live_week = _persisted_digest_week()
         keep_weeks = {live_week} if live_week and live_week != week_id else set()
-        if config.R2_ACCOUNT_ID:
+        live_digest_unreadable = config.DIGEST_BATCH_FILE.exists() and not live_week
+        if live_digest_unreadable:
+            logger.warning("Live digest unreadable; skipping previous-week purge "
+                           "so its videos survive (recover digest first).")
+        elif config.R2_ACCOUNT_ID:
             storage_r2.purge_previous_weeks_videos(
                 current_week_id=week_id, keep_week_ids=keep_weeks)
```

### P2-12 — `--expand` and `--reconcile` bypass the R2 quota and byte-budget guards

- **Location:** `main.py:2451-2529` (expand download+upload loop) and `main.py:517-525` (reconcile upload loop) — no `check_preflight_quota`, no `MAX_FEED_BATCH_BYTES` budget. Contrast the weekly path (budget `main.py:1874-1891`, quota gate `main.py:1939`).
- **Trigger:** `main.py --expand 500` (or the dashboard button, clamped only to 1..500 at `local_server.py:691-697`) on top of an existing ~5.8 GB week, or a large reconcile outbox.
- **Impact:** The self-imposed 8 GB safety quota and Cloudflare's 10 GB free-tier hard limit can be exceeded mid-batch → billing risk / partial upload failures. The weekly job's protections are bypassed by the manual paths.
- **Fix:** pre-flight the real batch bytes before the upload loops in `_run_expand` (before `main.py:2512`) and `_run_reconcile` (before `main.py:517`):

```python
    if config.R2_ACCOUNT_ID and config.R2_PUBLIC_DOMAIN:
        new_bytes = 0
        for r in downloadable:
            try:
                new_bytes += (week_videos_dir / _final_name(r, r["rank"])).stat().st_size
            except OSError:
                pass
        if not storage_r2.check_preflight_quota(estimated_new_bytes=new_bytes):
            logger.error("Expand aborted: batch would breach the R2 safety quota.")
            return 1
```

### P2-13 — Tier-3 top-up and expand bypass the cross-week seen-id dedup ledger

- **Location:** `main.py:1056` and `main.py:1680` (`existing_ids = {r["id"] for r in ranked_reels}` — this run only), `main.py:2198` (expand: digest ids only). The ledger (`main.py:375-429`) is consulted only at `main.py:1649`. Aggravated by `extractor.py:1796`: externals carry `timestamp: int(time.time())`, so the date cutoff can never retire them.
- **Trigger:** The discovery feed resurfaces a viral reel already published in a previous digest.
- **Impact:** Previously published external reels re-enter the digest via Tier 3 or `--expand`, breaking the "finite briefing / no resurfacing" promise and duplicating content across weeks.
- **Fix:** include the ledger in the exclusion ids at all three sites:

```diff
-                        existing_ids = {r["id"] for r in ranked_reels}
+                        existing_ids = {r["id"] for r in ranked_reels} | set(_load_seen_reel_ids())
```
(and the same union at `main.py:2198`).

### P2-14 — Sources with a category outside the fixed 6 are silently never scraped

- **Location:** `main.py:851-862` (`ordered_sources` built only from `cats = ["entertainment", "finance", "ai_tech", "niche", "health", "food"]`), same pattern at `main.py:1394-1403`. `ranker.CATEGORY_ALIASES` (`ranker.py:99-103`) proves legacy values like `"tech"` exist in real data, and `POST /api/channels/add` (`local_server.py:1774`) accepts arbitrary category strings.
- **Trigger:** A source whose `category` is `"tech"` (legacy), `"ai-tech"` (typo), or any custom string.
- **Impact:** The creator is silently excluded from `ordered_sources` → never visited, zero candidates, forever, with no log line. Currently latent (all 100 `sources.json` entries use canonical ids) but one typo recreates it.
- **Fix:**

```diff
--- a/main.py
+++ b/main.py
@@ -856,2 +856,5 @@
     ordered_sources: list[dict[str, Any]] = []
     cats = ["entertainment", "finance", "ai_tech", "niche", "health", "food"]
+    # Never silently drop sources whose category is outside the canonical 6
+    # (legacy aliases, dashboard typos) — interleave them last instead.
+    cats += sorted(c for c in by_cat if c not in cats)
```

### P2-15 — Unlocked read-modify-write of `sources.json` across threads and processes

- **Location:** `extractor.py:495-512` (`sync_following_accounts`: load → mutate → save, no lock; runs in a background thread from `local_server.py:1877-1887`), `local_server.py:1776-1814` and `:2022-2026` (dashboard endpoints guard only with the thread-only `_STATE_LOCK`), `audit_channels.py:229-231` / `:253-255` (separate process). The recommendation feedback file got a proper cross-process flock (`recommendations.py:61-98`); `sources.json` did not.
- **Trigger:** A dashboard "add channel" POST lands while the following-sync worker (or a concurrent `audit_channels --fix`) is between read and write.
- **Impact:** Lost update — the newly added channel vanishes, or the following-sync's import is discarded; a creator silently stops being scraped. Intermittent and hard to diagnose.
- **Fix:** add `atomic_io.sources_file_lock()` (a copy of `recommendations._feedback_file_lock` — flock on `data/.sources.lock`, reentrant via a thread-local depth counter) and wrap all three read-modify-write sections.

### P2-16 — Corrupt `sources.json` is quarantined and then overwritten by dashboard endpoints

- **Location:** `local_server.py:1777` (`_load_json_tolerant(config.SOURCES_FILE, [])`) → `:1797-1808` (append one handle, write) and `:2024-2026` (blacklist endpoint rewrites from the defaulted `[]`). `_load_json_tolerant` (`local_server.py:941-959`) renames corrupt bytes aside and returns the default.
- **Trigger:** `sources.json` is corrupt and the owner then adds a channel or blacklists someone from the dashboard.
- **Impact:** The 65-channel list is replaced by a one-entry (or empty) file; the next weekly run scrapes 1 creator → viability gate abort. Recovery requires restoring the `.corrupt-*` quarantine manually.
- **Fix:** refuse to overwrite when the existing file failed to parse (return 500 "sources.json unreadable — restore it before editing channels") and guard the blacklist endpoint's rewrite with `if isinstance(sources, list):`.

---

## P2 — Shell, ops, dashboard, CI

### P2-17 — TubeLM failure in the Friday chain is log-only, then the machine powers off

- **Location:** `run_friday_overnight.sh:85-89` (verified: `TUBELM_RC` captured and logged, never notified), poweroff at `run_friday_overnight.sh:114-121`.
- **Trigger:** `~/.tubelm/run_weekly.sh` exits non-zero — including rc=127 if the script is missing.
- **Impact:** The only trace is `tubelm=127` in `logs/friday_overnight.log`; the machine then powers off, so the user wakes to an off laptop with the TubeLM weekly sync silently missing. (The digest half alerts by email; the TubeLM half has no notification path.)
- **Fix:**

```diff
--- a/run_friday_overnight.sh
+++ b/run_friday_overnight.sh
@@ -86,6 +86,11 @@
 TUBELM_RC=$?
 set -e
 log "TubeLM finished with exit code $TUBELM_RC"
+if [ "$TUBELM_RC" -ne 0 ]; then
+    "$APP_DIR/.venv/bin/python" "$APP_DIR/notifier.py" --failure-alert \
+        --context "Friday chain: TubeLM weekly sync failed (rc=$TUBELM_RC)" \
+        --exit-code "$TUBELM_RC" >>"$LOG_FILE" 2>&1 || true
+fi
 log "Chain summary: tubelm=$TUBELM_RC digest=$DIGEST_RC"
```

### P2-18 — Stale expand checkpoint is treated as a live job → spurious auto-expansions at every login

- **Location:** `main.py:2552-2558` (success cleanup deletes only `checkpoint_file` and `read_path`, unlike sync's "delete the rest" at `main.py:920-926`), contradicting `resume_pending.sh:137-139`. Secondary divergence: `resume_pending.sh:143-150` picks the newest checkpoint **by mtime** while `main.py:2207-2214` resumes from `sorted(...)[-1]` **by name**.
- **Trigger:** An expand resumed across weeks leaves two checkpoint files; the next fully-successful expand deletes only two names, stranding the older file.
- **Impact:** The stranded `expand_checkpoint_*.json` shows as a permanent "STUCK" job (`local_server.py:783`) and `resume_pending.sh:140-161` fires `main.py --expand N --deploy` at **every login** — each finding "fresh reels" (`main.py:2281`) and deploying an unwanted +N digest, repeatedly.
- **Fix:** on success delete every expand checkpoint (`for done_file in config.DATA_DIR.glob("expand_checkpoint_*.json")`) instead of only `{checkpoint_file, read_path}`.

### P2-19 — Expand-resume outcome is unreported; "resume finished" is claimed even on failure

- **Location:** `resume_pending.sh:159-165`.
- **Trigger:** `main.py --expand "$TARGET" --deploy` fails during login auto-resume (cookie-expired exit 2 at `main.py:2364`, discovery failure at `main.py:2380/2389`, busy exit 3 at `main.py:2167`) — none of these email.
- **Impact:** Line 160 only logs the exit code; line 165 then notifies "Instagram Digest resume finished" regardless. The user reasonably believes the pending +100 job completed; the checkpoint retries only at the *next login*, so a failing expand can sit unrun for days.
- **Fix:** capture `EXPAND_RC=$?` and notify "expand top-up failed (rc=…) — checkpoint kept for next login" on non-zero, instead of the unconditional "finished".

### P2-20 — `logs/launch.log` grows without bound (only unrotated log; server logs every HTTP request)

- **Location:** `launch.sh:117` (`exec >>"$SCRIPT_DIR/logs/launch.log" 2>&1`, no rotation) — root cause: `local_server.py` never overrides `log_message`, so `BaseHTTPRequestHandler` writes one stderr line per request, and the dashboard polls `/api/sync-status` etc. every 5–60 s (`templates/dashboard.html:704-709`) against a server that stays up for weeks. The other three scripts cap logs at 10 MB.
- **Impact:** Unbounded disk/log growth (MB/week) and real errors drowned out in the only place bind failures are captured.
- **Fix:** `def log_message(self, fmt, *args): pass` on the request handler (or add the standard 10 MB cap in `launch.sh` before `:117` — bounds growth only across restarts).

### P2-21 — `install_launcher.sh` Desktop Entry values end up unquoted

- **Location:** `install_launcher.sh:14-16` (inside the double-quoted `DESKTOP_ENTRY` heredoc, `:8-21`): the `"` around `$SCRIPT_DIR/launch.sh` are shell quoting and **do not land in the value**.
- **Trigger:** The checkout path contains a space.
- **Impact:** The Desktop Entry spec splits `Exec=` on spaces — clicking the icon runs the wrong argv and the dashboard never starts. Works today only because the current path has no spaces.
- **Fix:** make the quotes literal (`Exec=\"$SCRIPT_DIR/launch.sh\"`, same for `Path=` and `Icon=`).

### P2-22 — `build-ipa.yml`: fragile simulator selection, masked boot failures, no job timeout, no unit-test diagnostics

- **Location:** `.github/workflows/build-ipa.yml:29-35` (jq picks the first available iPhone across all runtimes in JSON order — non-deterministic; `xcrun simctl boot … || true` masks boot failure; fallback literal `"iPhone 16"`), destinations matched by `name=` only at `:42` and `:55` ("multiple matching destinations" hazard), job at `:13-15` has no `timeout-minutes`, and the unit-test step (`:37-48`) writes no `-resultBundlePath` so the `if: failure()` upload (`:64-70`) only ever contains UAT results.
- **Impact:** Intermittent red builds tied to runner-image drift, opaque boot failures, potential hang toward GitHub's 6-hour default, and no artifact to debug unit-test failures. (The verification itself is genuine: both `-only-testing` targets exist and both suites run.)
- **Fix:** select one simulator by UDID (newest runtime) and address it with `id=`, fail loudly if none found, use `xcrun simctl bootstatus "$DEVICE_ID" -b`, add `timeout-minutes: 90`, and add `-resultBundlePath UnitResults.xcresult` + upload to the unit step.

---

## Checked-clean (highlights)

Full areas examined and found sound — no action needed:

- **Session/cookie handling:** step-0 probe session is closed before extraction (`main.py:694-709`); challenge vs login-redirect classification is consistent across all four call sites (`extractor.py:629-659`); soft-block detection strips scripts/captions before matching (`extractor.py:610-622`); `cookie_exporter.py` never clobbers a good `cookies.json` and writes 0600 temp+fsync+replace; CDN download handles 429/403/404/410 with ffprobe validation (`extractor.py:1395-1421`).
- **Checkpoint/resume (beyond P1-6):** week-drift adoption keeps `week_id` coherent (`main.py:904-908`); stale checkpoints are retired (renamed), never deleted; `ranked`/`publishing` resume reuses `downloaded_paths`/`uploaded_url_map` and re-upload is idempotent (`storage_r2.py:446-457`); expand resume dedups against the digest.
- **R2 purge safety (beyond P2-11):** JIT purge scoped to `videos/` with keep prefixes; orphan purge never trusts a torn digest and matches by reel-id suffix; retention requires date-in-key **and** mtime to agree (`storage_r2.py:156-172`); pre-flight usage probe fails closed (`main.py:832-836`, `storage_r2.py:106-130`).
- **Atomic writes/locking:** `atomic_io.py` (temp+fsync+replace+dir-fsync) is correct and used everywhere; cross-process pipeline flock with holder sidecar (`main.py:105-150`); recommendation feedback flock (`recommendations.py:61-98`).
- **Dashboard API guards:** path traversal contained (`local_server.py:1004, 1181, 1195`), discard allowlist (`:836-869`), week_id regex-restricted, CSRF via `_is_local_origin`, 4 MB body cap, RFC 7233 range streaming. `templates/dashboard.html` escapes every injected string (`esc()` at `:235`) — no XSS found; all 19 fetched endpoints match their handlers.
- **Shell:** `run_weekly.sh` (`set -euo pipefail`, exit-3 lock retry, notifier CLI contract verified) and `run_friday_overnight.sh` (window guard + stale-trigger alert, poweroff contract matches its header) are sound; lock coverage complete (`data/.pipeline.lock`, `data/.resume.lock`, launcher lock); systemd units valid (`OnCalendar=Fri *-*-* 18:00:00`, `Persistent=true`, env matches `resume_pending.sh:32`).
- **AVPlayerPool:** `Slot.teardown` (`AVPlayerPool.swift:41-68`) removes time observer, Combine KVO, both NotificationCenter tokens, disables the looper, nils the item; all async loads generation-fenced with `[weak self, weak slot]`; no retain cycles; end-of-item restart logic correct.
- **Rollover/cache safety:** `purgeOldWeekDirectory` pin-set computation cannot delete bookmarked media with an on-disk copy nor the 3 bound pool reels; `LibraryPathResolver` blocks traversal and re-stats at call time; cap arithmetic is decimal-consistent with no off-by-ones.
- **SwiftData:** per-call `ModelContext` strictly inside actors, no `@Model` crossing boundaries, all fields defaulted (lightweight-migration-safe); `BookmarkController` tombstone ordering and duplicate-insert rollback are correct.
- **View layer:** no force unwraps in view bodies, no retain cycles, `@State`/`@ObservedObject` usage correct, sheet resume logic (`wasPlayingBeforeSheet`) covers all dismiss paths, and every accessibility identifier referenced by the suite exists (except those noted in A19 as merely unused).
