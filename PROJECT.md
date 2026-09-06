# Instagram Digest v1.0 — High-Signal Top 100 Reel Viewer & Rolling Retention

## Outcome
Curate a high-signal, finite weekly batch of the Top 100 Instagram reels across followed creators into a clean, mobile-first web viewer deployed to GitHub Pages and streamed via Cloudflare R2 (with an identical zero-bandwidth local desktop dashboard). Replaces Instagram's algorithmic addiction with a structured, high-efficiency media briefing that auto-advances at 1.5x speed, filters by 4 thematic buckets, and eliminates doom-scrolling.

## Scope
- **Current module (v1.0 Core Pipeline):**
  1. **Source Ingestion & 30-Day Auto-Sync:** Extract followed accounts directly from your authenticated Chrome browser session (filtering out private personal accounts) with 30-day background refresh and an on-demand dashboard sync button.
  2. **Fair-Share Viral Ranking:** Rank the weekly Top 100 using a creator-normalized viral velocity algorithm (guarantees ≥1 reel per active creator, caps max 4 per creator to prevent feed takeover).
  3. **R2 Media Sync & 14-Day Rolling Purge:** Download Top 100 MP4s to local disk and upload to Cloudflare R2 free-tier object storage. Enforce a strict <5 GB pre-flight safety guard and automatically purge reels and site archives older than 14 days from both R2 and local storage.
  4. **Variant 1A Instagram-Styled Viewer (PWA & Desktop):**
     - **Clean Video Canvas:** Full-bleed 9:16 vertical video with zero intrusive rectangular boxes or floating plastic buttons obstructing the subject.
     - **Natural Text Scrim:** Subtle bottom-left soft dark gradient containing creator handle (`@handle • #01`) and clean 1-line expandable caption.
     - **Top App Header:** Instagram script logo, discreet `⚡ 1.5x` speed pill (cycles 1x, 1.25x, 1.5x, 1.75x, 2x), and week archive badge.
     - **5 Story Category Circles:** Gradient-ringed filter bubbles (`🔥 All Top 100`, `💻 Tech & AI`, `🏋️ Health & Wellness`, `🧠 Deep Explainer`, `🎨 Creative & Culture`).
     - **Hands-Free Playback:** Default **1.5x speed** with **0.5-second auto-advance** to the next unread reel upon completion.
     - **Anti-Doomscroll Watched State:** Watched videos saved in `localStorage` and hidden from the active playback sequence until all videos are completed ("You're all caught up! 🎉").
     - **MRT Snappiness:** 2-reel ahead in-memory DOM preloading (`preload="auto"`).
  5. **Local Dashboard & Desktop Launcher:**
     - `python main.py --serve` runs a zero-bandwidth local server at `http://localhost:8080` streaming from local disk.
     - Desktop launcher (`InstagramDigest.desktop`) installed to `~/Desktop` and Ubuntu application menu for 1-click launch and dock pinning.
  6. **Lean Weekly Notification:** Send an executive notification with Top 5 viral highlights and link to the live viewer.
- **Later modules:**
  - Automated weekly cron scheduling via systemd user timer.
  - Offline PWA service worker caching.
- **Not included:**
  - Heavy LLM/NotebookLM text or audio summaries (reels are visual and short).
  - Committing raw video MP4 files to Git (exceeds GitHub Pages 1 GB limit; streamed from Cloudflare R2).
  - Swift iOS native app (PWA provides 98% native feel with $0 fee and zero maintenance).

