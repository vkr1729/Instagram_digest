# Implementation Plan (Hardened v2): WhatsApp Share, Hybrid Bookmarks & Two-Tier Security

**Date:** 2026-09-14
**Status:** Final, hardened — single-shot implementation ready
**Supersedes:** `docs/superpowers/plans/2026-09-14-whatsapp-video-share-and-bookmarks-implementation-plan.md`
**Design reference:** `docs/superpowers/specs/2026-09-14-whatsapp-video-share-and-bookmarks-revised-design.md` (v2.2)
**Repo facts verified against:** `templates/viewer.html`, `templates/partials/{player.js,feed.html,header.html}`,
`templates/sw.js` + `site/sw.js` (identical), `site_builder.py`, `storage_r2.py`, `config.py`, `tests/test_storage.py`

---

## 0. Review verdict: 7 ship-blockers found and fixed in this revision

| # | Blocker | Where | Fix in this plan |
|---|---------|-------|------------------|
| 1 | Preflight quota (5 GB) contradicts design peak (~6.6 GB): weekly pipeline aborts once bookmarks grow | `config.py:89` vs spec §6 | Raise quota to 8 GB; preflight reserves bookmark cap (§8) |
| 2 | Auth header mismatch: spec diagram says `Authorization: Bearer`, worker section says `X-Owner-Key` | plan §Component 5 vs spec §2 | Single contract: `Authorization: Bearer <OWNER_KEY>`, accept `X-Owner-Key` fallback (§2) |
| 3 | `size_bytes` trusted from client → cap bypass / corrupt accounting | plan §Component 5 step 1 | Worker uses authoritative `src.size` from R2 `get()` (§4.2) |
| 4 | R2 "copy" underspecified; `Content-Type` loss breaks iOS seek | plan §Component 5 step 2 | `get`→`put` with `httpMetadata: {contentType:'video/mp4'}` (§4.2) |
| 5 | Telegram via GET query string: breaks on long captions, encoding, 1024-char cap ignored | plan §Component 5 step 7 | POST JSON, truncate caption to 1000 chars, no `parse_mode` (§4.4) |
| 6 | Thumbnail has no R2 source (R2 holds mp4 only; thumbs live on gh-pages) | plan §Component 5 step 2 | Worker fetches `thumbnail_url` over HTTP, tolerates failure (§4.3) |
| 7 | Outbox retries forever; 403/404 retried as if transient | plan §Component 4 | 5-retry cap + poison-pill drop rules (§5.2) |

Non-blocking hardenings also included: D1 TOCTOU re-check, scroll-lock protocol,
gesture-safe share pre-resolution, build-time PIN hash, overlay lazy video.

---

## 1. Scope

**In:** `cloudflare/{worker.js,wrangler.toml,schema.sql}` [NEW]; `templates/partials/{auth.html}` [NEW];
`player.js`, `feed.html`, `header.html`, `styles.css`, `viewer.html` [MODIFY];
`storage_r2.py`, `config.py`, `tests/test_storage.py` [MODIFY]; `tests/test_bookmarks_isolation.py` [NEW].
**Out:** native apps, login/JWT system, >50 MB Telegram path, changes to ranking/extraction.

**Done means:** (a) all §7 tests green; (b) manual §7.2 checklist passes on iOS Safari + desktop;
(c) `wrangler deploy` succeeds (Appendix A); (d) weekly cron still purges only `videos/`.

---

## 2. Frozen API & auth contract (worker ↔ PWA — implement exactly this)

- Base: `https://ig-digest-api.<subdomain>.workers.dev`
- Auth: `Authorization: Bearer <OWNER_KEY>` on **all three** routes (reads included — keeps grid
  URL unscrapable; family read-only devices use local snapshot only). Worker **also** accepts
  `X-Owner-Key` as fallback for older cached clients. Missing/invalid → `403 {error:'Forbidden'}`.
- CORS: `Access-Control-Allow-Origin: https://vkr1729.github.io` (never `*`);
  `Allow-Methods: GET, POST, DELETE, OPTIONS`; `Allow-Headers: Content-Type, Authorization, X-Owner-Key`;
  `Vary: Origin`; `OPTIONS` → `204`.
