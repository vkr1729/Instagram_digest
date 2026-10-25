# Requirements Specification: Zero-Cloud / Direct-Meta Stream Architecture

## 1. Project Overview & Target User Anchor
- **Project Name:** Instagram Digest (Direct-Meta Stream / Zero-Cloud Branch)
- **Target User & Scale:** Single-person personal use (Kedarnath Reddy). Strict personal-use constraints; zero multi-tenant overhead.
- **Primary Objective:** Eliminate Cloudflare R2 object storage and Telegram forwarding, replacing them with a client-side device caching and on-demand Meta dynamic stream resolution architecture, while preserving full app feature parity and seamless offline playback on iOS.
- **Core Value Proposition:** Zero cloud storage costs, zero storage quota ceiling headaches (no 8 GB/10 GB limits), clean and resilient single-user consumption on iPhone during underground MRT commutes without having the native Instagram app installed.
- **Non-Destructive Refactor Invariant:** Do not delete existing files or infrastructure files permanently (keep `storage_r2.py`, `cloudflare/`, etc. intact in the repo). Make clean code-level toggles/switches or modular bypasses so the pipeline stops referring to Cloudflare R2 and Telegram services on this branch, allowing easy benchmarking and side-by-side comparison with main.
- **Isolated Benchmarking Channel:** In this branch, the pipeline deploys to a dedicated manifest path (`site/data_direct.json` deployed to `https://vkr1729.github.io/Instagram_digest/data_direct.json`) on GitHub Pages. Production `data.json` with R2 URLs remains untouched on GitHub Pages, ensuring safe side-by-side comparison.

## 2. Functional Requirements (Scope Matrix)
- **Weekly Media Delivery & Workstation Pipeline:**
  - `main.py` continues to discover, rank, and curate the weekly Top 250 reels using Chrome session cookies on the local workstation.
  - Video download to local disk continues locally for processing/validation and extracting metadata (like exact `size_bytes`), but R2 upload is bypassed via a clean toggle (`DIRECT_STREAM_MODE = True`).
  - The static manifest (`site/data_direct.json`) embeds the direct Meta CDN stream URLs (`video_cdn_url`) freshly resolved at sync time, along with thumbnail URLs hosted on GitHub Pages (`/thumbnails/{id}.jpg`) and `size_bytes`.
  - GitHub Pages (`https://vkr1729.github.io/Instagram_digest/data_direct.json`) is the primary manifest host for this branch.
- **iOS App Weekly Bulk Caching (Offline MRT Commute):**
  - Priority 1 target is the native iOS Swift app.
  - When a new week arrives, the app presents an auto-prompt / 1-tap prominent "Download Week for Offline" action on the home screen with a live background download progress indicator.
  - **Storage Discipline Invariant (Strict ~3 GB Cap):** When the user initiates "Download Week", the app immediately purges the previous week's local video directory before downloading the new week. This strictly enforces a maximum device storage footprint of ~2.5–3.5 GB (no double-batch overlap).
  - `DownloadAllCoordinator` bulk-caches the 250 reels to the iPhone's local storage within the fresh 48-hour window before Meta signed CDN URLs expire:
    - Per-item atomic promotion: downloads to `.tmp` file and atomically renames to final cache path upon verifying non-zero bytes and playability.
    - Keyed by `(week_id, reel_id)`.
    - Resumable queue with partial success tracking (`ready` vs `failed`).
  - `AVPlayerPool` streams from local disk files when cached, providing instant offline playback during underground MRT commutes with zero buffering.
  - If links expire before download completes, the workstation pipeline serves as the authoritative bulk URL refresher.
- **Bookmarks Lifecycle & Dynamic Resolver:**
  - Bookmarks only store the reel reference (ID, URL, creator handle, caption, thumbnail, date) — no cloud storage (R2) and no Telegram bot forward.
  - **Active Week Phase:** While the current week's local cache exists on the phone, bookmarked reels play instantly from the local cache.
  - **Long-Term / Expired Phase:** Once the active week's cache is purged and original Meta CDN URLs expire, the iOS app dynamically resolves fresh playable MP4 stream URLs on the fly using a public Cobalt API instance (`https://api.cobalt.tools/`) with bounded retries and timeout backoff.
  - When offline in the MRT tunnel, the app displays an honest "Online required for older bookmarks" badge instead of hanging or spinning.
  - The user does NOT have the native Meta Instagram app installed on their phone; all playback remains within the app.

