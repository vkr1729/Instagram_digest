# Instagram Digest

An intentional, high-signal weekly digest of Instagram Reels curated into a clean, distraction-free Progressive Web App (PWA).

Replaces endless algorithmic doom-scrolling with a finite, curated media briefing streamed with zero bandwidth cost via Cloudflare R2 and deployed directly to GitHub Pages.

---

## Key Features

### 1. Intentional Curation
* **Configurable Finite Batch:** Curates a finite weekly batch of top reels (configured via `TOP_DIGEST_COUNT`, with an optional on-demand desktop expansion button for high-consumption periods).
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
  - Double-tap left/right edges to skip $\pm 10\text{s}$ with visual ripple feedback.
  - Double-tap center or press `F` for fullscreen immersive mode.
  - Per-reel `2x` speed booster toggle.
  - Full keyboard shortcuts (`J`/`K` navigation, `Space` play/pause, `M` mute/unmute, `G` jump to reel).

### 4. Zero-Egress Cloudflare R2 Media Delivery
* **Zero Egress Fees:** All video streaming is offloaded to Cloudflare R2 object storage ($0 egress bandwidth), keeping GitHub Pages ultra-lean (~2 MB static footprint).
* **Content-Addressed Asset Invariance:** Media files are indexed by immutable entity IDs (`{reel_id}.mp4`), eliminating redundant re-downloads when ranks shift or batches expand.
* **Rolling Retention & Purge:** Automatically prunes media and weekly site archives older than configured retention days to remain comfortably within storage quotas.

### 5. Offline PWA & Flight Mode
* **Permanent Offline Cache Access:** The `#offlineDownloadBtn` in the header allows inspection of local video storage and one-tap downloading of the entire digest.
* **HTTP 206 Partial Content Slicing:** Dedicated Service Worker (`sw.js`) provides zero-copy range request slicing directly from CacheStorage for smooth offline playback on mobile WebKit.
* **Rolling Video Window:** Automatically keeps a rolling window of adjacent videos cached offline during playback.

### 6. Local Management Dashboard
* **Zero-Bandwidth Desktop Server:** Run `python main.py --serve` to launch a local server on port 8080 streaming from local disk.
* **Channel Manager (`/channels`):** Visual web dashboard to audit followed creators, toggle subscriptions, inspect engagement metrics, and trigger ad-hoc refreshes.

---

## System Architecture

The pipeline consists of a modular Python backend and an atomic, single-bundle static frontend:

```
[ Instagram Session ]
        │
        ├──> extractor.py (Playwright + yt-dlp)
        │       ├── Profile Reels from Tracked Creators
        │       └── External Discovery (High signal likes filter, humanized jitter & cooldowns)
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

---

## Quick Start

### Prerequisites
* Python 3.10+
* Playwright (`playwright install chromium`)
* Cloudflare R2 Bucket (or local fallback mode)
* Authenticated Instagram session in Google Chrome

### Installation
```bash
# Clone the repository
git clone https://github.com/<your-username>/Instagram_digest.git
cd Instagram_digest

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium
```

### Configuration (`.env`)
Create a `.env` file in the root directory:
```env
# Cloudflare R2 Configuration
R2_ACCOUNT_ID=<your-account-id>
R2_ACCESS_KEY_ID=<your-access-key-id>
R2_SECRET_ACCESS_KEY=<your-secret-access-key>
R2_BUCKET_NAME=<your-bucket-name>
R2_PUBLIC_DOMAIN=https://<your-r2-subdomain>.workers.dev

# GitHub Deployment
GH_PAGES_REPO=https://github.com/<your-username>/<your-repo>.git
PAGES_BASE_URL=https://<your-username>.github.io/<your-repo>

# Digest Settings
TOP_DIGEST_COUNT=250 # Configurable target size (e.g. 200, 250, 300)
RETENTION_WEEKS=1
```

### Running the Pipeline

* **Run Full Weekly Sync & Deploy:**
  ```bash
  python main.py --sync --deploy
  ```
* **Run Ad-Hoc Midweek Refresh (Fetch reels since last run):**
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
* **Rebuild Static Site from Cache (Zero network calls):**
  ```bash
  python main.py --build-only
  ```

---

## Testing

The project maintains an automated test suite covering unit logic, integration flows, and end-to-end Playwright browser simulations:

```bash
# Run all unit and integration tests
pytest

# Run Playwright mobile and desktop E2E verification
python scratch/test_e2e_verification.py
```

---

## License
MIT
