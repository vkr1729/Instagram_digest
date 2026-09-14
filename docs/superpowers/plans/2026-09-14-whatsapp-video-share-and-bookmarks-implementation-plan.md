# Implementation Plan: WhatsApp Video Sharing, Hybrid Bookmarks & Two-Tier Security

**Date:** 2026-09-14  
**Target Milestone:** Instagram Digest v1.2  
**Design Reference:** `docs/superpowers/specs/2026-09-14-whatsapp-video-share-and-bookmarks-revised-design.md` (v2.2)  

---

## 1. Architectural Summary & Scope

Implement three cohesive extensions to the Instagram Digest PWA:

1. **Two-Tier Access Control:**
   - *Tier 1 (Viewing PIN):* 4-digit keypad gate (`#lockScreen`) protecting feed viewing from scrapers/public.
   - *Tier 2 (Owner Secret Key):* High-entropy admin secret stored exclusively on the owner's personal device, required for adding/deleting bookmarks and invoking backend sync.
2. **Feature 1 (WhatsApp Video Attachment Sharing):**
   - Replace link-only sharing with direct `.mp4` file attachments via Web Share API Level 2.
   - Enforce a strict 1-slot memory buffer to guarantee zero iOS Safari WebKit memory crashes.
3. **Feature 2 (Hybrid Bookmarks Subsystem):**
   - *Client UI:* Instagram-style 3-column thumbnail grid with instant search and full-screen swipable player overlay.
   - *Client Resilience:* IndexedDB outbox with ordered replay for offline/subway bookmarking.
   - *Serverless Backend:* Cloudflare Worker backed by Cloudflare D1 (SQLite) enforcing an atomic **300 reels / 3.5 GB Dual-Safety Cap** in R2, with permanent cold archiving to a private Telegram channel via URL-only `sendVideo`.

---

## 2. File Touchpoints & Proposed Code Changes

### Component 1: Privacy & Two-Tier Access Gate

#### [NEW] `templates/partials/auth.html`
- Render the `#lockScreen` overlay ahead of `#feedContainer`.
- Keypad with 10 digit buttons, Backspace (`⌫`), and Clear (`C`).
- JavaScript helper functions:
  - `checkAuthOnLoad()`: Inspects URL query params `?pin=...&owner_key=...`. If found, saves to `localStorage` and cleans the URL via `window.history.replaceState`.
  - Compares entered PIN with stored hash / value. If valid, sets `#lockScreen.style.display = 'none'` and reveals `#feedContainer`.
  - Exposes `isOwnerDevice()` helper returning `Boolean(localStorage.getItem('digest_owner_key'))`.

#### [MODIFY] `templates/partials/styles.css`
- Styles for `#lockScreen`: Full-screen dark backdrop (`background: #000`), centered keypad grid (`display: grid; grid-template-columns: repeat(3, 1fr)`), tactile touch-active animations, and secure PIN dot indicators (`● ● ● ○`).

---

### Component 2: Feature 1 — WhatsApp MP4 Video Attachment

#### [MODIFY] `templates/partials/player.js`
- Refactor `shareReelWhatsApp(reelId, creatorHandle, e)`:
  - **Memory Guard:** Maintain `let activeShareFile = null; let activeShareReelId = null;`.
  - When card changes: `activeShareFile = null;` to free previous video buffer immediately.
  - On Share tap:
    - Retrieve cached response from `caches.open('ig-digest-media-v1')`.
    - If found: convert blob to `new File([blob], `${creatorHandle || 'reel'}_${reelId}.mp4`, { type: 'video/mp4' })`.
    - Check `navigator.canShare && navigator.canShare({ files: [file] })`.
    - Invoke `navigator.share({ files: [file], text: shareCaption, title: ... })`.
    - Fallback: On desktop or unsupported browsers, trigger `<a download>` stream to disk and copy the caption to clipboard.

---

### Component 3: Feature 2 — Hybrid Bookmarks Client & UI