## 3. Interview Record & Decision Log
| # | Architectural Question | Recommended Approach | Evaluated Alternatives | User Decision / Rationale |
|---|------------------------|----------------------|------------------------|---------------------------|
| 1 | Overcoming Meta CDN URL Expiration | Pre-cache weekly batch to device within fresh window | Local server streaming, Direct static URLs (403 after 72h) | Confirmed: Pre-cache weekly batch directly to phone (Device CacheStorage / iOS local storage) within 48h fresh window. Zero cloud storage. |
| 2 | Bookmarks Persistence & Playback | Dynamic on-demand scrape when played | Permanent device cache, Telegram backup | Confirmed: Only store reel reference (URL/ID); scrape fresh stream URL dynamically from Meta on-demand when playing. |
| 3 | Dynamic Scraper Execution Backend | Public media resolver API (Cobalt / SnapInsta backend) | Local server, Cloudflare Worker, In-app webview | Confirmed: Call public resolver API on the fly to get fresh MP4 stream directly in iOS app (no Meta app installed). |
| 4 | On-Device Bookmark Caching Lifecycle | Temporary cache during active week, wipe on new week, dynamic fallback | Permanent on-device cache | Confirmed: Bookmarks use current week's local cache while active (for underground MRT commute). When new week arrives, cache wipes; older bookmarks dynamically fetch fresh stream from Meta via resolver API. |
| 5 | Cloudflare R2 & Telegram Deprecation | Non-destructive code-level bypass | Hard file deletion, Keep Telegram | Confirmed: Code-level bypass to stop referring to Cloudflare and Telegram services in this branch; preserve files for side-by-side comparison. |
| 6 | iOS Bulk Download Trigger | Auto-prompt / 1-tap prominent 'Download Week for Offline' | Silent auto-download, Hidden settings button | Confirmed: Auto-prompt / 1-tap prominent 'Download Week for Offline' action on home screen when new week arrives, with background progress indicator. |
| 7 | Side-by-Side Manifest Channel | Dedicated `data_direct.json` on GitHub Pages | Overwrite production `data.json` | Confirmed: Deploy to dedicated `data_direct.json` so production R2 manifest remains untouched for safe testing. |
| 8 | Cache Purge Timing on Rollover | Immediate wipe upon initiating new week download | Overlap two weeks (5-7 GB peak) | Confirmed: Immediately wipe old week when initiating 'Download Week' to strictly maintain ~3 GB device storage ceiling. |
| 9 | Bookmark Resolver Contract | Public Cobalt API with graceful offline badge | Custom proxy, Configurable URL | Confirmed: Use public Cobalt API with bounded timeout/retry, showing 'Online required' badge when offline. |

## 4. Playback Guarantee & Offline Matrix
| Item State | Offline (No Network / MRT) | Online (Wi-Fi / 5G) |
|---|---|---|
| Active week reel, cached locally (`.mp4` on disk) | 100% offline playback from local file | Plays from local file (zero bandwidth) |
| Active week reel, uncached / download pending | Shows 'Download required' / retry | Streams from fresh Meta CDN URL or downloads |
| Older bookmarked reel (previous week purged) | Shows 'Online required for this older bookmark' | Dynamically resolves via Cobalt API & streams |
| Reel metadata / caption / thumbnail | Instant from local cache / GitHub Pages | Instant from local cache / GitHub Pages |

## 5. Refinements & User Feedback Addendum (Oct 24–25, 2026)
- **UI & Presentation Hardening:**
  - Remove feed banner ("All 250 Reels Cached Offline") and header pill ("Offline · 250 downloaded") from production main feed.
  - Fix squished thumbnails: enforce crisp 9:16 vertical portrait covers across GridView and BookmarksSheet.
  - Feed Chronological Sort ("Chronological (Newest)") immediately reorders the live feed pager and persists across launches.
  - Grayscale Detox Mode applies across both feed and BookmarksSheet.
  - Zero Cloudflare prompts: completely excise "Cloudflare Owner Key" dialogs and remote worker calls.
- **Bookmarks Storage Architecture (Single Repo + GitHub Sync):**
  - Target single repository: `vkreddy1729-ops/ig-bookmarks` (or custom user-configured repo).
  - Git history contains lightweight `bookmarks.json` and permanent portrait thumbnails in `thumbnails/{id}_portrait.jpg`.
  - Binary video release asset uploads are deferred; video playback is local-first from device cache and dynamically resolved via Cobalt API upon demand.
  - Automatic background sync on BookmarksSheet open + manual "Sync Bookmarks Now" button.
- **Offline Bookmarking Storage Cap & LRU Pruning:**
  - Configurable in Settings: Keep latest 25, 50 (default), 100, or Unlimited offline bookmark videos.
  - Pruning evicts older `.mp4` video files beyond the cap while preserving all SwiftData metadata and the local `.thumb.jpg` portrait thumbnail.
- **Self-Contained JSON Export & Import:**
  - Export embeds local portrait thumbnail JPEG as base64 string (`thumbnail_base64`).
  - Import parses JSON, restores database records, and writes thumbnails to disk for 100% offline display without internet.
- **New Device Restore Contract:**
  - Entering repo and PAT on a new device pulls `bookmarks.json` and downloads all thumbnails from the repo's `thumbnails/` folder into local cache automatically.