- `POST /api/bookmark` body: `{id, creator_handle, caption, category, video_url, thumbnail_url}`
  (`size_bytes` accepted but **ignored** — server measures). Success → `200 {ok:true, id, size_bytes,
  telegram_message_id, evicted:[...]}`. Source already purged → `404 {error:'SOURCE_PURGED'}`.
  Validation failure → `400`. Auth failure → `403`.
- `DELETE /api/bookmark/:id` → `200 {ok:true, id}` (idempotent: unknown id still `200`).
- `GET /api/bookmarks` → `200 [...]` ordered `bookmarked_at ASC` (client reverses for newest-first).

---

## 3. Component 1 — Two-tier gate (`auth.html` [NEW], `viewer.html`, `styles.css`)

Static-site truth: the viewing PIN is **obfuscation, not authentication** — its hash ships in the
bundle. The owner key is the real credential and **never** enters the bundle. Implement honestly:

1. Build injects `window.__PIN_SHA256` (hex of SHA-256 of `VIEWING_PIN` env) into `viewer.html`;
   plaintext PIN appears nowhere in `site/`.
2. `auth.html` renders `#lockScreen` **before** `#feedContainer` (include order in `viewer.html`:
   header → auth → feed → modals → player script). `#feedContainer` starts `hidden` until unlock —
   no feed flash. Keypad: digits, `⌫`, `C`; compare `sha256(entered)` (SubtleCrypto) to
   `window.__PIN_SHA256` or accept `localStorage digest_pin_ok==='1'`.
3. Rate-limit: 5 wrong attempts → 30 s lockout (`lockUntil` in memory, not localStorage).
   Unlock sets `digest_pin_ok='1'`; never store the PIN itself.
4. One-time links: on load, parse `?pin=` / `?owner_key=` **first**, persist (`digest_pin_ok`,
   `digest_owner_key`), then `history.replaceState` to strip query **before any fetch** (referer
   hygiene). `?pin=` still requires beating the hash check — it only saves typing, it is not a bypass.
   Never log keys; warn in-code: owner link is single-use, never forward it.
5. `isOwnerDevice()` = `Boolean(localStorage.getItem('digest_owner_key'))`. Non-owner devices:
   bookmark buttons hidden via CSS class on `<body>` (`body.readonly .bookmark-btn{display:none}`),
   grid renders from local snapshot without sync. Owner-gating is UX only — **server re-checks every
   mutation** (§2), so a forged DOM gains nothing.

## 4. Component 2 — Worker + D1 (`cloudflare/worker.js`, `schema.sql`, `wrangler.toml` [ALL NEW])

### 4.1 Schema (D1/SQLite)

```sql
CREATE TABLE IF NOT EXISTS bookmarks (
  id TEXT PRIMARY KEY,
  creator_handle TEXT NOT NULL,
  caption TEXT DEFAULT '',
  category TEXT DEFAULT '',
  thumbnail_url TEXT DEFAULT '',
  video_url TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  bookmarked_at TEXT NOT NULL,
  telegram_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_at ON bookmarks (bookmarked_at);
```

