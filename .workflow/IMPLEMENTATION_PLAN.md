# Implementation Plan: Final Refactor & Permanent Repository Lockdown (§6) — Hardened

## Overview & Architecture
This plan executes the permanent repository freeze for Instagram Digest (`arch/direct-meta-stream` branch) per `.workflow/REQUIREMENTS.md` §6, incorporating all findings from the Frontier Architectural Review (`.workflow/FRONTIER_PLAN_REVIEW.md`). The implementation adheres strictly to single-person personal use (zero enterprise overhead), reorders phases so the app compiles and functions at every milestone, introduces honest on-disk cache census states, implements GitHub Release video asset upload & streaming with silent background sync, eliminates main-thread thumbnail decode jank, and certifies the codebase with Swift 6 strict concurrency before locking the repository.

---

## Phase A: Cache Census & Honest Completed States

### Step A.1: Single Source of Truth Cache Census Helper
- **Files:** `Sources/InstagramDigest/Engine/LibraryPathResolver.swift`
- **Changes:**
  - Add helper `public func cacheCensus(reels: [ReelItem], weekID: String) -> (cached: Int, total: Int)`:
    - Counts non-empty local video files using `isLocalFileAvailable(for: weekID, reelID: reel.id)`.
    - Pure, read-only filesystem check, safe to call off the main thread.
- **Tests:** Add unit tests in `Tests/InstagramDigestTests/DirectStreamTests.swift` testing empty directory, partial, full, and zero-byte exclusion.

### Step A.2: Honest Completed State in `DownloadAllSheet`
- **Files:** `Sources/InstagramDigest/Views/Modals/DownloadAllSheet.swift`
- **Changes:**
  - In `.idle` state, evaluate `cacheCensus` asynchronously via `.task` off the main thread.
  - When `cached == total`:
    - Display completed UI: "All reels downloaded and ready offline!" with green checkmark icon.
    - Hide the "Start Download" button.
    - Present a single prominent "Done" button that dismisses the sheet.
  - When `cached < total`:
    - Display "Ready to download \(total - cached) reels (\(cached) already cached)".
    - "Start Download" initiates download only for the pending items.

### Step A.3: Honest Completed State in `SettingsSheetView`
- **Files:** `Sources/InstagramDigest/Views/Modals/SettingsSheetView.swift`, `Sources/InstagramDigest/InstagramDigestApp.swift`
- **Changes:**
  - Pass `reels: [ReelItem]` and `weekID: String` into `SettingsSheetView` from `FeedMainView` (mirroring `DownloadAllSheet` inputs).
  - In `SettingsSheetView`, run `cacheCensus` on appear via `.task`.
  - If `cached == total`:
    - Display row: `Label("All Reels Downloaded (\(cached)/\(total))", systemImage: "checkmark.circle.fill")` in green.
  - If `cached < total`:
    - Display row: `Label("Download All for Offline (\(total - cached) pending)", systemImage: "arrow.down.circle")`.
  - Tapping opens `DownloadAllSheet`.

---

## Phase B: GitHub Release Video Upload & Tier 2 Streaming (Manual Proven Path)

### Step B.1: SwiftData Model & DTO Extension
- **Files:** `Sources/InstagramDigest/Models/DigestState.swift`, `Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift`
- **Changes:**
  - In `BookmarkItem`:
    - Add additive SwiftData fields: `public var videoAssetName: String? = nil` and `public var releaseTag: String? = nil` (lightweight migration safe).
  - In `BookmarkSyncDTO`:
    - Support `video_asset_name: String?` and `release_tag: String?`.
  - Round-trip these fields in `exportBookmarksJSON` and `importBookmarks`.
  - Add pure helper `public static func releaseTag(for date: Date) -> String` using Gregorian UTC Calendar (`v{YYYY}`). Add unit test covering Dec 31 / Jan 1 boundary.

### Step B.2: Video Asset Upload to GitHub Releases
- **Files:** `Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift`
- **Changes:**
  - Implement video upload logic during `syncWithGitHub`:
    - Group bookmarks requiring upload by `releaseTag`.
    - For each release tag:
      - GET `/repos/{repo}/releases/tags/{tag}`. If 404, create release via POST `/repos/{repo}/releases`. Treat 422 as success/already exists.
      - GET `/repos/{repo}/releases/{release_id}/assets?per_page=100` to list existing assets.
      - Strip template `{?name,label}` from `upload_url`.
      - For each bookmark needing upload: locate local file via `LibraryPathResolver.shared.resolvedLocalFileURL(for: bookmark.weekID, reelID: bookmark.reelID)`.
      - Sanitize ID via `LibraryPathResolver.sanitizeComponent`.
      - If `{safeID}.mp4` is not in release assets, upload via `uploadTask(fromFile:)` with `Content-Type: video/mp4`. Treat 422 as success (already exists).
      - On successful upload, record `videoAssetName = "{safeID}.mp4"` and `releaseTag = tag` on the local `BookmarkItem`, and set `dtoMutated = true`.
    - Push `bookmarks.json` if `dtoMutated || newlyAddedLocal > 0 || remoteDTOs.isEmpty`.
    - Await video uploads before pushing `bookmarks.json`.

