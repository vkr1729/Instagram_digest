# WhatsApp Video Sharing & Hybrid Bookmarks Design Specification (v1.3 Hardened)

**Date:** 2026-09-14  
**Status:** Hardened Specification (Pre-Frontier Review)  
**Target Milestone:** Instagram Digest v1.2  

---

## 1. Executive Summary & Architecture Overview

This specification hardens the implementation design for two extensions to the Instagram Digest PWA:

1. **Feature 1 (WhatsApp Video Attachment):** Native Web Share API Level 2 (`navigator.share({ files: [file], text: ... })`) using pre-cached `.mp4` video files from Service Worker `CacheStorage`, with a strict **1-item bounded memory window** to eliminate iOS WebKit memory pressure crashes.
2. **Feature 2 (Hybrid R2 + Telegram Bookmarks with 3-Column Grid):**
   - **Hot Storage:** Cloudflare R2 (`bookmarks/` prefix) governed by a **Dual-Safety Cap** (max 300 reels OR 3.5 GB total, whichever is reached first).
   - **Chronological Index & Multi-Device Sync:** Central `bookmarks/manifest.json` on R2 providing true FIFO ordering, size accounting, and a multi-device `GET /api/bookmarks` endpoint.
   - **Cold Archive (Telegram):** Automated forwarding to a private Telegram channel via Cloudflare Worker. Handles the Telegram 20 MB URL cap via hybrid direct-URL (<= 20 MB) and `multipart/form-data` streaming (20 MB – 50 MB) so every reel is preserved as a single, complete playable video.
   - **UI Experience:** Isolated Instagram-style 3-column thumbnail grid with instant client filtering/search and a full-screen swipable player overlay.

---

## 2. Feature 1: WhatsApp Video Attachment Architecture

### 2.1 Web Platform Constraints & Web Share Level 2

WhatsApp does not permit file attachments via URL schemes (`whatsapp://send`). The browser must trigger the native OS share sheet using the Web Share API Level 2.

```
User Clicks Share Button
       │
       ▼
Is Active Card's MP4 File in Bounded Memory (1-Slot)?
 ├── Yes ────────────────────────────────────────────────────────┐
 └── No  ──► Extract from ServiceWorker CacheStorage (~15ms)      │
                                                                 │
                                                                 ▼
                                                  navigator.share({ files: [mp4File], text: shareText })
                                                                 │
                                         ┌───────────────────────┴────────────────────────┐
                                         ▼                                                ▼
                               Mobile OS Share Sheet                             Desktop Browser
                            (User selects WhatsApp:                              (Fallback: 1-Click
                            MP4 is attached directly)                            Instant MP4 Download)
```

### 2.2 Heap Safety & Anti-Timeout on iOS WebKit

- **Memory Limit Rule:** An in-memory cache of video `File` objects must NEVER exceed **1 item**. When card $N$ becomes active, the `File` for card $N-1$ is immediately dereferenced and garbage collected. This caps peak JS heap overhead to ~15–25 MB, preventing WebKit memory crashes.
- **Microtask Activation Preservation:** Reading from local CacheStorage takes ~10–25ms on disk, which remains safely within WebKit’s transient user gesture window. If extraction completes under 50ms, `navigator.share()` triggers synchronously without user-gesture revocation.
- **Share Caption:** The `text` field contains:
  ```text
  Watch @{creator_handle} on Instagram Digest: {shareUrl}

  {caption_snippet}
  ```
  WhatsApp natively treats this text as the attached video's caption.
- **Desktop Fallback:** On browsers where `navigator.canShare({ files })` is unsupported, clicking Share triggers an immediate `<a download>` stream to disk and copies the caption to clipboard.

---

## 3. Feature 2: Hybrid Bookmarks System

### 3.1 Data Flow Architecture

```
[ PWA on Phone / Desktop ]
  │
  ├── 1. Tap Bookmark Icon (🔖)
  │      ├─ Optimistic UI toggle (immediate visual feedback)
  │      ├─ Cache in local localStorage for instant offline access
  │      └─ POST https://ig-digest-api.<subdomain>.workers.dev/api/bookmark
  │            Payload: { reel_id, r2_url, creator_handle, caption, thumbnail_url }
  │
  ▼
[ Cloudflare Worker (/api/bookmark) ]
  │
  ├── 2. R2 Video & Thumbnail Persist:
  │      ├─ Copy video to bookmarks/{reel_id}.mp4
  │      └─ Copy thumbnail to bookmarks/{reel_id}_portrait.jpg
  │
  ├── 3. Manifest Update & Dual-Safety Cap:
  │      ├─ Fetch bookmarks/manifest.json from R2
  │      ├─ Append new record: { id, creator, caption, size_bytes, bookmarked_at, telegram_msg_id }
  │      └─ Enforce Cap: While count > 300 OR sum(size_bytes) > 3.5 GB:
  │            - Pop oldest record from head of array
  │            - Delete its bookmarks/{id}.mp4 and thumbnail from R2
  │      ├─ Put updated bookmarks/manifest.json back to R2
  │
  └── 4. Telegram Cold Backup (Hybrid Handler):
         ├─ Video Size <= 20 MB:
         │    POST https://api.telegram.org/bot<TOKEN>/sendVideo
         │    body: { chat_id, video: r2_public_url, caption, supports_streaming: true }
         └─ Video Size 20 MB – 50 MB:
              Stream R2 object body directly via multipart/form-data to Telegram Bot API
              (Preserves single-file playback bubble in chat; zero chunking)
```