## Architecture and why
- **Design:** Single-command Python pipeline orchestrating extraction (`yt-dlp`), ranking, S3-compatible R2 sync (`boto3`), and Jinja2 static site compilation, deployed to GitHub Pages (`gh-pages` branch) and served locally via a lightweight Python server.
- **Why it fits:** 
  - Offloads video streaming to Cloudflare R2's free tier (10 GB storage, $0 egress fees), keeping GitHub Pages ultra-lean (~2 MB).
  - 200 videos (2 weeks retention) occupy only ~1.6 GB (<20% of R2's free tier).
  - TubeLM and Instagram Digest remain completely isolated repositories on GitHub Pages.
- **Tradeoff:** Requires Chrome browser session login for Instagram extraction, but eliminates API paywalls and rate-limit blocks.
- **Rejected alternatives:**
  - *Direct Instagram iframe embeds:* Rejected due to 2 MB tracking scripts per video, broken autoplay, and constant login prompts.
  - *Per-creator 50+ story bubbles:* Rejected in favor of 4 broad category buckets visible simultaneously on the home screen.
  - *Floating box overlay UI:* Rejected in favor of Variant 1A's clean native scrim.

## How it works
`sources.json` (auto-synced from browser session) → `extractor.py` (yt-dlp reels & metadata) → `ranker.py` (fair-share viral scoring) → `storage_r2.py` (upload MP4s to R2 & purge >14d files) → `site_builder.py` (render `index.html` & push to `gh-pages`) → `main.py --serve` (local launcher).

## File map
| Path | What it contains | Why it exists / connects to |
| --- | --- | --- |
| `PROJECT.md` | Single source of architectural truth and design documentation. | Blueprint and maintainer reference. |
| `sources.json` | Curated list of Instagram accounts, handles, categories, and enabled toggles. | Input configuration for tracked creators. |
| `config.py` | Central settings: paths, R2 bucket credentials, retention days, playback defaults. | Configuration hub reading from `.env`. |
| `extractor.py` | Ingests reels via `yt-dlp` using Chrome session cookies; syncs followed accounts. | Ingestion layer; feeds candidates to ranker. |
| `ranker.py` | Normalizes views by creator baseline, applies fair-share caps, outputs Top 100. | Selection intelligence; feeds Top 100 list. |
| `storage_r2.py` | S3-compatible client for R2 upload, <5 GB quota check, and 14-day purge. | Media delivery engine with local fallback. |
| `site_builder.py` | Compiles `templates/viewer.html` into static `index.html` and deploys to `gh-pages`. | Static presentation and deployment runner. |
| `templates/viewer.html` | Variant 1A clean Instagram-styled PWA with story bucket circles, 1.5x speed, 0.5s auto-advance, read tracking. | UI presentation layer. |
| `local_server.py` | Lightweight local HTTP server supporting local disk video streaming and on-demand sync. | Local dashboard backend. |
| `install_launcher.sh` | Installs `InstagramDigest.desktop` to Desktop and GNOME application dock. | Ubuntu desktop integration. |
| `main.py` | CLI orchestrator: `--sync`, `--serve`, `--sync-following`, `--build-only`, `--deploy`. | Main entrypoint. |

## Implementation
1. **Scraping, Following Sync & Ranking Core (`extractor.py`, `ranker.py`, `sources.json`):**
   Implement `yt-dlp` session cookie extraction, Instagram following auto-sync with private-account filtering, and fair-share viral multiplier ranking.
2. **Storage & Purge Engine (`storage_r2.py`, `config.py`):**
   Implement S3-compatible client targeting Cloudflare R2 for uploading MP4s, quota safety check (<5 GB), and 14-day rolling retention purge across remote R2 and local directories.
3. **Variant 1A Clean Viewer, Local Server & Launcher (`site_builder.py`, `templates/viewer.html`, `local_server.py`, `install_launcher.sh`):**
   Build the clean responsive PWA template (story buckets, 1.5x default speed, 0.5s auto-advance, watched state, 2-reel preloading), local server, desktop launcher, and GitHub Pages deployer.

## Proof checks
1. `python main.py --dry-run` — verifies profile extraction, session cookie access, and fair-share ranking without downloading full videos or calling remote cloud APIs.
2. `python main.py --build-only` — compiles `site/index.html` locally using mock/cached data and verifies valid HTML5 `<video>`, 1.5x speed controls, story buckets, and 0.5s auto-advance.
3. `pytest tests/` — verifies viral multiplier math, R2 14-day retention purge filtering, and static site template rendering.

## Run and limitations
- **Run:** `python main.py` (full sync, upload, and deploy), `python main.py --serve` (local dashboard), or double-click Desktop launcher.
- **Limitations:** Requires logging into Instagram on Google Chrome for automatic session cookie detection.
