# Frontier Model Implementation Handover Prompt

Copy and paste the prompt below into your frontier model (e.g., Claude 3.7 Sonnet, OpenAI o3, or Gemini 1.5 Pro with Maximum Thinking / Effort enabled).

```markdown
# MISSION: SINGLE-SHOT PRODUCTION IMPLEMENTATION & RIGOROUS TESTING

You are a Principal Full-Stack Systems & Web Platform Engineer. You are tasked with executing the **complete end-to-end implementation** of the WhatsApp Video Sharing, Hybrid Bookmarks Subsystem, and Two-Tier Security Gate for the "Instagram Digest" application.

Spend **maximum effort** to implement this cleanly in a single shot with zero back-and-forth. Write complete, robust code (no placeholders, no `TODO`s, no half-finished stubs), adhere strictly to the design contracts, and run the automated test suite to verify that everything passes cleanly before calling it done.

---

## 1. SPECIFICATION & PLAN REFERENCES

The design and implementation plan have already completed multiple rounds of adversarial review and second-order hardening. You must implement exactly what is specified in these documents:

- **Authoritative Implementation Plan:**  
  `docs/superpowers/plans/2026-09-14-whatsapp-video-share-and-bookmarks-implementation-plan-revised.md`
- **Underlying Design Specification:**  
  `docs/superpowers/specs/2026-09-14-whatsapp-video-share-and-bookmarks-revised-design.md` (v2.2)

---

## 2. THE 7 NON-NEGOTIABLE CORE FIXES (DO NOT MISS ANY)

1. **8 GB Quota Fix in `config.py`:** Update `R2_STORAGE_QUOTA_BYTES` from 5 GB to 8 GB (`8 * 1024 * 1024 * 1024`). Update `tests/test_storage.py` quota thresholds to match 8 GB so tests pass.
2. **Unified Auth Header:** Use `Authorization: Bearer <OWNER_KEY>` across all endpoints, while accepting `X-Owner-Key` as fallback.
3. **Server-Authoritative Video Size:** In `cloudflare/worker.js`, use `src.size` from `env.MY_BUCKET.get()`. Never trust client-supplied `size_bytes`.
4. **iOS Seekability Header Preservation:** In `cloudflare/worker.js`, copy videos using `httpMetadata: { contentType: 'video/mp4', cacheControl: 'public, max-age=31536000, immutable' }`.
5. **Telegram JSON Payload:** Send Telegram notifications via JSON POST with caption cleanly truncated to 1,000 characters and `parse_mode: null`.
6. **Thumbnail HTTP Fetch:** In `cloudflare/worker.js`, fetch thumbnails via HTTPS (with a 10s AbortController timeout) and save to `bookmarks/{id}_portrait.jpg`. Fall back gracefully if missing.
7. **IndexedDB 5-Retry Cap & Poison-Pill Dropping:** In `templates/partials/player.js`, drop permanent failures (403, 404 SOURCE_PURGED) immediately; cap network retries at 5 attempts before parking.

---

## 3. IMPLEMENTATION SEQUENCE (EXECUTE IN ORDER)

Follow the 9 steps detailed in Section 9 of the plan:

- **Step 1: Pipeline Safety & Quota:**
  - Update `config.py` quota to 8 GB.
  - Audit `storage_r2.py` to ensure `purge_expired_r2_objects` and `purge_unreferenced_r2_videos` assert on `Prefix="videos/"` and explicitly skip `bookmarks/`.
  - Add `tests/test_bookmarks_isolation.py` and update `tests/test_storage.py`.
- **Step 2: Database Schema:**
  - Create `cloudflare/schema.sql` with the `bookmarks` table and index.
- **Step 3: Serverless Worker & Config:**
  - Create `cloudflare/worker.js` and `cloudflare/wrangler.toml` following the exact frozen API contract in Section 2 & 4 of the plan.
- **Step 4: Two-Tier PIN Gate:**
  - Create `templates/partials/auth.html` with `#lockScreen`.
  - Update `site_builder.py` / `viewer.html` to inject the SHA-256 hash of `VIEWING_PIN`.
  - Add lock screen styling to `templates/partials/styles.css`.
- **Step 5: WhatsApp MP4 Sharing:**
  - In `templates/partials/player.js`, implement active card pre-resolution (`resolveShareFile`), 1-slot memory bounding (`activeShareFile`), and Web Share API Level 2 file sharing with desktop download fallback.
- **Step 6: Bookmarks UI & Grid View:**
  - Update `templates/partials/feed.html` with the 🔖 button.
  - Update `templates/partials/header.html` with the Bookmarks navigation chip.
  - Create `templates/partials/bookmarks.html` with the 3-column grid, live search input, and full-screen `#bookmarkOverlay` player.
  - Implement `toggleBookmarksView(open)` in `templates/partials/player.js` with scroll lock and scroll restoration.
  - Add all styling in `templates/partials/styles.css`.
- **Step 7: Offline Outbox & Multi-Device Sync:**
  - In `templates/partials/player.js`, implement the IndexedDB outbox (`ig-digest-store` / `pending-bookmark-ops`), background replay on `online` event, and `GET /api/bookmarks` reconciliation.
- **Step 8: Automated Verification:**
  - Run `pytest tests/test_storage.py tests/test_bookmarks_isolation.py tests/test_architectural_fixes.py tests/test_sw_range.py`.
  - Ensure 100% of tests pass.

---

## 4. VERIFICATION & HANDOVER REQUIREMENTS

Before finishing:
1. Run all pytest test suites and ensure all unit tests pass with zero failures.
2. Provide a concise summary of every file modified and created.
3. Provide the exact Wrangler commands needed for one-time Cloudflare deployment.
4. Leave the codebase in a clean, tested, ready-to-run state.
```
