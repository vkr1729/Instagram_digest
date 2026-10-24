# Implementation Plan: Zero-Cloud / Direct-Meta Stream Architecture (Hardened)

## Phase 0: Feasibility Gates & Baseline Configuration

### Step 0.1: Configuration Flags & Non-Destructive Mode
- **Files:** `config.py`, `.env.example`
- **Changes:**
  - Add `DIRECT_STREAM_MODE: bool = _env_bool("DIRECT_STREAM_MODE", True)` (defaulting to True on this branch).
  - Add dynamic paths derived at call time from `SITE_DIR`:
    - `direct_manifest_file() -> Path`: returns `config.SITE_DIR / "data_direct.json"`.
    - `DIRECT_PAGES_MANIFEST_URL: str = f"{PAGES_BASE_URL}/data_direct.json"`.
  - Add `COBALT_RESOLVER_API_URL: str = os.getenv("COBALT_RESOLVER_API_URL", "https://api.cobalt.tools/").rstrip("/")`.
  - Preserve all existing R2 and Cloudflare configuration variables intact for full backwards compatibility.
  - Pin legacy tests to `DIRECT_STREAM_MODE = False` so existing tests keep their verified semantics.

---

## Phase 1: Python Pipeline (Zero-R2 / Direct Stream Mode)

### Step 1.1: Direct Stream Gating across all Pipeline Entry Points
- **Files:** `main.py`
- **Changes:**
  - **Startup Gate:** In `main.py:956-960`, if `DIRECT_STREAM_MODE` is True, skip S3 client creation and bucket usage checks.
  - **Phase 5 Storage Gate:** In `main.py:2230-2435`:
    - Skip `storage_r2.purge_previous_weeks_videos`, `storage_r2.check_preflight_quota`, and all `storage_r2.upload_reel_to_r2` calls.
    - Validate local video downloads and extract accurate `size_bytes` and direct `video_cdn_url`.
    - Skip Telegram bookmark notifier setup and Cloudflare Worker sync.
  - **Subcommands:** In `--expand`, `--build-only`, and `--deploy-only`, enforce direct mode gating without falling through to S3.

### Step 1.2: Dedicated Direct Manifest Writer (`write_direct_manifest`)
- **Files:** `site_builder.py`
- **Changes:**
  - Create pure function `write_direct_manifest(items: list[dict], run_date: str, output_path: Path) -> Path`:
    - Iterates over validated items:
      - Sets `video_url` to the exact validated direct Meta stream URL (`video_cdn_url`).
      - Strips `r2_url`.
      - Sets canonical Instagram reel URL (`url: "https://www.instagram.com/reel/{id}/"`).
      - Sets absolute GitHub Pages thumbnail URL: `f"{config.PAGES_BASE_URL}/thumbnails/{reel_id}.jpg"`.
      - Retains exact `size_bytes`, `run_date`, `generated_at`, and `count`.
    - Durably writes `output_path` (`site/data_direct.json`) using `atomic_io.durable_write_json`.
    - Does NOT mutate caller's `items` or touch production `site/data.json`.

### Step 1.3: Isolated GitHub Pages Direct Channel Deployment
- **Files:** `site_builder.py`
- **Changes:**
  - In `deploy_to_gh_pages()`:
    - When `DIRECT_STREAM_MODE` is True:
      - Overlay only `data_direct.json` and new direct thumbnails onto the `gh-pages` worktree.
      - **CRITICAL INVARIANT:** Byte-verify that production `data.json` exists and remains 100% untouched.
      - Commit and push without deleting legacy files.
      - Poll `DIRECT_PAGES_MANIFEST_URL` (verifying `run_date` and valid direct URLs).
      - Write sentinel `data/deployed_direct_week.json` upon confirmed serving (leaving `data/deployed_week.json` untouched).

---

## Phase 2: iOS Swift Models & Channel-Isolated Manifest Routing

