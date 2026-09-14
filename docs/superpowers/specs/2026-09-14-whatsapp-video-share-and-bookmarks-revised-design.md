# WhatsApp Video Sharing, Hybrid Bookmarks & Tiered Security — Final Battle-Hardened Design (v2.2)

**Date:** 2026-09-14  
**Status:** Final Approved Specification (Post-Frontier Review + Two-Tier Security Gate)  
**Supersedes:** `2026-09-14-whatsapp-video-share-and-bookmarks-revised-design.md` (v2.1)  
**Target Milestone:** Instagram Digest v1.2  
**Key Decisions Locked:**  
- **Two-Tier Security Architecture:**  
  1. *Viewing PIN (Read-Only):* 4-digit PIN unlocks the PWA to view reels. If shared with family or guessed by someone, they can ONLY watch.  
  2. *Owner Secret Key (Read/Write Admin):* High-entropy secret key stored exclusively on YOUR personal device. Required for adding/deleting bookmarks, modifying R2 storage, or sending to Telegram.  
- **Storage & State:** Cloudflare D1 transaction truth + derived R2 manifest + Dual-Safety Cap (300 items / 3.5 GB).  
- **Offline Reliability:** IndexedDB outbox with ordered background replay.  
- **Telegram Archive:** URL-only `sendVideo` (Telegram servers pull directly from R2; zero Worker byte proxying).  
- **Video Sharing:** Web Share API Level 2 with 1-slot memory bound (prevents iOS Safari crashes).  

---

## 1. System Architecture Overview

```
[ User Browser / Device ]
       │
       ▼
 🔒 Passcode Gate (Viewing PIN) ──► Validates against local PIN hash
       │
  (Unlocked: Read-Only Feed Access)
       │
       ├── Weekly Feed (#feedContainer): Watch reels, share via WhatsApp (.mp4 file)
       │
       └── Bookmarks Action (🔖):
              │
              ├── Does device have OWNER_KEY in localStorage?
              │     ├── NO  ──► Hide bookmark button OR show "Read-only mode"
              │     └── YES ──► Send request with `Authorization: Bearer <OWNER_KEY>`
              ▼
    [ Cloudflare Worker (/api/bookmark) ]
              │
              ├── Validates `X-Owner-Key` against `env.OWNER_KEY` (403 if invalid)
              ├── D1 Database: Atomic transaction (insert/delete, enforce 300/3.5GB cap)
              ├── Cloudflare R2: Copy/delete bookmarks/{id}.mp4 & regenerate bookmarks/manifest.json
              └── Telegram Bot API: sendVideo by URL (Telegram fetches R2 directly)
```

---

## 2. Two-Tier Access Control: Viewing PIN vs. Owner Key

### 2.1 Tier 1: Viewing Passcode (Read-Only PIN)

- **Purpose:** Protects the feed from web crawlers, scrapers, and the general public. Can be shared with family members without giving them permission to alter your bookmarks.
- **PWA Lock Screen (`#lockScreen`):**
  - Minimalist dark 4-digit keypad overlay.
  - Entered once on a device and saved in `localStorage.getItem('digest_pin')`.
  - Also accepts one-time URL unlock: `https://vkr1729.github.io/Instagram_digest/?pin=1729`.
  - If a family member has this PIN (or someone guesses it), they can browse and watch the top 300 reels, but **cannot modify bookmarks**.

### 2.2 Tier 2: Owner Secret Key (Write / Bookmark Admin Key)

- **Purpose:** Cryptographically authorizes all bookmark operations (R2 writes, D1 updates, Telegram dispatches).
- **Storage:** Stored **strictly** on your personal phone/computer (`localStorage.getItem('digest_owner_key')`).
- **One-Time Setup:** You set up your personal phone using a private link once:
  `https://vkr1729.github.io/Instagram_digest/?pin=1729&owner_key=YOUR_SECRET_KEY_HERE`
  The PWA automatically extracts both keys, saves them to `localStorage`, and removes the query parameters from the browser address bar.
