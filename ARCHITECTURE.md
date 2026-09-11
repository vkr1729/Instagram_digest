# Instagram Digest — System Architecture & Technical Rationale

> **Audience & Purpose:**  
> This architectural design document details the end-to-end topography, mechanical invariants, and design trade-offs of the **Instagram Digest** platform. It is explicitly tailored for deep technical review by principal software architects and frontier AI reasoning models to stress-test design choices, evaluate edge cases, and propose high-leverage optimizations.

---

## 1. Domain Problem & Design Principles

### 1.1 The Behavioral Problem
Modern social video feeds (Instagram Reels, TikTok, YouTube Shorts) are architected around variable-ratio dopamine schedules, infinite scroll mechanics, and engagement algorithms that optimize for platform time-on-app rather than signal-to-noise ratio. Users seeking educational, technical, or creative updates from specific creators are inevitably pulled into doom-scrolling.

### 1.2 The Core Solution
**Instagram Digest** transforms Instagram Reels into a **finite, structured, high-signal weekly media briefing**:
1. **Finite Quota:** Exactly 300 top-ranked reels per week (with an optional on-demand `+100` expansion on desktop).
2. **Anti-Doomscroll Watched State:** Watched reels are tracked in local storage and excluded from playback; completing the digest displays a celebratory terminal screen (*"You're all caught up! 🎉"*).
3. **Daily Mindful Check-in:** A gentle nudge at 50 reels in a single calendar day promotes conscious media consumption without hard lockouts.
4. **Zero Cloud Infrastructure Fees:** Cloudflare R2 provides S3-compatible storage with **$0 egress fees**, paired with GitHub Pages for high-availability static web hosting.
5. **Zero-Roundtrip PWA:** The published viewer is compiled into an atomic, single-document Progressive Web App with zero external CSS/JS dependencies, complete with Service Worker media caching and HTTP 206 byte-range slicing for offline flight mode.

---