### Step 2.1: Reel Item Model & Canonical URL Preservation
- **Files:** `Sources/InstagramDigest/Models/ReelItem.swift`
- **Changes:**
  - Preserve `videoUrl` exactly (including query parameters and hex signature tokens).
  - Add optional canonical permalink property: `public let canonicalUrl: URL?` (decoded from `url` or constructed from `id`).
  - Retain `sizeBytes` mapping.

### Step 2.2: Channel-Isolated Manifest Routing in `DigestDataService`
- **Files:** `Sources/InstagramDigest/Engine/DigestDataService.swift`
- **Changes:**
  - Add direct manifest constants:
    - `public static let directManifestURL = URL(string: "https://vkr1729.github.io/Instagram_digest/data_direct.json")!`
    - Cache file: `manifest_cache_direct.json` in `pathResolver.mediaCacheBaseURL`.
  - When running in direct mode:
    - Route manifest fetches to `directManifestURL` with fallback ONLY to `manifest_cache_direct.json`.
    - Never fall back to R2 or production `data.json`.
    - Reject empty manifests atomically.
  - Disable legacy remote bookmarks sync calls (`fetchRemoteBookmarks`) in direct mode.

---

## Phase 3: iOS Storage Rollover & Bulk Offline Caching Engine

### Step 3.1: Coordinated Single-Transaction Rollover in `MediaCacheManager`
- **Files:** `Sources/InstagramDigest/Engine/MediaCacheManager.swift`
- **Changes:**
  - Remove manifest-arrival purge from `FeedMainView` to prevent wiping offline media prematurely.
  - Implement single-transaction rollover method:
    `public func executeRolloverPurge(forNewWeek incomingWeekID: String) async throws`
    - Cancels active in-flight downloads and pauses old playback.
    - Deletes all prior week directories (`videos/{oldWeekID}`, `.part` files, thumbnails, stale resume data).
    - Preserves incoming week files if already partially downloaded.
    - Preserves SwiftData bookmark metadata rows (while clearing legacy isolated `Bookmarks/` MP4 files).
    - Ensures device storage strictly maintains the ~2.5–3.5 GB ceiling.

### Step 3.2: Download Coordinator Hardening
- **Files:** `Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift`
- **Changes:**
  - State machine with unified progress reporting:
    - Single state enum: `.idle`, `.preparing`, `.downloading(ready: Int, total: Int, failed: Int)`, `.completed(ready: Int, failed: Int)`, `.failed(String)`.
  - When user taps "Download Week":
    - Enters `.preparing` state (disables duplicate taps).
    - Executes `MediaCacheManager.shared.executeRolloverPurge(forNewWeek:)`.
    - Enforces direct batch storage preflight with reclaimable space accounted for.
    - Launches bounded queue (concurrency: 3) downloading to `.part` files keyed by `(weekID, reelID)`.
    - Promotes `.part` files via `MediaCacheManager.promotePartFile` with size and non-zero byte validation.
    - Honors 403/410 by pausing and requesting a single debounced manifest refresh from GitHub Pages.

---

## Phase 4: Dynamic Resolver & Bookmarks Architecture

### Step 4.1: Public Media Resolver (`BookmarkResolver`)
- **Files:** `Sources/InstagramDigest/Engine/BookmarkResolver.swift` (new file)
- **Changes:**
  - Actor `BookmarkResolver` with injected `URLSession` (ephemeral, timeout: 15s):
    - Configured endpoint: `https://api.cobalt.tools/` (or user setting).
    - Request: POST JSON `{"url": canonicalPermalink}` with headers `Accept: application/json`, `Content-Type: application/json`.
    - Response parsing:
      - Supports `tunnel` and `redirect` status types; extracts HTTPS `url`.
      - Rejects `local-processing` (unsupported) and `picker`.
      - Maps HTTP 429 to `.rateLimited(retryAfter: TimeInterval?)`.
      - Maps 401/403/Turnstile HTML to `.authRequiredOrBlocked`.
      - Maps network errors to `.offline`.
    - Bounded retries: max 1 retry with exponential backoff on 429/5xx.
    - Resolves on demand; does NOT persist resolved streaming URLs in database.