#### [MODIFY] `templates/partials/feed.html`
- Inside `.reel-actions`, add the Bookmark toggle button:
  ```html
  <button class="bookmark-btn" data-id="{{ item.id | e }}" onclick="toggleBookmark(this.dataset.id, event)" title="Bookmark Reel (Owner Only)">
    <svg class="bookmark-icon" viewBox="0 0 24 24" width="14" height="14"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
  </button>
  ```

#### [MODIFY] `templates/partials/header.html`
- Add navigation chip in the category scroll container:
  ```html
  <button class="filter-chip bookmarks-chip" data-category="bookmarks" onclick="toggleBookmarksView(true)">
    🔖 Bookmarks <span class="bookmark-badge" id="bookmarkBadge">0</span>
  </button>
  ```

#### [NEW] `templates/partials/bookmarks.html`
- Container `<section id="bookmarksContainer" class="bookmarks-view" style="display:none">`:
  - Search Header: `<input type="search" id="bookmarkSearchInput" placeholder="🔍 Search saved recipes, workouts, topics..." oninput="filterBookmarksGrid(this.value)">`
  - 3-Column Grid: `<div class="bookmarks-grid" id="bookmarksGrid"></div>`
  - Full-Screen Overlay: `<div class="bookmark-overlay" id="bookmarkOverlay" style="display:none">...</div>` with video player, swipe up/down event handlers, un-bookmark toggle, and `✕` close button.

#### [MODIFY] `templates/partials/styles.css`
- Add CSS for:
  - `.bookmark-btn`: Gold stroke when unbookmarked; solid gold fill when active.
  - `.bookmarks-grid`: Responsive 3-column CSS grid (`grid-template-columns: repeat(3, 1fr); gap: 2px; padding: 2px;`).
  - `.bookmark-card`: 9:16 aspect ratio thumbnail cards with dark gradient overlay, handle pill, and duration badge.
  - `.bookmark-overlay`: Fixed full-screen z-index overlay replicating the native vertical reel feel.

---

### Component 4: Offline Outbox & Multi-Device Sync

#### [MODIFY] `templates/partials/player.js`
- IndexedDB Database: `ig-digest-store`, store: `pending-bookmark-ops`.
- Functions:
  - `queueBookmarkOp(opType, reelData)`: Appends operation to IndexedDB outbox.
  - `flushBookmarkOutbox()`: Iterates pending operations chronologically, dispatching to Cloudflare Worker with `X-Owner-Key`. Replays with exponential backoff on network failures.
  - `syncBookmarksFromServer()`: Calls `GET /api/bookmarks`, updates local `localStorage` cache, reconciles active bookmark states across UI buttons, and updates `#bookmarkBadge`.
  - Event Listeners: Trigger `flushBookmarkOutbox()` on `window.addEventListener('online', ...)`.

---

### Component 5: Serverless Backend & Database

#### [NEW] `cloudflare/schema.sql`
- D1 Database schema:
  ```sql
  CREATE TABLE IF NOT EXISTS bookmarks (
    id TEXT PRIMARY KEY,
    creator_handle TEXT NOT NULL,
    caption TEXT DEFAULT '',
    category TEXT DEFAULT '',
    thumbnail_url TEXT NOT NULL,
    video_url TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    bookmarked_at TEXT NOT NULL,
    telegram_message_id INTEGER
  );
  CREATE INDEX IF NOT EXISTS idx_bookmarks_at ON bookmarks (bookmarked_at);
  ```