`size_bytes` = **video bytes only** (thumbnails ~100 KB excluded by definition — state this in code
comment so a future reader doesn't "fix" the accounting). `bookmarked_at` = server-generated
`new Date().toISOString()` (never trust client clock for FIFO order).

### 4.2 POST /api/bookmark — exact order (each step handles its error; no fall-through)

1. Auth (§2) → `OPTIONS` short-circuit → parse JSON, `400` on missing `id`/`video_url`.
2. Derive source key: `video_url` must start with `env.R2_PUBLIC_BASE_URL`; strip prefix to get the
   `videos/<week>/<file>` key, else `400 {error:'UNTRUSTED_SOURCE'}` (blocks SSRF-by-URL: worker only
   copies from its own bucket, never fetches arbitrary URLs server-side).
3. `src = await env.MY_BUCKET.get(sourceKey)`; `null` → `404 {error:'SOURCE_PURGED'}` (client reverts
   optimistic toggle + toast "This reel expired from the weekly digest").
4. `size = src.size` (**authoritative**; client value ignored — fixes blocker #3). Reject `size > 50 MB`
   with `413 {error:'TOO_LARGE'}` (Telegram Bot API ceiling; R2 copy skipped, nothing stored).
5. R2 copy preserving iOS seekability (fixes blocker #4):
   `await env.MY_BUCKET.put('bookmarks/'+id+'.mp4', src.body, {httpMetadata:{contentType:'video/mp4',
   cacheControl:'public, max-age=31536000, immutable'}})`.
6. D1 `db.batch([INSERT OR IGNORE ..., SELECT COUNT+SUM ..., ...deletes])` — compute evictions from a
   fresh `SELECT id FROM bookmarks ORDER BY bookmarked_at ASC` **inside the same request** after insert,
   deleting oldest while `count>300 OR bytes>3.5e9`. TOCTOU note: two concurrent requests can each
   overshoot by ≤1 row; therefore run a **re-check pass** after R2 deletes (one more SELECT; delete any
   residual over-cap oldest). Self-healing beats distributed locking here.
7. Delete evicted + replaced R2 keys, then regenerate `bookmarks/manifest.json` from full ordered
   SELECT (300 rows — trivial) as read-cache/debug artifact.
8. Telegram dispatch (§4.4, non-blocking for response: `event.waitUntil` OK, but record message id
   best-effort; failure leaves `telegram_message_id=null`, bookmark still succeeds).

### 4.3 Thumbnails (fixes blocker #6)

R2 `videos/` holds mp4 only — there is nothing to "copy". Worker does
`fetch(thumbnail_url)` (client-supplied HTTPS URL; 10 s timeout via AbortController), on `200` puts
`bookmarks/<id>_portrait.jpg` with `contentType:'image/jpeg'`; **any failure → proceed with
`thumbnail_url=''`** and grid falls back to `<video poster>` frame / placeholder. Thumbnail failure
never fails the bookmark. Prefer caller's portrait URL (`*_portrait.jpg`, matching
`site_builder.py:191`); the landscape `thumbnails/<id>.jpg` is acceptable fallback.

### 4.4 Telegram URL-only dispatch (fixes blocker #5)

- Never proxy bytes. `POST https://api.telegram.org/bot<TOKEN>/sendVideo` with **JSON body**
  `{chat_id, video: <public bookmarks URL>, caption: caption.slice(0,1000), supports_streaming:true}` —
  1000 chars keeps under Telegram's 1024 cap with margin; **no `parse_mode`** (user captions contain
  literal `@_#` that would break entity parsing).
- Public URL = `env.R2_PUBLIC_BASE_URL + '/bookmarks/' + id + '.mp4'` (requires public read on the
  `bookmarks/` prefix or presigned URL — deployment choice, Appendix A; keys are unguessable ids).
- Telegram fetch failure → bookmark still `200`s; client does not retry Telegram (cold archive is
  best-effort; hot R2 copy is the guarantee).

### 4.5 DELETE + GET

- `DELETE`: auth → `DELETE FROM bookmarks WHERE id=?` → delete both R2 keys (ignore missing) →
  regenerate manifest → `200`. Telegram message retained permanently. Unknown id → still `200`.
- `GET`: auth (owner key required, §2) → ordered SELECT → JSON. No pagination (300 rows max).

---

## 5. Component 3 — PWA client (`player.js`, `feed.html`, `header.html`, `bookmarks.html`, `styles.css`)

### 5.1 WhatsApp mp4 share (replaces thumbnail-share in `player.js:422-465`)

Current code shares the prefetched **thumbnail image**; the plan needs the **mp4**. Gesture-safety is
the crux: `await caches.match()` inside the tap handler risks losing Safari's transient activation.
Fix by pre-resolving the active card's `File` at card-activation time:

- Add `let activeShareFile=null, activeShareReelId=null;` Call `resolveShareFile(card)` from
  `playCardVideo` (already the single activation funnel): open `caches.open('ig-digest-media-v1')`,
  `match(video.dataset.src.split('?')[0])` (same strip-query keying as `site/sw.js:101`), build
  `new File([blob], handle_id.mp4, {type:'video/mp4'})`, store + `activeShareReelId`. Wrap in
  try/catch — pre-resolution never breaks playback. On card change the old `File` is dereferenced
  (1-slot bound, ~15–25 MB peak; `File([blob])` references bytes, no copy).
- `shareReelWhatsApp` becomes synchronous on the hot path: if `activeShareReelId===reelId &&
  activeShareFile && navigator.canShare({files:[activeShareFile]})` → `navigator.share({files,
  text})` immediately. Else async fallback: try cache match now (may lose gesture on iOS — accept),
  then `<a download>` + clipboard caption on desktop, `whatsapp://send?text=` on mobile without
  Web Share (existing `fallbackShare` kept verbatim).
- Sanitise filename: `creatorHandle.replace(/[^a-z0-9_-]/gi,'_')` — current code interpolates raw
  handle into `File` name and share text (minor injection/odd-filename surface).

### 5.2 IndexedDB outbox (fixes blocker #7)

DB `ig-digest-store`, store `pending-bookmark-ops` (keyPath `opId` auto-increment), record
`{op, id, payload, ts, attempts}`. Rules:

- Optimistic toggle paints instantly; op appended in the same handler (if IndexedDB unavailable —
  private mode — fall back to direct fetch, no queue).
- `flushBookmarkOutbox()` runs on `online` event, app launch (after unlock), and Bookmarks-tab open.
  Strict `ts` order; stop at first network failure (preserve order), continue past dropped poison pills.
- Per-op outcome: `200` → drop op, update snapshot; `403` → drop op, toast "Owner key invalid —
  re-link this device", set `body.readonly`; `404 SOURCE_PURGED` / `400` / `409` / `413` → drop op
  (permanent), revert toggle where applicable; `5xx`/network → `attempts++`, exponential backoff
  1s/5s/30s/5m, **park after 5 attempts**: keep op, badge shows `⚠ N pending`, retry on next trigger.
- After flush: `GET /api/bookmarks` reconciles (server truth overwrites local snapshot + button states
  + badge). Re-bookmark of an existing id is `INSERT OR IGNORE` → safe replay (at-least-once, no dupes).

### 5.3 Grid, overlay & scroll isolation (the plan's thinnest section — specified here)

- `feed.html`: add bookmark button inside `.reel-actions` (after share button); hydrate its
  active state on render from `localStorage digest_bookmarks_snapshot` (id set) + post-reconcile.
  `toggleBookmark(id, event)` must `stopPropagation/preventDefault` (it sits inside the tap-to-pause
  card surface — same discipline as `shareReelWhatsApp:423-426`).
- `header.html`: add `🔖 Bookmarks <badge>` chip; badge = snapshot count, `⚠ N` suffix when parked ops.
- `bookmarks.html` [NEW]: `#bookmarksContainer` (normal document scroll — **not** snap), search input
  filtering in-memory snapshot by handle+caption (debounce 120 ms; `toLowerCase().includes`), grid of
  `<img loading="lazy" src=thumbnail_url||video poster>` cards, `#bookmarkOverlay` fixed inset-0
  z-index above top-chrome with its own `<video controls playsinline>` set from `video_url` on open
  (lazy — no feed sliding-window reuse needed; pause on close).
- View-switch protocol `toggleBookmarksView(open)` (new, ~20 lines — the missing piece):
  open: save `feed.scrollTop`, pause `currentActiveCard` video, `feed.style.display='none'`,
  `document.body.style.overflow='hidden'` (feed is snap container; body lock prevents double scroll),
  render grid, show container. Close: reverse, `feed.scrollTop = saved`, restore body overflow,
  resume only on explicit user tap (never autoplay — respects the manual-pause contract in
  `player.js:66-70`). Overlay open adds a second `overflow:hidden` layer + `Esc`/✕ close returning to
  grid scroll offset (save `grid.scrollTop` too). `visibleCards()` already excludes `display:none`
  cards so the IntersectionObserver goes quiet while hidden — no observer teardown needed, but
  `playCardVideo` must early-return when `feed.style.display==='none'` (one-line guard).

---

## 6. Component 4 — Pipeline safety (`storage_r2.py`, `config.py`)

1. `purge_expired_r2_objects` already lists `Prefix="videos/"` (line 111) and `purge_unreferenced_r2_videos`
   only paginates `videos/<week>/` (line 202) — `bookmarks/` is structurally unreachable. Harden to
   guarantee: add `assert prefix.startswith("videos/")` in both list calls + a defensive
   `if key.startswith("bookmarks/"): continue` in the stale-key loop. Cheap, permanent.
2. **Quota math fix (blocker #1):** `R2_STORAGE_QUOTA_BYTES = 5 GB` while the design's own peak is
   ~3.1 GB weekly + 3.5 GB bookmarks ≈ 6.6 GB — the preflight would start aborting weekly uploads as
   bookmarks grow. Set quota to **8 GB** (leaves 2 GB buffer under the 10 GB free tier) and update
   `test_check_preflight_quota_logic` thresholds accordingly. `get_bucket_storage_usage` keeps counting
   the whole bucket (correct — billing is bucket-wide).
3. `upload_reel_to_r2` already sets `ContentType: video/mp4` (line 329) — feed videos stay seekable;
   no change. The **worker** is where Content-Type was at risk (§4.2 step 5).

---

## 7. Edge-case matrix (implementer checklist — every row has an owner section above)

| Edge | Behaviour |
|------|-----------|
| Source video already purged (8-day window) | `404 SOURCE_PURGED`, client reverts toggle + toast (§4.2.3) |
| Same reel bookmarked twice / replayed op | `INSERT OR IGNORE` → no dupe, `200` (§4.2.6, §5.2) |
| 3 rapid taps / bookmark-vs-delete race | D1 serialises; re-check pass trims residual over-cap (§4.2.6) |
| Offline tap, airplane/subway | Queued, ordered replay, parked after 5 fails with badge (§5.2) |
| Wrong/rotated owner key | `403` → drop op, read-only mode, re-link toast (§5.2) |
| Video >50 MB | `413`, nothing stored, toast (§4.2.4) |
| Thumbnail fetch fails | Bookmark succeeds, `thumbnail_url=''`, grid fallback (§4.3) |
| Telegram down / caption with emoji/HTML | Best-effort, no `parse_mode`, 1000-char cut (§4.4) |
| iOS share gesture timeout | Pre-resolved File hot path; documented fallback (§5.1) |
| Private-mode IndexedDB absent | Direct-fetch fallback, no queue (§5.2) |
| Feed/bookmark scroll bleed | Save/restore scrollTops, body lock, observer guard (§5.3) |
| Weekly purger vs bookmarks | Prefix asserts + `bookmarks/` skip (§6.1); quota 8 GB (§6.2) |
| `?owner_key=` link leaked/forwarded | Rotate `OWNER_KEY` via `wrangler secret put` (App. A); old links die instantly |

## 8. Verification

### 8.1 Automated (run before manual)

```bash
pytest tests/test_storage.py tests/test_bookmarks_isolation.py tests/test_architectural_fixes.py tests/test_sw_range.py
```

- `tests/test_bookmarks_isolation.py` [NEW]: (a) `purge_expired_r2_objects` with moto-style fake S3
  listing containing `bookmarks/x.mp4` returns/deletes only `videos/` keys; (b) same for
  `purge_unreferenced_r2_videos`; (c) preflight at 6.5 GB + 1 GB estimate passes under new 8 GB quota.
- Update `test_check_preflight_quota_logic` to 8 GB thresholds (existing 5 GB numbers will fail — intended).
- D1 SQL smoke: run Appendix B statements through `sqlite3 :memory:` (D1 is SQLite; `batch` maps 1:1
  to a transaction) to validate FIFO eviction query logic without cloud access.
- `site_builder.py` change (PIN-hash injection) covered by asserting `site/index.html` contains
  `__PIN_SHA256` and no plaintext `VIEWING_PIN`.

### 8.2 Manual (iOS Safari + desktop Chrome)

1. Fresh load → lock screen first, no feed flash; 5 wrong PINs → 30 s lockout; correct PIN persists.
2. Owner link `?pin=&owner_key=` → keys stored, URL stripped, reload stays unlocked with bookmark buttons.
3. Share on iOS: sheet shows **video** (not thumbnail still) with caption; airplane-mode cached reel shares.
4. Offline bookmark → instant toggle → reconnect → R2 object + D1 row + Telegram post appear.
5. Grid: 3-col render, search filters, overlay plays with swipe nav, ✕ returns to grid scroll position,
   feed scroll position preserved; no autoplay on return.
6.wrangler tail shows `SOURCE_PURGED` handled gracefully for an expired-week reel.

## 9. Implementation sequence (single-shot order — each step leaves tree green)

1. `config.py` quota 8 GB + `storage_r2.py` guards + `test_bookmarks_isolation.py` → `pytest tests/test_storage.py`.
2. `cloudflare/schema.sql` → local sqlite3 smoke → `wrangler d1 create` + migrate (App. A).
3. `cloudflare/worker.js` + `wrangler.toml` → `wrangler deploy` → curl the 5 contract cases in §2.
4. `auth.html` + `viewer.html` order + `site_builder.py` hash injection → rebuild, inspect `site/`.
5. `player.js` share pre-resolution → device test step 3.
6. `feed/header/bookmarks.html` + `styles.css` + `toggleBookmarksView` → device test step 5.
7. Outbox + reconcile → device test step 4. Full suite + manual pass.

---

## Appendix A — Wrangler setup (zero-to-deployed, ~2 min; secrets never touch git)

```bash
npm i -g wrangler
wrangler login
mkdir -p cloudflare
# schema.sql: paste §4.1 block
wrangler d1 create ig-digest-bookmarks   # copy database_id into wrangler.toml below
wrangler d1 execute ig-digest-bookmarks --file=cloudflare/schema.sql
wrangler secret put OWNER_KEY            # high-entropy, e.g. openssl rand -hex 32
wrangler secret put TELEGRAM_BOT_TOKEN
wrangler secret put TELEGRAM_CHAT_ID
wrangler deploy
```

`cloudflare/wrangler.toml` (commit this; secrets stay in `wrangler secret`, not here):

```toml
name = "ig-digest-api"
main = "worker.js"
compatibility_date = "2026-09-01"
[[d1_databases]]
binding = "DB"
database_name = "ig-digest-bookmarks"
database_id = "<paste-from-create>"
[[r2_buckets]]
binding = "MY_BUCKET"
bucket_name = "instagram-digest"
[vars]
R2_PUBLIC_BASE_URL = "https://<your-r2-public-host>"
```

Contract smoke test after deploy (expect 403, then 200 after adding header):

```bash
curl -i https://ig-digest-api.<subdomain>.workers.dev/api/bookmarks
curl -H "Authorization: Bearer $OWNER_KEY" https://ig-digest-api.<subdomain>.workers.dev/api/bookmarks
```

Key rotation (leaked owner link): `wrangler secret put OWNER_KEY` + re-link device. Reads keep working
through rotation only after device update — by design.

## Appendix B — D1 FIFO check without cloud (stdlib sqlite3 mirrors D1 semantics)

```bash
sqlite3 :memory: < cloudflare/schema.sql
sqlite3 :memory: "INSERT INTO bookmarks SELECT ... ; SELECT id, size_bytes FROM bookmarks ORDER BY bookmarked_at ASC;"
```

Validate: insert 301 rows → oldest evicted; sizes summing >3.5e9 → oldest evicted until under cap.
`db.batch([...])` in the worker maps to one SQLite transaction — keep all cap deletes in a single
batch call, never split across awaits.

---

## Hardening deltas vs the v1 plan (what changed and why)

1. Frozen §2 contract (single Bearer header, exact status codes) — v1 used two header names.
2. Server-measured `size_bytes` + `UNTRUSTED_SOURCE` + `413` — v1 trusted client metadata.
3. `get`→`put` with mp4 metadata + HTTP-fetched thumbnails that may fail soft — v1 assumed R2 CopyObject.
4. Telegram POST JSON, 1000-char cut, no parse_mode, best-effort — v1 GET query string.
5. 5-retry cap + poison-pill table + parked badge — v1 retried unboundedly.
6. Gesture-safe share via activation-time pre-resolution — v1 awaited cache inside the tap.
7. `toggleBookmarksView` scroll protocol + observer guard — v1 had no isolation mechanics.
8. 8 GB quota + prefix asserts — v1's 5 GB quota contradicted its own storage table.
9. Honest PIN-hash treatment + rate limit + include order — v1 implied a real server-side PIN check
   that cannot exist on a static host.