- **Server Enforcement:**
  The Cloudflare Worker rejects any mutating bookmark request without the exact Owner Key:
  ```javascript
  const ownerKey = request.headers.get('X-Owner-Key') || '';
  if (ownerKey !== env.OWNER_KEY) {
    return new Response(JSON.stringify({ error: 'Forbidden: Owner key required' }), { status: 403 });
  }
  ```
- **Result:** Even if someone guesses the 4-digit viewing PIN, they are physically blocked from altering your bookmarks, depleting your R2 storage, or posting to your private Telegram channel.

---

## 3. Feature 1: WhatsApp Video Attachment Sharing

- **Trigger:** User taps the WhatsApp button on an active reel.
- **API Invocation:** `navigator.share({ files: [mp4File], text: shareText })`.
- **WhatsApp Behavior:** WhatsApp receives the raw `.mp4` file and uses `shareText` as the video caption.
- **Persistence:** The video is preserved directly in the recipient's chat media, completely unaffected by the weekly R2 purge.
- **1-Slot Bounded Memory:** The app only holds the `File` object for the single currently active card in memory ($\sim 15\text{--}25\text{ MB}$ peak), eliminating Safari reload crashes.
- **Desktop Fallback:** Direct `<a download>` stream to disk with clipboard caption copy.

---

## 4. Feature 2: Hybrid Bookmarks Subsystem

### 4.1 Data Flow & Cloudflare D1 Transactions

1. **Tap 🔖:** Optimistically toggle UI icon and append to IndexedDB outbox.
2. **Cloudflare Worker:**
   - Verifies `X-Owner-Key`.
   - **D1 Atomic Transaction:** Inserts bookmark, checks `COUNT > 300 OR SUM(size_bytes) > 3.5 GB`, and purges oldest bookmarks from D1.
   - **R2 Operations:** Copies video to `bookmarks/{id}.mp4`, copies portrait thumbnail, purges evicted files, and regenerates `bookmarks/manifest.json`.
   - **Telegram Dispatch:** Posts `sendVideo` by URL to private Telegram channel (Telegram pulls video directly from R2).
3. **Multi-Device Sync:** Worker exposes `GET /api/bookmarks` (gated by `X-Owner-Key` or viewing PIN for read-only view).
4. **Un-bookmark:** `DELETE /api/bookmark/:id` removes record from D1 and deletes R2 objects; leaves message in Telegram permanently.

---

## 5. UI/UX: Instagram-Style Bookmarks Grid View

- `<section id="bookmarksContainer" class="bookmarks-view" style="display:none">`.
- Header navigation chip: `🔖 Bookmarks (<count>)`.
- 3-column CSS Grid with 9:16 portrait thumbnails, creator handle overlays, and category pills.
- Instant client-side search input matching creator handles or caption text.
- Full-screen `#bookmarkOverlay` player with swipe-up/down navigation and an active un-bookmark toggle.

---

## 6. Storage Economics & Safety Bounds

| Resource | Guardrail | Worst-Case Bound | Margin |
| :--- | :--- | :--- | :--- |
| **Active Bookmark Pool** | Max 300 reels OR 3.5 GB | Exactly $\le 3.5\text{ GB}$ (D1 Enforced) | Hard-capped by bytes |
| **Weekly Digest Storage** | 300 reels | $\sim 2.8\text{--}3.1\text{ GB}$ | Purged every 8 days |
| **Total Cloudflare R2 Storage** | Free tier: 10.0 GB | Peak: $\sim 6.6\text{ GB}$ | **$\sim 3.4\text{ GB}$ safe buffer** |
| **R2 Egress / Bandwidth** | Free unlimited egress | $0.00 | $0.00 permanently |
| **Cloudflare D1 Database** | 100,000 writes/day free | $\sim 10\text{--}50\text{ writes/week}$ | $<0.05\%$ of free tier |
| **Telegram Cold Archive** | Unlimited free storage | Unlimited | Permanent backup |