#### [NEW] `cloudflare/wrangler.toml`
- Cloudflare configuration:
  - D1 database binding (`DB`).
  - R2 bucket binding (`MY_BUCKET`).
  - Secret variables: `OWNER_KEY`, `VIEWING_PIN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `R2_PUBLIC_BASE_URL`.

#### [NEW] `cloudflare/worker.js`
- Implements:
  - **Auth Middleware:** Checks `X-Owner-Key === env.OWNER_KEY` for mutations. Rejects with `403` if missing or invalid.
  - **CORS:** Pinned strictly to `Access-Control-Allow-Origin: https://vkr1729.github.io`.
  - **`POST /api/bookmark`:**
    1. Parse incoming reel metadata.
    2. Check object in R2 `videos/...` and copy to `bookmarks/{id}.mp4` and `bookmarks/{id}_portrait.jpg`.
    3. Execute D1 transaction: `INSERT OR IGNORE INTO bookmarks ...`.
    4. Enforce Dual-Safety Cap in D1: Query `SELECT id, size_bytes FROM bookmarks ORDER BY bookmarked_at ASC`. While `COUNT > 300 OR SUM(size_bytes) > 3.5 GB`, delete oldest records from D1 and collect their R2 keys for deletion.
    5. Delete evicted keys from R2.
    6. Regenerate and write `bookmarks/manifest.json` to R2 for read-cache debugging.
    7. Dispatch Telegram `sendVideo` by URL:
       `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendVideo?chat_id=${env.TELEGRAM_CHAT_ID}&video=${r2Url}&caption=${caption}&supports_streaming=true`.
  - **`DELETE /api/bookmark/:id`:**
    1. Removes record from D1.
    2. Deletes `bookmarks/{id}.mp4` and thumbnail from R2.
    3. Retains message in Telegram permanently (cold backup).
  - **`GET /api/bookmarks`:**
    - Returns array of bookmarked reels from D1 ordered by `bookmarked_at DESC`.

---

### Component 6: Weekly Pipeline Safety Audit

#### [MODIFY] `storage_r2.py`
- Audit `purge_expired_r2_objects(max_age_days)` and `purge_unreferenced_r2_videos()`:
  - Add explicit guard assertions ensuring every S3/R2 list prefix strictly contains `Prefix="videos/"`.
  - Add negative unit test confirming that an object under `bookmarks/` is NEVER returned or deleted by the weekly purger.

---

## 3. Step-by-Step Implementation Sequence

```
Step 1: Pipeline Retention Defense (storage_r2.py audit & tests)
        │
Step 2: Serverless Backend & Database (cloudflare/schema.sql, worker.js, wrangler.toml)
        │
Step 3: Client Security & PIN Gate (templates/partials/auth.html, styles.css)
        │
Step 4: WhatsApp MP4 Video Attachment (templates/partials/player.js 1-slot memory)
        │
Step 5: Bookmarks UI & Grid View (templates/partials/feed.html, header.html, bookmarks.html)
        │
Step 6: Offline Outbox & Multi-Device Sync (templates/partials/player.js IndexedDB outbox)
        │
Step 7: Automated Test Suite & Multi-Device Manual Verification
```

---

## 4. Verification & Testing Protocol

### 4.1 Automated Test Suite
- Run existing regression tests:
  ```bash
  pytest tests/test_architectural_fixes.py
  pytest tests/test_sw_range.py
  ```
- Add `tests/test_bookmarks_isolation.py`:
  - Test that `purge_expired_r2_objects()` and `purge_unreferenced_r2_videos()` never target `bookmarks/`.
  - Test that `checkAuthOnLoad()` accurately extracts and strips `pin` and `owner_key` from URL strings.

### 4.2 Manual Verification Steps
1. **PIN Gate:** Open site in fresh browser. Confirm `#feedContainer` is hidden and PIN pad is shown. Enter PIN, verify unlock, and reload to confirm state persists.
2. **WhatsApp Video Attachment:** Tap Share on mobile. Verify native share sheet presents the `.mp4` video with caption prefilled.
3. **Bookmarks Grid:** Tap 🔖 on 3 reels. Switch to Bookmarks view. Verify 3-column thumbnail grid renders with instant search filter. Tap thumbnail to verify full-screen swipable modal player.
4. **Backend Sync:** Confirm file arrives in R2 under `bookmarks/`, row appears in D1, and video post appears in private Telegram channel.
