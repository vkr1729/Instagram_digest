# Frontier Model Review & Deep-Probing Prompt (v1.3 Hardened Baseline)

Copy and paste the entire prompt below into your frontier model (e.g., Claude 3.7 Sonnet, OpenAI o3 / GPT-4.5, or Gemini 1.5 Pro with Thinking enabled).

```markdown
# MISSION: ADVERSARIAL ARCHITECTURAL REVIEW & SECOND-ORDER PROBING INTERVIEW

You are a Principal Distributed Systems & WebKit Platform Architect. Your job is to conduct a relentless, adversarial technical review of a hardened design specification (v1.3) for the "Instagram Digest" application.

We have already identified and designed initial solutions for 5 baseline platform traps:
1. Telegram's 20 MB URL limit (addressed via hybrid URL / multipart streaming up to 50 MB).
2. R2 FIFO pruning (addressed via chronological `bookmarks/manifest.json` with a 300-count / 3.5 GB Dual-Safety Cap).
3. Multi-device sync (addressed via Worker `GET /api/bookmarks` reconciling into local storage).
4. iOS Safari WebKit memory pressure (addressed via a strict 1-slot active card buffer).
5. Un-bookmark lifecycle (purges R2, retains Telegram cold backup).

Now, your mission is to go **one layer deeper**: probe for second-order failure modes, edge-case race conditions, network partition behaviors, and security boundaries.

---

## 1. REPOSITORY & INFRASTRUCTURE CONTEXT

- **What the app is:** Instagram Digest converts an unbounded algorithmic Instagram Reels feed into a finite weekly briefing (top 300 reels) delivered as a zero-dependency static Progressive Web App (PWA) with $0 marginal cost.
- **Frontend & Hosting:** Static HTML5/CSS/JS on GitHub Pages (`https://vkr1729.github.io/Instagram_digest`), deployed via `git push gh-pages`. There is NO cloud server backend running 24/7.
- **Video Storage:** Cloudflare R2 (10 GB free tier, $0 egress fees). Reels stored under `videos/{week_id}/{rank}_{handle}_{id}.mp4`.
- **Retention:** Every Friday, local Linux cron pipeline runs `storage_r2.py`, uploading the new top 300 and purging R2 videos older than 8 days under `videos/{date}/` to keep storage under safety caps.
- **Media Delivery & Seekability:** Service Worker (`sw.js`) with browser `CacheStorage` (`ig-digest-media-v1`) and synthetic HTTP 206 Partial Content byte-range slicing for iOS Safari AVFoundation.

---

## 2. THE HARDENED DESIGN SPECIFICATION (v1.3) UNDER REVIEW

```markdown
### Feature 1: WhatsApp Video File Sharing
- Uses Web Share API Level 2 (`navigator.share({ files: [file], text: shareText })`).
- Bounded 1-Item Window: Only the single active card's video is held as a `File` in JS heap (~15-25 MB peak). Auto-freed on swipe to prevent iOS Safari memory crashes.
- Microtask Activation: Video extracted from local ServiceWorker CacheStorage in ~15ms, preserving transient user gesture.
- Caption: `text` parameter populates WhatsApp video caption with creator handle, digest URL, and snippet.
- Desktop fallback: 1-click `<a download>` stream to disk.

### Feature 2: Hybrid Bookmarks (Cloudflare R2 + Telegram Cold Backup + 3-Column Grid)
- PWA Client:
  - 🔖 icon on reel card. Instant optimistic toggle.
  - "🔖 Bookmarks" header tab hides `#feedContainer` and renders an isolated Instagram-style 3-column thumbnail grid (`#bookmarksContainer`) with instant search/filter.
  - Tapping thumbnail opens full-screen reel player overlay with swipe-up/down navigation and un-bookmark toggle.
