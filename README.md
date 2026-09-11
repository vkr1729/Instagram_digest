# Instagram Digest

An intentional, high-signal weekly digest of Instagram Reels curated into a clean, distraction-free Progressive Web App (PWA).

Replaces endless algorithmic doom-scrolling with a finite, curated media briefing streamed with zero bandwidth cost via Cloudflare R2 and deployed directly to GitHub Pages.

---

## Key Features

### 1. Intentional Curation
* **Configurable Finite Batch:** Curates a finite weekly batch of top reels (default 300 via `TOP_DIGEST_COUNT`), with on-demand `--expand` for high-consumption periods.
* **Anti-Doomscroll Watched State:** Automatically tracks completed reels in local storage, remembers your progress, and celebrates when you reach the end with a celebratory *"You're all caught up! 🎉"* screen.
* **Daily Mindful Check-in:** Soft nudge upon reaching your daily target to encourage conscious media consumption without hard blockers.

### 2. Native Floating HUD Experience
* **Native Aspect Ratio Preservation:** Strict `object-fit: contain` video scaling ensures portrait, square, and landscape videos are never cropped or distorted.
* **Floating Glass Overlay:** High-contrast text drop shadows over a subtle, transparent scrim eliminates intrusive black bars and artificial UI blocks.
* **Safe-Area Home Indicator Clearance:** Optimized for modern mobile PWAs (iOS Safari, Android Chrome) with adaptive viewport padding that floats text comfortably above the gesture bar.
* **Touch Gesture Isolation:** Dedicated pointer tracking and temporal scroll suppression prevents accidental play/pause triggers when swiping between reels.

### 3. Story Categories & Seamless Navigation
* **Dynamic Category Buckets:** Live gradient-ringed story bubbles for instant filtering by category:
  - 🔥 **All Top Reels**
  - 🎬 **Entertainment**
  - 💰 **Finance**
  - 💻 **AI & Tech**
  - 🧠 **Niche & Mindset**
  - 🏋️ **Health & Wellness**
  - 🥗 **Food & Recipes**
* **Instant Resume:** Selecting any category automatically navigates to your first unwatched reel in that topic.
* **Playback Speed & Gestures:**
  - Videos auto-enter immersive fullscreen on play (top chrome and info scrim hide); pausing restores them for share and caption.
  - Press-and-hold the right edge for latched `2x` speed; tap the right edge to exit (resets on reel change).
  - Double-tap center or press `F` for native fullscreen; horizontal drag scrubs the video.
  - Full keyboard shortcuts (`J`/`K` navigation, `Space` play/pause, `G` jump to reel).

### 4. Zero-Egress Cloudflare R2 Media Delivery
* **Zero Egress Fees:** All video streaming is offloaded to Cloudflare R2 object storage ($0 egress bandwidth), keeping GitHub Pages ultra-lean (~2 MB static footprint).
* **Stable Identity Across Re-ranks:** R2 keys carry rank prefixes, but every match (dedup, orphan pruning) is by immutable reel ID — renumbers never orphan live videos or trigger re-downloads.
* **Rolling Retention & Purge:** Automatically prunes media and weekly site archives older than configured retention days to remain comfortably within storage quotas. A pre-flight quota guard aborts before exceeding the 5 GB safety cap.

### 5. Offline PWA & Flight Mode
* **Permanent Offline Cache Access:** The `#offlineDownloadBtn` in the header allows inspection of local video storage and one-tap downloading of the entire digest.
* **RFC 7233 Range Slicing:** The Service Worker (`sw.js`) serves cached video as proper HTTP 206 partial content — including suffix ranges and `416` for unsatisfiable seeks — for smooth offline playback on mobile WebKit.
* **Rolling Video Window:** Automatically keeps a rolling window of adjacent videos cached offline during playback.

### 6. Local Management Dashboard
* **Zero-Bandwidth Desktop Server:** Run `python main.py --serve` to launch a local server on port 8080 streaming from local disk.
* **Channel Manager (`/channels`):** Visual web dashboard to audit followed creators, toggle subscriptions, inspect engagement metrics, and trigger ad-hoc refreshes.

### 7. Durability & Safety Posture
* Crash-safe state writes (temp + fsync + atomic replace) for every digest and state file; corrupt files are quarantined, never silently reset.
* Sync and expand pipelines are mutually exclusive; only one digest-mutating run at a time.
* Deploy gate refuses to publish under 60% of the target playable count; unplayable reels are dropped from manifest and site alike.

---

## System Architecture

The pipeline consists of a modular Python backend and an atomic, single-bundle static frontend:

```
[ Instagram Session ]
        │
        ├──> extractor.py (Playwright + yt-dlp)
        │       ├── Profile Reels from Tracked Creators
        │       └── External Discovery (≥25k-like filter, Gaussian pacing, pooled sessions)
        │
        ├──> ranker.py (Creator-Normalized Viral Scoring & Dynamic Quotas)
        │
        ├──> storage_r2.py (Cloudflare R2 Sync, Quota Guard & Rolling Purge)
        │
        └──> site_builder.py (Jinja2 Template Compilation & GitHub Pages Push)
                ├── templates/partials/styles.css
                ├── templates/partials/player.js
                ├── templates/partials/header.html
                ├── templates/partials/feed.html
                └── templates/partials/modals.html
                        │
                        ▼
            [ site/index.html (Atomic PWA) ]
                        │
            ┌───────────┴───────────┐
            ▼                       ▼
   [ GitHub Pages ]        [ Cloudflare R2 ]
   (Static Viewer)         (Video Streaming)
```

Supporting modules: `atomic_io.py` (crash-safe writes), `cookie_exporter.py`
(Chrome session reuse), `notifier.py` (email alerts), `local_server.py`
(local dashboard + byte-range streaming), `main.py` (CLI orchestrator).

Full design rationale: [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
Change history: [`CHANGELOG.md`](CHANGELOG.md) ·
Review brief: [`HANDOFF_REVIEW_PROMPT.md`](HANDOFF_REVIEW_PROMPT.md)

---

## Quick Start

### Prerequisites
* Python 3.10+
* Playwright (`playwright install chromium`)
* Cloudflare R2 Bucket (or local fallback mode)
* Authenticated Instagram session in Google Chrome

### Installation
```bash
git clone https://github.com/vkr1729/Instagram_digest.git
cd Instagram_digest

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium
```

### Configuration (`.env`)
Copy [`.env.example`](.env.example) to `.env` and fill in your values:
```env
# Cloudflare R2 Configuration
R2_ACCOUNT_ID=<your-account-id>
R2_ACCESS_KEY_ID=<your-access-key-id>
R2_SECRET_ACCESS_KEY=<your-secret-access-key>
R2_BUCKET_NAME=instagram-digest
R2_PUBLIC_DOMAIN=https://<your-r2-subdomain>.r2.dev

# GitHub Deployment
GH_PAGES_REPO=https://github.com/<your-username>/<your-repo>.git
PAGES_BASE_URL=https://<your-username>.github.io/<your-repo>

# Digest Settings
TOP_DIGEST_COUNT=300
MAX_PER_CREATOR=4
RETENTION_WEEKS=1
```

### Running the Pipeline

* **Run Full Weekly Sync & Deploy:**
  ```bash
  python main.py --sync --deploy
  ```
* **Run Ad-Hoc Midweek Refresh (reels since last run):**
  ```bash
  python main.py --ad-hoc --deploy
  ```
* **Expand Active Digest by N Additional Reels:**
  ```bash
  python main.py --expand <count> --deploy
  ```
* **Launch Local Desktop Server:**
  ```bash
  python main.py --serve --port 8080
  ```
* **Rebuild Static Site from Cache (zero network calls):**
  ```bash
  python main.py --build-only
  ```
* **Automated Friday run (cron-friendly, logs to `logs/`):**
  ```bash
  ./run_weekly.sh
  ```

---

## Operations Notes

* **Cookie expiry:** Instagram sessions expire. The pipeline detects it,
  emails you via the notifier, and aborts without touching the digest.
  Refresh by opening Instagram in Chrome, then re-run (or hit `/retrigger`).
* **Thin digests never ship:** under 60% playable items, the run aborts
  before deploy or site rebuild — the previous good digest stays live.
* **Sync vs expand:** only one runs at a time; a second trigger reports
  `already_running` instead of racing.
* **Data files** live in `data/` (`top100_digest.json`, `watched.json`,
  `blacklist.json`, `sources.json`). Corrupt files are quarantined next to
  the original with a `.corrupt-<timestamp>` suffix — check there before
  assuming data loss.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Sync aborts, "blocked" in logs | Instagram challenged the session | Re-authenticate in Chrome, re-run |
| Cookie-expiry email | `sessionid` rotated | Same as above |
| Deploy refused, thin digest | Too few playable reels | Check `logs/`, re-run; quota gate in R2 dashboard |
| Videos stall offline on iOS | Stale Service Worker | Hard-refresh once to pick up new `sw.js` |
| Playwright won't launch in sandbox | Kernel seccomp (SIGTRAP) | Run browser tests on host hardware |

---

## Testing

```bash
# Fast suite: unit + integration + Node-executed SW range tests
.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q

# Full suite including Playwright browser tests (needs a real browser;
# will not launch inside locked-down sandboxes)
.venv/bin/python -m pytest tests/ -q
```

Regression collateral for the hardening pass lives in
`tests/test_{xss_hardening,durability,sw_range,scraper_hardening,parser_resilience,dom_perf}.py`.

---

## License
MIT