### Step B.3: On-the-Fly GitHub Release Video Streaming
- **Files:** `Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift`, `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift`
- **Changes:**
  - In `GitHubBookmarkSyncService`:
    - Add `resolveReleaseStreamURL(reelID: String, year: Int, repo: String, pat: String) async -> URL?`:
      - Uses a dedicated ephemeral `URLSession` with a `URLSessionTaskDelegate` that cancels redirects and captures the HTTP 302 `Location:` header pointing to AWS S3.
      - Returns the pre-signed S3 streaming URL.
      - Maintain a memory-only short-lived cache `[reelID: (url: URL, fetchedAt: Date)]` (TTL ~4 mins). Never persist to disk/database.
  - In `BookmarksSheet.swift`:
    - Integrate Tier 2 into `loadVideo`:
      1. Tier 1 (Local Disk Vault): If video exists locally on disk, play immediately.
      2. Tier 1.5 (Active-Week Stored URL): If `weekID == currentActiveWeekID` and `videoUrl != nil` and online, attempt playback. Wire `failObserverToken` and `statusObserver` with a single-shot guard to fall through to Tier 2 on failure.
      3. Tier 2 (GitHub Release Streaming): If PAT and repo configured, resolve streaming URL via `resolveReleaseStreamURL` and stream via `AVPlayer`. Fall through to resolver/Tier 3 on failure.
    - Rename `isResolving` to `isLoadingVideo` so async resolution shows a smooth loading state instead of a dead screen.
- **Tests:** Add unit tests for `releaseTag(for:)`, upload URL template stripping, 422 handling, and S3 redirect capture via `MockURLProtocol`.

---

## Phase C: Resolver Purge, Dead UI Removal & Tier 3 Web Fallback

### Step C.1: Delete Resolver & Banner Files and Call Sites
- **Files:**
  - Delete `Sources/InstagramDigest/Engine/BookmarkResolver.swift`.
  - Delete `Sources/InstagramDigest/Views/Feed/OfflineCacheBanner.swift`.
  - Remove `OfflineCacheBanner` block in `InstagramDigestApp.swift` (lines 349–359).
  - Remove `ResolverResult` enum, `playViaResolver` method, and resolver-specific error branches from `BookmarksSheet.swift`.
  - Add versioned one-time cleanup in `InstagramDigestApp.swift` (`hasPurgedCobaltKeys_v1`) to remove `cobalt_api_url`, `cobalt_api_key`, `cobalt_api_token` from `UserDefaults`.
  - Remove `COBALT_RESOLVER_API_URL` from `config.py` and `.env.example`.
- **Note:** Keep `ResolverTests.swift` intact (it tests `LibraryPathResolver`). Only delete the `BookmarkResolver` tests in `DirectStreamTests.swift`.

### Step C.2: Complete Tier 3 Web Fallback & Badges
- **Files:** `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift`, `Tests/InstagramDigestUITests/InstagramDigestUITests.swift`
- **Changes:**
  - In `BookmarksSheet.swift`:
    - Tier 3 state: When not in local cache and not in GitHub, show clean "Open on Instagram" button.
    - If offline and not in local cache, show "You're offline — this bookmark isn't downloaded".
    - Update bookmark badges:
      - Cached: checkmark (`checkmark.circle.fill`).
      - Active-week uncached: download arrow (`arrow.down.circle`).
      - GitHub-backed: cloud (`cloud.fill`).
      - Web-only: globe / safari (`safari`).
  - Update `InstagramDigestUITests.swift` to match the new badge and Tier 3 identifiers.
- **Verification:** `git grep -i cobalt Sources/` and `git grep -i cobalt Tests/` return 0 hits.

---

## Phase D: FeedCell Zero-Thumbnail Revert (60 FPS Swiping)