- Cloudflare Worker Bridge (`/api/bookmark`):
  - Copies video to `bookmarks/{id}.mp4` and thumbnail to `bookmarks/{id}_portrait.jpg`.
  - Maintains `bookmarks/manifest.json` on R2: chronological array of `{ id, creator, caption, size_bytes, bookmarked_at, telegram_msg_id }`.
  - Dual-Safety Cap: Enforces max 300 items OR 3.5 GB total bookmark storage (FIFO eviction of oldest items from R2).
  - Multi-Device Sync: Exposes `GET /api/bookmarks` so phone, tablet, and desktop all see the exact same grid.
  - Telegram Cold Backup:
    - If video <= 20 MB: Dispatches Telegram `sendVideo` by URL.
    - If video 20 MB – 50 MB: Streams R2 bytes via `multipart/form-data` to Telegram Bot API (preserves single-file playback bubble in chat).
  - Un-bookmark: `DELETE /api/bookmark/:id` removes record from manifest and deletes R2 objects; leaves message in Telegram as immutable cold backup.
```

---

## 3. SECOND-ORDER TRAPS TO PROBE & RESOLVE

Scrutinize the following areas relentlessly:

1. **Manifest Concurrency & Race Conditions in Cloudflare Worker:**
   - Cloudflare Workers are distributed and stateless. If a user rapidly taps bookmark on 3 reels in succession, or bookmarking happens while an un-bookmark is processing:
   - Does reading `manifest.json`, modifying the array in memory, and writing back to R2 risk an overwrite race condition (lost updates)?
   - How should concurrent writes to `manifest.json` be handled (e.g. ETag/conditional `If-Match` on R2 `put`, atomic queues, or Cloudflare KV / D1 SQLite)?
2. **Offline & Flaky Network Resilience in PWA:**
   - The user is in an airplane or subway without internet and taps 🔖 on a cached reel.
   - If the POST to the Worker fails, how does the PWA handle it? Does it maintain an offline sync queue in IndexedDB/localStorage to retry when back online?
3. **CORS & Authentication Boundary:**
   - The Worker lives at `https://ig-digest-api.<subdomain>.workers.dev` while the PWA runs at `https://vkr1729.github.io`.
   - What CORS headers must be configured?
   - What prevents an unauthorized third party from finding your public Worker URL and spamming bookmark POSTs to flood your Telegram channel or R2 bucket? Should there be a simple shared token or header secret?
4. **Cloudflare Worker CPU & Subrequest Execution Limits:**
   - On the Cloudflare Workers Free Tier, each request has a **50 subrequest limit** and **10ms CPU time** (wall-clock I/O wait is unbounded).
   - When streaming a 35 MB video from R2 to Telegram via `multipart/form-data`, does the Worker stay comfortably within free tier memory (128 MB) and execution constraints?

---

## 4. INSTRUCTIONS FOR THE INTERVIEW & FINAL OUTPUT

### Phase 1: Interactive Probing (Grilling)
- Do NOT jump straight to writing a new plan.
- Walk down the decision tree and interview the user with sharp, targeted questions to resolve the second-order architectural dependencies one by one.
- **Crucial Rule:** For EVERY question you ask, provide your concrete **"(Recommended)"** answer first with a clear technical justification, followed by viable alternatives, formatted so the user can easily respond with their choice.
- Ask questions **one at a time** (or in a tightly cohesive batch of no more than 2 related decisions) and wait for user input before moving forward.

### Phase 2: Revised Specification Delivery
Once all decisions are resolved with the user:
- Formulate the final, battle-hardened specification.
- Save the updated specification to:
  `docs/superpowers/specs/2026-09-14-whatsapp-video-share-and-bookmarks-revised-design.md`
- Include a dedicated section titled:
  `## Deviations from Original Design & Architectural Rationale`
  explaining every improvement made over the v1.3 spec and why it guarantees production stability.

---
Begin immediately by introducing your review stance, pointing out the most critical second-order risk (e.g., manifest write race conditions or worker authentication), and asking your first probing decision question.
```