### Step 4.2: Bookmark Controller & Playback Routing
- **Files:** `Sources/InstagramDigest/Engine/BookmarkController.swift`, `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift`
- **Changes:**
  - In direct mode:
    - Bypass Cloudflare Worker/D1 sync and Telegram forward.
    - Save bookmark as metadata row with `canonicalUrl`, `reelID`, `weekID`, `caption`, `creatorHandle`, `thumbnailUrl`.
    - Stop creating isolated permanent `Bookmarks/{id}.mp4` copies.
  - In `BookmarkPlayerOverlay.loadVideo()`:
    - Check if active week's local file exists in `videos/{bookmark.weekID}/{id}.mp4`. If yes, play local file immediately (offline MRT ready!).
    - If local file missing (prior week purged):
      - If network offline: show "Online required for this older bookmark" without spinning player.
      - If network online: invoke `BookmarkResolver.shared.resolve(url:)`, receive fresh MP4 URL, and stream directly in AVPlayer.
  - One-time migration: purge legacy isolated `Bookmarks/` MP4 files to reclaim device storage.

---

## Phase 5: iOS UI Integration & Feedback

### Step 5.1: 1-Tap 'Download Week for Offline' Home Banner
- **Files:** `Sources/InstagramDigest/InstagramDigestApp.swift` (`FeedMainView`), `Sources/InstagramDigest/Views/Feed/HeaderBarView.swift`
- **Changes:**
  - When active week is un-downloaded:
    - Display prominent 1-tap banner: "⚡ Download Week for Offline (~3 GB)".
  - While downloading:
    - Display live progress banner: "Downloading: 42/250 ready (2 failed) — Tap for details".
  - When finished:
    - Display green badge: "✓ Ready Offline (250/250)".

### Step 5.2: Bookmarks Sheet Three-State Indicators
- **Files:** `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift`
- **Changes:**
  - Derive badge from filesystem and week:
    - `Offline ready` (green icon): local feed file is present on disk.
    - `Download required` (amber icon): current week, but not downloaded yet.
    - `Online required` (globe icon): older bookmark whose local week was purged; requires network for dynamic stream resolution.

---

## Phase 6: Automated Verification & Test Matrix

### Step 6.1: Python Test Suite
- **File:** `tests/test_direct_stream_mode.py`
- **Tests:**
  1. `test_full_call_graph_bypass()`: Verify all R2/S3 methods are bypassed when `DIRECT_STREAM_MODE = True`.
  2. `test_legacy_parity()`: Verify legacy mode (`DIRECT_STREAM_MODE = False`) still performs standard R2 operations.
  3. `test_direct_manifest_generation()`: Verify `data_direct.json` has exact Meta URLs, canonical URLs, absolute thumbnails, and correct `size_bytes`.
  4. `test_isolated_gh_pages_deploy()`: Verify production `data.json` is preserved 100% byte-for-byte during direct manifest deploy.

### Step 6.2: Swift Test Suite
- **File:** `Tests/InstagramDigestTests/DirectStreamTests.swift`
- **Tests:**
  1. `testDirectManifestDecoding()`: Verify `DigestDataService` decodes `data_direct.json` with canonical permalinks and direct URLs.
  2. `testRolloverTransaction()`: Verify `executeRolloverPurge` deletes old week directories before starting new week downloads.
  3. `testBookmarkResolverContracts()`: Stub `URLProtocol` tests verifying `tunnel`, `redirect`, `local-processing` rejection, 429 backoff, and offline handling.
  4. `testBookmarkPlaybackRouting()`: Verify local file is preferred when present, and resolver is only invoked for purged/older reels.