### 3.2 Manifest Schema (`bookmarks/manifest.json`)

```json
[
  {
    "id": "DdERU2xAcsG",
    "creator_handle": "mom_in_dubai",
    "caption": "10-minute high protein dinner recipe...",
    "category": "food",
    "thumbnail_url": "https://pub-...r2.dev/bookmarks/DdERU2xAcsG_portrait.jpg",
    "video_url": "https://pub-...r2.dev/bookmarks/DdERU2xAcsG.mp4",
    "size_bytes": 14954202,
    "bookmarked_at": "2026-09-14T10:15:00.000Z",
    "telegram_message_id": 4821
  }
]
```

### 3.3 Multi-Device Synchronization

- The Cloudflare Worker exposes `GET /api/bookmarks`.
- When the PWA launches or the user selects the "🔖 Bookmarks" tab:
  1. It reads from local `localStorage` immediately (sub-millisecond render).
  2. In the background, it fetches `GET /api/bookmarks` to reconcile any bookmarks made on another device.
  3. Updates the 3-column grid and refreshes local cache.

### 3.4 Un-bookmark Lifecycle

When a user taps the active 🔖 icon on a bookmarked reel:
1. PWA sends `DELETE /api/bookmark/:id`.
2. Worker removes the record from `bookmarks/manifest.json`.
3. Worker deletes `bookmarks/{id}.mp4` and `bookmarks/{id}_portrait.jpg` from Cloudflare R2, immediately reclaiming quota.
4. **Telegram Policy:** The message in Telegram is **retained permanently** as an immutable cold backup (user preference).

---

## 4. UI/UX: Instagram-Style Bookmarks Grid View

### 4.1 Component Isolation & Layout

```
┌────────────────────────────────────────────────────────┐
│ Header: [ All 🔥 ] [ Food 🥗 ] ... [ 🔖 Bookmarks (42) ]│
├────────────────────────────────────────────────────────┤
│ Search: [ 🔍 Filter by creator, recipe, topic...      ]│
├──────────────┬──────────────┬──────────────────────────┤
│  Thumbnail   │  Thumbnail   │  Thumbnail               │
│  @creator    │  @creator    │  @creator                │
├──────────────┼──────────────┼──────────────────────────┤
│  Thumbnail   │  Thumbnail   │  Thumbnail               │
│  @creator    │  @creator    │  @creator                │
└──────────────┴──────────────┴──────────────────────────┘
```

- **DOM Isolation:** Lives in `<section id="bookmarksContainer" class="bookmarks-view" style="display:none">`.
- **Feed Protection:** Clicking "Bookmarks" toggles `#feedContainer.style.display = 'none'` and pauses any playing feed video. The snap-scroll physics, touch gestures, and 2x speed listeners of the main feed are completely bypassed while browsing bookmarks.
- **Search & Filter:** Client-side instant filter input that matches against `creator_handle` or `caption` text in real time.

### 4.2 Full-Screen Reel Overlay Experience

Tapping any thumbnail in the grid opens `#bookmarkOverlay`:
- Re-uses the clean vertical 9:16 player layout.
- Enables vertical swiping to move between bookmarked reels.
- Top navigation includes an active `🔖` toggle (to un-bookmark) and an `✕` button to close back to the exact grid scroll position.

---

## 5. Storage Economics & Safety Guardrails

| Metric | Target Limit | Worst-Case Bound | Safety Margin |
| :--- | :--- | :--- | :--- |
| **Active Bookmark Pool** | 300 reels | Max 3.5 GB (Dual-Safety Cap) | Capped strictly by manifest bytes |
| **Weekly Digest Storage** | 300 reels | ~2.8 GB | Purged every 8 days |
| **Total Cloudflare R2 Usage** | ~6.3 GB | Max 7.0 GB | Well under 10.0 GB free tier limit |
| **R2 Egress Cost** | $0.00 | $0.00 | Free unlimited egress on Cloudflare R2 |
| **Telegram Cold Storage** | Unlimited | Unlimited | 100% Free permanent cloud storage |

---

## 6. Implementation Boundaries & File Touchpoints

| File Path | Nature of Change |
| :--- | :--- |
| `templates/partials/player.js` | Update `shareReelWhatsApp` to use 1-item bounded memory MP4 `File` via `navigator.share({ files })`; add bookmark toggle and grid modal navigation. |
| `templates/partials/feed.html` | Add Bookmark icon button (`.bookmark-btn`) inside `.reel-actions`. |
| `templates/partials/header.html` | Add "🔖 Bookmarks" navigation chip with dynamic badge count. |
| `templates/partials/bookmarks.html` | **[NEW]** 3-column grid layout, search bar, and full-screen swipable modal overlay. |
| `templates/partials/styles.css` | Styling for grid cards, 3-column layout, search input, and modal overlay. |
| `storage_r2.py` | Verify that `purge_expired_r2_objects` and `purge_unreferenced_r2_videos` only match `videos/{date}/` and strictly ignore `bookmarks/`. |
| `cloudflare/worker.js` | **[NEW]** Cloudflare Worker handling `/api/bookmark` (R2 copy, manifest dual-cap pruning, Telegram hybrid send, sync GET). |