## 2. End-to-End Pipeline Topography

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             INGESTION LAYER                                 │
│                                                                             │
│   [ Chrome SQLite / Session Cookies ]                                       │
│                    │                                                        │
│                    ▼                                                        │
│           cookie_exporter.py                                                │
│                    │                                                        │
│                    ▼ (Netscape cookies.txt / Playwright cookies.json)        │
│              extractor.py                                                   │
│        ┌───────────┴───────────────────────────────┐                        │
│        ▼                                           ▼                        │
│   Tracked Creators                          External Discovery              │
│   (Profile Scraping)                        (Reels Feed Crawl)              │
│   - Following list sync                     - Visible likes ≥ 25,000        │
│   - 7-day candidate window                  - Human jitter: 2.8s – 4.8s     │
│   - Direct CDN / yt-dlp                     - 12s cooldown every 25 reels   │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             RANKING LAYER                                   │
│                                                                             │
│                                ranker.py                                    │
│   - Engagement Multiplier: V_reel / Median(V_creator)                       │
│   - Anti-Monopoly Guard: Max 4 reels per creator                            │
│   - Proportional Category Quotas (40% Ent, 15% Fin, 15% Tech, 10% Niche...) │
│   - Append-Only Invariance on Expansion: Tail insertion (#258 – #300)        │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             STORAGE LAYER                                   │
│                                                                             │
│                              storage_r2.py                                  │
│   - Cloudflare R2 Object Storage (S3 API, $0 Egress Bandwidth)              │
│   - Content-Addressed Asset Invariance: Deduplication by Immutable Reel ID  │
│   - Rolling Retention Purge: Automatic cleanup of media > 14 days           │
│   - Orphan Object Pruner: Garbage-collects unreferenced video assets        │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            BUILD & DELIVERY                                 │
│                                                                             │
│                             site_builder.py                                 │
│   - Inlined Modular Jinja Partials (styles.css, player.js, header, feed...) │
│   - Static Asset Optimization (Thumbnails, WebManifest, Icons)              │
│   - Atomic Compilation: Outputs site/index.html & site/local_index.html     │
│   - Direct Git Push to GitHub Pages (orphan gh-pages branch)                │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
         ┌───────────────────────────┴───────────────────────────┐
         ▼                                                       ▼
┌───────────────────────────────────┐   ┌────────────────────────────────────┐
│      GITHUB PAGES & PWA           │   │     LOCAL DESKTOP COMPANION        │
│                                   │   │                                    │
│  - Static Atomic Index (HTML/CSS) │   │  - local_server.py (Port 8080)     │
│  - sw.js HTTP 206 Partial Slicing │   │  - RFC 7233 Byte-Range Video Stream│
│  - Native Instagram Floating HUD  │   │  - Channel Manager (/channels)     │
│  - Strict object-fit: contain     │   │  - Ad-hoc Sync & Expand Triggers   │
│  - Sliding Window Memory Cleanup  │   │  - Launch Script (launch.sh)       │
└───────────────────────────────────┘   └────────────────────────────────────┘
```

---

## 3. Deep Architectural Choices & Rationale

### 3.1 Session Extraction & Anti-Bot Evasion (`cookie_exporter.py`, `extractor.py`)

#### Decision: Direct Browser Cookie Extraction vs. Headless Login Automation
* **Choice:** Extract active authentication tokens (`sessionid`, `csrftoken`, `ds_user_id`) directly from the user's local Google Chrome SQLite profile (`cookie_exporter.py`), exporting into dual formats: Netscape `cookies.txt` (for `yt-dlp`) and JSON format (for Playwright).
* **Rationale:** Instagram's bot detection systems employ aggressive device fingerprinting, TLS JA3/JA4 fingerprint analysis, and CAPTCHA challenges during automated login flows. By piggybacking on a pre-authenticated user session from an established desktop browser, we bypass the entire login challenge surface with zero credential storage in plain text.
* **Failure Circuit Breaker:** When cookies expire, the system raises `CookieExpiredException`, sends an email notification via `notifier.py`, and aborts rather than triggering challenge loops.

#### Decision: Pacing & Anti-Detection Architecture for Feed Discovery
* **Choice:** 
  1. High-signal pre-filter: Visible likes $\ge 25,000$ (or comments $\ge 150$ if likes are hidden).
  2. Randomized humanized scroll jitter: `time.sleep(random.uniform(2.8, 4.8))`.
  3. Cooldown pause: Forced `time.sleep(12.0)` rest every 25 evaluated reels.
* **Rationale:** Automated scrapers that issue constant-frequency keystrokes or scrolls at superhuman velocity (>1 scroll/sec) are immediately flagged by Instagram's behavioral telemetry. Emulating the distribution of a human user browsing the feed prevents IP rate-limiting, shadowbanning, and session invalidation.
* **Context Recycling:** Playwright contexts are recycled every 40 navigations to eliminate Chromium memory leaks and detached DOM retention.

---

### 3.2 Media Delivery & Asset Invariance (`storage_r2.py`, `main.py`)

#### Decision: Cloudflare R2 vs. AWS S3 / Git LFS / Direct CDN
* **Choice:** Cloudflare R2 object storage with S3-compatible API.
* **Rationale:**
  1. **Egress Economics:** AWS S3 charges $0.09/GB for egress. Streaming 300 high-definition reels (~2.5 GB) across multiple devices weekly would incur substantial ongoing bandwidth costs. Cloudflare R2 has **$0 egress fees**, enabling free high-bandwidth video streaming indefinitely.
  2. **Storage Limits:** Committing videos to Git or GitHub Pages would violate repository storage limits (1 GB soft cap on GitHub Pages). R2 keeps the repository strictly code-only (~2 MB).
  3. **Direct Instagram CDN Links vs. R2:** Direct Instagram CDN URLs expire within hours due to signed security tokens (`?_nc_ht=...&oh=...`). Storing files in R2 ensures stable, immutable URLs across the entire 7-day digest window.

#### Decision: Content-Addressed Asset Invariance
* **Choice:** Identify and index local and remote video assets by immutable entity ID (`{reel_id}.mp4` or `{creator_handle}_{reel_id}.mp4`) rather than ordinal rank numbers (`{rank:02d}_{handle}_{id}.mp4`).
* **Rationale:** When an existing digest is expanded (e.g. +43 reels to reach 300, or +100 on desktop), ordinal ranks shift. If assets are named by ordinal prefix, the downloader would fail to find the existing local video and re-download it over the network. Under content-addressed lookup, `main.py` searches for `*_{reel_id}.mp4` before invoking network downloads, achieving **100% deduplication and zero redundant bandwidth consumption**.

---

### 3.3 Frontend Architecture & Static PWA Compilation (`templates/`, `site_builder.py`)

#### Decision: Modular Jinja Partials with Compile-Time Inlining
* **Choice:** 
  - Authoring: Modular files in `templates/partials/`:
    - `styles.css`: CSS tokens, layout, typography, animations (~870 lines).
    - `player.js`: Video engine, gesture isolation, watched tracker, cache manager (~1,750 lines).
    - `header.html`: Top chrome, story bubbles, action buttons.
    - `feed.html`: Feed container and reel card loops.
    - `modals.html`: Jump to reel, offline cache, mindful check-in.
    - `viewer.html`: Concise 75-line skeleton.
  - Compilation: Inlined via Jinja2 `{% include %}` into a single standalone document (`site/index.html`).
* **Rationale:**
  1. **Developer Ergonomics:** Editing separate CSS, JS, and HTML partials prevents monolithic file merge conflicts and cognitive overload.
  2. **PWA Runtime Snappiness:** Separate HTTP assets (`index.html`, `style.css`, `bundle.js`) introduce network waterfalls, render-blocking delays, and asset version mismatches when updated. Inlining produces a single atomic document: **1 HTTP request loads 100% of the UI**.
  3. **Offline Reliability:** Service Worker caching is simplified: caching `index.html` guarantees that CSS, JS, and HTML are always in complete lockstep, with zero partial-cache corruption risk.

#### Decision: Native Instagram Display Paradigm (`object-fit: contain` + Floating HUD)
* **Choice:** Strict `object-fit: contain` on `.reel-video`, paired with a transparent floating HUD overlay (`rgba(0,0,0,0.38)` gradient, text drop-shadows `0 1px 3px rgba(0,0,0,0.95)`).
* **Rationale:**
  - Setting `object-fit: cover` to fill tall mobile screens (19.5:9) catastrophically crops 9:16 vertical videos and cuts off ~60% of horizontal landscape videos.
  - Native Instagram handles aspect ratios by preserving native dimensions (`contain`) and floating metadata over the bottom with text drop shadows.
  - Earlier iterations of this project placed an 88% opaque black gradient across the bottom 100px, which mimicked a heavy, artificial toolbar. The floating HUD restores native video real estate while maintaining legibility over bright video backgrounds.

#### Decision: Mobile WebKit Safe Area Clearance
* **Choice:** `padding-bottom: max(56px, calc(env(safe-area-inset-bottom, 34px) + 16px))` on `.bottom-scrim`.
* **Rationale:** In iOS Safari standalone PWA mode (added to Home Screen), `env(safe-area-inset-bottom)` can inconsistently evaluate to `0px` depending on WebKit version and display mode. Providing a hard minimum floor of `56px` guarantees that captions and badges never collide with or get obscured by the iOS Home Indicator bar.

#### Decision: Touch Gesture Isolation vs. WebKit Synthetic Clicks
* **Choice:** Dedicated pointer tracking (`isPointerDown`, `pointermove` distance calculation) combined with a 500ms temporal scroll suppression window (`performance.now() - lastScrollTime < 500`).
* **Rationale:** Mobile WebKit dispatches a synthetic `click` event ~150ms after finger release following a fast momentum scroll. Because the newly centered card begins auto-playing on arrival, the incoming synthetic click immediately paused the video. The dual guard ensures that swipe gestures never trigger playback toggle events.

---

### 3.4 Service Worker & Offline Range Request Engine (`templates/sw.js`)

#### Decision: Synthetic HTTP 206 Partial Content Generation via `Blob.slice()`
* **Choice:** Service Worker intercepts `.mp4` video requests, queries `CacheStorage`, and constructs synthetic HTTP `206 Partial Content` responses using zero-copy `Blob.slice()`.
* **Rationale:**
  - Mobile Safari/WebKit strictly requires HTTP 206 Partial Content responses with `Range`, `Content-Range`, and `Accept-Ranges: bytes` headers for media playback.
  - Standard `CacheStorage.match()` returns a full HTTP 200 response. Passing HTTP 200 into an iOS `<video>` element fails or causes infinite stall loops.
  - `sw.js` parses incoming `bytes=start-end` Range headers, slices the cached `Blob` without memory copies, and synthesizes compliant HTTP 206 responses.

#### Decision: Dynamic Sliding Window Memory Virtualization
* **Choice:** JavaScript virtualization window restricting loaded media to `[-2, +3]` cards around active index. Cards outside this window have their `src` removed and call `video.load()` to free GPU and RAM decode buffers.
* **Rationale:** A 300-video feed cannot instantiate 300 active `<video>` decoders simultaneously without exceeding mobile RAM limits (resulting in iOS Safari `WebContent` process crashes). The sliding window caps active video instances to at most 5, ensuring smooth 60fps scrolling on resource-constrained devices.

---

## 4. Architectural Decision Matrix & Trade-Offs

| Decision Area | Selected Pattern | Alternative Considered | Trade-Off Rationale |
| :--- | :--- | :--- | :--- |
| **Media Hosting** | Cloudflare R2 | AWS S3 / Self-Hosted MinIO | R2 provides $0 egress fees; AWS S3 bandwidth costs scale linearly with video consumption. |
| **Frontend Distribution** | Atomic Compiled PWA | Multi-file SPA (React/Vite) | Inlining eliminates asset waterfalls and build tool complexity; zero runtime overhead. |
| **Video Scaling** | `object-fit: contain` | `object-fit: cover` | `cover` crops portrait videos and destroys landscape aspect ratios; `contain` preserves creator intent. |
| **Expansion Strategy** | Append-only tail insertion | Global re-ranking | Appending preserves watched progress and active index; global re-ranking forces users to re-start from #1. |
| **Authentication** | Local Chrome SQLite cookies | Headless credentials login | Bypasses Instagram 2FA, CAPTCHA, and TLS fingerprint heuristics completely. |
| **PWA Video Cache** | Synthetic HTTP 206 in SW | Standard CacheStorage 200 | WebKit media pipeline fails on HTTP 200 for video; synthetic range slicing is mandatory for iOS offline playback. |

---

## 5. Review Inquiries for Frontier Reasoning Models

When evaluating this repository, please direct adversarial focus to the following architectural vectors:

1. **Anti-Detection Longevity:** Can Instagram's heuristics distinguish our randomized human pacing (2.8s–4.8s + 12s cooling every 25 reels) from genuine human consumption via mouse/keyboard dispatch in Playwright? What additional behavioral telemetry (e.g. cursor bezier curves, touch event distributions) would increase detection resistance?
2. **WebKit Service Worker Reliability:** Are there edge cases where `Blob.slice()` byte-range calculation could mismatch Safari's internal media buffer expectations during rapid seeking or playback rate changes?
3. **DOM Virtualization Scaling:** With 300 (or 400) `<article class="reel-card">` nodes in the DOM, does passive DOM node presence (even with videos unloaded) degrade scroll performance on low-end Android devices? Would a full virtual-scroll recycler (swapping DOM nodes entirely) provide measurable CWV benefits without compromising scroll snap?
4. **State Synchronization:** Can the dual watched-state mechanism (`localStorage` on client, `data/watched.json` on local server) diverge during multi-device or offline PWA usage? What CRDT or timestamp-based reconciliation strategy would be most resilient?
