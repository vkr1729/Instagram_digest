# Instagram Digest v1.0 — Automated User Acceptance Testing (UAT) Plan

## Overview
This document defines the strict, end-to-end automated acceptance criteria for the Instagram Digest project. It verifies that the implementation fulfills all behavioral, algorithmic, storage, UI/UX, and deployment specifications.

---

## Acceptance Criteria & Test Suites

### Suite 1: Ingestion & Following Sync (`extractor.py`)
- [ ] **UAT-1.1 Session Extraction**: `extractor.py` detects and extracts active Instagram session cookies from Google Chrome (`--cookies-from-browser chrome`) without raising authentication errors.
- [ ] **UAT-1.2 Following Fetching**: Querying the following list successfully retrieves `@handle`, display name, profile picture, and account privacy status.
- [ ] **UAT-1.3 Private Account Filtering**: All private personal accounts are automatically filtered out; only public accounts are imported.
- [ ] **UAT-1.4 30-Day Freshness Cache**: Followed accounts are cached with a timestamp in `data/following_cache.json`. Repeated runs within 30 days use cache unless `--sync-following` is explicitly invoked.
- [ ] **UAT-1.5 Non-Destructive Merge**: Running following sync never overwrites custom categories or `"enabled": false` flags in `sources.json`.

### Suite 2: Public Reel Extraction (`extractor.py`)
- [ ] **UAT-2.1 Anonymous Fallback**: If browser cookies fail or are absent, public creator reels are fetched anonymously without halting the pipeline.
- [ ] **UAT-2.2 Metadata Integrity**: Every extracted reel record contains: `id`, `url`, `video_url`, `view_count`, `like_count`, `comment_count`, `caption`, `duration`, `timestamp`, and `creator_handle`.
- [ ] **UAT-2.3 7-Day Window Filter**: Only reels posted within the last 7 days (168 hours) are admitted into the weekly candidate pool.

### Suite 3: Fair-Share Viral Multiplier Ranking (`ranker.py`)
- [ ] **UAT-3.1 Creator Normalization**: Score is calculated relative to each creator's baseline (median views/likes), allowing small niche creators with high viral engagement to compete with mega-accounts.
- [ ] **UAT-3.2 Guaranteed Representation**: Every active creator who posted at least 1 valid reel in the 7-day window receives at least 1 slot in the Top 100.
- [ ] **UAT-3.3 Creator Saturation Cap**: No creator can occupy more than 4 slots in the Top 100 digest, preventing feed takeover.
- [ ] **UAT-3.4 Deterministic Output**: The ranked output produces exactly up to 100 items (or all candidates if <100), numbered #01 through #100 in descending score order, output to `data/top100_digest.json`.

### Suite 4: Cloudflare R2 Storage & Quota Guard (`storage_r2.py`)
- [ ] **UAT-4.1 S3-Compatible Client**: Authenticates against Cloudflare R2 using `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` from `.env`.
- [ ] **UAT-4.2 Pre-Flight Safety Quota (<5 GB)**: Calculates total existing storage in the bucket. If `current_storage + new_batch_size >= 5 GB`, the upload halts with an actionable error.
- [ ] **UAT-4.3 14-Day Rolling Purge (R2 Bucket)**: Scans objects in `videos/` on R2; automatically deletes all video files older than 14 days.
- [ ] **UAT-4.4 14-Day Rolling Purge (Local Disk)**: Purges local video downloads older than 14 days from `~/.instagram_digest/videos/`.
- [ ] **UAT-4.5 Upload Verification**: Uploaded videos are publicly accessible and streamable from `R2_PUBLIC_DOMAIN`.
- [ ] **UAT-4.6 Local Fallback Mode**: If R2 credentials are unset or invalid, the pipeline falls back gracefully to local disk playback without crashing.

### Suite 5: Variant 1A Static Site Compilation (`site_builder.py`)
- [ ] **UAT-5.1 HTML5 Semantic Output**: `site_builder.py` generates `site/index.html` (R2 stream links) and `site/local_index.html` (local disk relative paths).
- [ ] **UAT-5.2 Zero In-Video Box Clutter**: Video canvas is completely free of floating rectangular buttons or boxes.
- [ ] **UAT-5.3 Natural Bottom Scrim**: Creator handle (`@handle • #01`) and caption are rendered inside an organic bottom-left gradient scrim.
- [ ] **UAT-5.4 5 Story Category Circles**: Renders 5 gradient-ringed circles: `🔥 All Top 100`, `💻 Tech & AI`, `🏋️ Health & Wellness`, `🧠 Deep Explainer`, `🎨 Creative & Culture`.
- [ ] **UAT-5.5 2-Reel DOM Preloading**: HTML tags for the upcoming 2 videos include `preload="auto"` for MRT tunnel resilience.
- [ ] **UAT-5.6 Orphan Branch Deploy**: `site_builder.py` deploys `site/` to the orphan `gh-pages` branch using git commands.

### Suite 6: Automated Playwright Browser E2E Tests (`tests/e2e/`)
- [ ] **UAT-6.1 Mobile Viewport & CSS Scroll-Snap**: In 390×844 (iPhone 14) viewport, verifying `scroll-snap-type: y mandatory` provides 1-per-screen vertical snap.
- [ ] **UAT-6.2 Story Category Filter**: Clicking `💻 Tech & AI` story circle immediately filters visible cards to only Tech creators; clicking `🔥 All Top 100` restores all 100.
- [ ] **UAT-6.3 Playback Speed Cycle**: Verifies initial `video.playbackRate === 1.5`. Clicking the `⚡ 1.5x` pill cycles through `1.75x`, `2x`, `1x`, `1.25x`, and back to `1.5x`.
- [ ] **UAT-6.4 0.5s Hands-Free Auto-Advance**: Triggering video completion event (`ended`) waits 500ms and smoothly scrolls/advances to the next reel.
- [ ] **UAT-6.5 Watched State Persistence**: Watching a reel records its ID in `localStorage['ig_watched_ids']`. Watched reels are hidden from the active sequence.
- [ ] **UAT-6.6 Celebration Screen**: Once all reels in a category/batch are watched, the player displays "You're all caught up for the week! 🎉" with a functioning "Reset / Rewatch" button.

### Suite 7: Local Server & Desktop Launcher
- [ ] **UAT-7.1 Local HTTP Server**: `python main.py --serve` starts a local server on port 8080 and serves `local_index.html` streaming MP4s from local disk.
- [ ] **UAT-7.2 On-Demand Sync Endpoint**: Triggering POST `/api/sync-following` from the dashboard runs the sync and updates `sources.json`.
- [ ] **UAT-7.3 Desktop Launcher File**: `InstagramDigest.desktop` is correctly created with valid `Exec` and `Icon` paths and installed to `~/Desktop` and `~/.local/share/applications`.

---

## Automated Execution Command
To execute the full automated acceptance suite after implementation:
```bash
pytest tests/ -v && npx playwright test tests/e2e/
```