### Step D.1: Revert `FeedCell` to Pure `AVPlayerLayer`
- **Files:** `Sources/InstagramDigest/Views/Feed/FeedPagerView.swift`, `Sources/InstagramDigest/Views/Feed/ReelCardView.swift`
- **Changes:**
  - In `FeedPagerView.swift`:
    - In `FeedCell`: Remove `posterImageView`, `imageTask`, and all synchronous `UIImage(contentsOfFile:)` disk reads and `URLSessionDataTask` loads from `configure(reel:isCurrent:)`.
    - `FeedCell` contains only `PlayerContainerView`. `configure` executes minimal setup (`playerContainerView?.isHidden = false`).
    - `prepareForReuse` immediately detaches `pc.playerLayer` via `AVPlayerPool.shared.detachLayer` to eliminate stale frame flashes.
  - In `ReelCardView.swift`:
    - Set `PlayerContainerView.backgroundColor = .black` so unattached or cold slots display clean black without flashing.
- **Verification:** `git grep "UIImage(contentsOfFile" Sources/InstagramDigest/Views/Feed/` returns 0 hits; buttery smooth 60 FPS scrolling.

---

## Phase E: Silent Background Sync & Local Vault Retention

### Step E.1: Centralized Silent Sync Engine
- **Files:** `Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift`, `Sources/InstagramDigest/Engine/BookmarkController.swift`, `Sources/InstagramDigest/InstagramDigestApp.swift`
- **Changes:**
  - In `GitHubBookmarkSyncService`:
    - Add `private var isSyncing: Bool = false` and `private var needsResync: Bool = false` to coalesce overlapping sync passes.
    - Add entry point `public func syncIfNeeded(context: ModelContext, force: Bool = false) async`.
    - Cellular Guard: Gate video asset uploads on `!Reachability.shared.isExpensive` (or non-cellular network). Metadata and thumbnails always sync.
  - In `BookmarkController`:
    - Trigger `syncIfNeeded` in `Task.detached` on `add` and `remove`.
  - In `InstagramDigestApp.swift`:
    - Trigger `syncIfNeeded` on scene phase `.active` (app launch / foreground).
  - In `BookmarksSheet.swift`:
    - Route on-appear sync through `syncIfNeeded`.

### Step E.2: Local 50-Reel Vault & Upload-Before-Evict Invariant
- **Files:** `Sources/InstagramDigest/Engine/MediaCacheManager.swift`
- **Changes:**
  - Ensure `syncWithGitHub` NEVER deletes local files.
  - Enforce the 50-reel LRU cap (or user-configured cap: 25/50/100/unlimited) in `MediaCacheManager.pruneBookmarksToCap`:
    - When selecting eviction victims, prioritize items with `videoAssetName != nil` (already uploaded to GitHub) before evicting items that have not yet uploaded.
    - Preserve SwiftData record and portrait thumbnail `.thumb.jpg` on disk upon eviction.
- **Tests:** Add unit tests verifying `syncIfNeeded` coalescing, cellular gating, and upload-before-evict prioritization.

---

## Phase F: Swift 6 Strict Concurrency, Tests & CI Packaging

### Step F.1: Swift 6 Strict Concurrency Audit
- **Files:** All modified Swift files in `Sources/InstagramDigest/`
- **Changes:**
  - Ensure all actor isolation (`@MainActor`, `actor MediaCacheManager`), `Sendable` types, and task boundaries compile cleanly with zero warnings under complete strict concurrency.

### Step F.2: Test Verification & Pipeline Execution
- **Files:** `tests/test_direct_stream_mode.py`, `Tests/InstagramDigestTests/DirectStreamTests.swift`
- **Changes:**
  - Run local Python tests (`pytest tests/test_direct_stream_mode.py`).
  - Commit all changes to branch `arch/direct-meta-stream` and push to origin.
  - Monitor GitHub Actions workflow (`.github/workflows/build-ipa.yml`) for macOS test execution and IPA packaging.
  - Download and inspect `build_artifacts/InstagramDigestDirect.ipa`.

---

## Exit Criteria
1. `rg -i cobalt Sources/ Tests/ config.py .env.example` returns 0 hits.
2. `rg OfflineCacheBanner Sources/` returns 0 hits.
3. `rg 'UIImage\(contentsOfFile' Sources/InstagramDigest/Views/Feed/` returns 0 hits.
4. DownloadAllSheet and Settings show honest completed states ("All Reels Downloaded (250/250)").
5. Bookmarks playback honors Tier 1 -> Tier 1.5 -> Tier 2 -> Tier 3 without captcha dialogs.
6. Newly bookmarked videos upload to GitHub Release assets on sync, retaining 50 reels locally.
7. GitHub Actions CI builds and packages verified `InstagramDigestDirect.ipa`.
8. Repository declared locked.
