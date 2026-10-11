<p align="center">
  <img src="assets/icon-192.png" width="96" height="96" alt="Instagram Digest Logo" />
</p>

<h1 align="center">📱 The Anti-Doomscroll Briefing</h1>

<p align="center">
  <strong>An intentional, finite, ad-free weekly digest of Instagram Reels on native iOS & web.</strong><br>
  Replaces algorithmic doom-scrolling with a finite, curated media briefing. Stream high-bitrate video via Cloudflare R2 and sync permanent bookmarks to Telegram — <strong>100% free forever ($0.00/mo)</strong>.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="#-native-ios-app-installation-primary"><img src="https://img.shields.io/badge/iOS_App-Swift_%26_SwiftUI-orange.svg" alt="Native iOS App" /></a>
  <a href="#-web-pwa-backup-no-install"><img src="https://img.shields.io/badge/Web_PWA-Mobile_%26_Desktop-purple.svg" alt="Web PWA" /></a>
  <a href="#-the-000-forever-zero-cost-architecture"><img src="https://img.shields.io/badge/Hosting_Cost-$0.00_Forever-success.svg" alt="Zero Cost" /></a>
  <a href="#5-permanent-telegram-cloud-bookmarks"><img src="https://img.shields.io/badge/Bookmarks-Telegram_Bot_API-blue.svg" alt="Telegram Sync" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-blue.svg" alt="Python 3.10+" /></a>
</p>

---

## 📸 Visual Tour

<p align="center">
  <img src="assets/screens/feed_view.png" width="23%" alt="Full-Screen Vertical Feed" />
  <img src="assets/screens/grid_view.png" width="23%" alt="3-Column Visual Grid" />
  <img src="assets/screens/manifesto_view.png" width="23%" alt="10 Traps Manifesto" />
  <img src="assets/screens/settings_view.png" width="23%" alt="Anti-Doomscroll Controls" />
</p>

<p align="center">
  <em>(Left to Right: Full-Screen Vertical Feed · 3-Column Visual Grid View · The 10 Traps Manifesto · Anti-Doomscroll Wellbeing Controls)</em>
</p>

---

## 💡 Why This Exists — Neutralizing The 10 Traps of Phone Addiction

Mainstream social media apps are behavioral casinos engineered for **infinite retention** — maximizing time-on-screen rather than user wellbeing.

This project was built as an open-source, engineering-first antidote to the 10 psychological traps exposed in the viral investigative documentary by **Satvic Movement**:  
🎬 **Watch the investigation:** [*"The Brain Behind Your Phone Addiction. We Caught Him!"*](https://www.youtube.com/watch?v=FmLJbYBu094)

Here is how **The Anti-Doomscroll Briefing** directly dismantles every single trap in code:

| # | Trap from the Video | The Psychological Trap | How This Repo Solves It | Status |
|---|---|---|---|:---:|
| 1 | **Infinite Scroll** | Eliminates natural stopping cues so the brain never decides to exit. | **Hard Weekly Cap:** Finite batch of customizable reels (ex: 150), followed by an explicit celebratory *"You're All Caught Up! 🎉"* milestone. No endless feed. | **Solved** |
| 2 | **The Red Colour Trap** | Red notification dots exploit primal threat/urgency reflexes to compel clicks. | **Quiet-Luxury Aesthetics:** Zero red notification badges or alarm dots. Frosted OLED slate tones and a 1-tap **Hardware Grayscale Mode**. | **Solved** |
| 3 | **Gamified Social Obligation** | Streaks, interaction scores, and follow ratios exploit fear of social rejection. | **Zero Social Graph:** Completely severed social graph. No follower counts, streaks, or social debt loops. | **Solved** |
| 4 | **Variable Reward (Casino Logic)** | Pull-to-refresh roulette; unpredictability triggers dopamine spikes like a slot machine. | **Deterministic Static Digest:** Predictable curated score ranking or pure chronological sort. Zero pull-to-refresh roulette. | **Solved** |
| 5 | **Filtered Reality & Envy** | Hyper-curated algorithmic feeds optimize for envy, drama, and artificial lifestyle comparisons. | **Channel Archetypes:** Whitelist ingestion balancing intentional categories (`Educational`, `Craft`, `Lifestyle`, `Vlog`) with strict fair-share creator caps. | **Solved** |
| 6 | **Social Validation Metrics** | Like counts, view tallies, and comment threads hijack the brain's validation circuitry. | **Metrics Stripped:** Like counts, views, and comment threads are completely removed. Includes a **Neutrality Mode** to hide even rank badges (`#01`, `#02`). | **Solved** |
| 7 | **Artificial Urgency (FOMO)** | 24-hour disappearing stories and live pings force compulsive daily checking. | **Asynchronous Weekly Batch:** Published once a week on your schedule (ex: Saturdays 18:00); reels remain permanently accessible offline with zero disappearing FOMO feeds. | **Solved** |
| 8 | **Hypnotic Trance State** | Frictionless 0-effort autoplay lulls the prefrontal cortex into a passive zombie state. | **Active Playback Friction:** Choose between Auto mode (0.5s pause) and Manual advance (swiping required to proceed), plus optional **3-second inter-reel breathers**. | **Solved** |
| 9 | **Sensory & Dopamine Overload** | Hyper-saturated colors and aggressive jump cuts trigger continuous neural stimulation. | **1-Tap Grayscale Detox:** Hardware-accelerated monochrome filter strips all saturation from video and interface with zero latency. | **Solved** |
| 10 | **The Buzz (Frictionless Re-entry)** | Push notifications and vibration pings pull you back into the loop multiple times per day. | **Pull-Only Architecture & Hard PIN Lockout:** Zero push notifications. Configurable daily watch-time quotas (10/20/30/45m) backed by a hard **4-digit PIN lockout**. | **Solved** |

---

## ✨ 5 Core Pillars

### 1. ★ Native iOS App Experience (Primary Tier)
* **120Hz ProMotion Fluidity:** Pure Swift & SwiftUI interface delivering butter-smooth vertical paging with native iOS gesture physics.
* **0ms Instant Video Playback:** Multi-slot `AVPlayer` background pre-buffering completely eliminates black screens, spinners, and thumbnail loading delay.
* **100% Offline Flight Mode:** One-tap download caches the entire weekly digest to your iPhone storage using SwiftData for uninterrupted offline travel.
* **Intuitive Media Gestures:**
  - **Tap Screen:** Instant play/pause toggle.
  - **Press-and-Hold Right Edge:** Latches `2.0x` fast-forward speed with a visual indicator pill. Tap again to return to 1.0x.
  - **Horizontal Scrubbing HUD:** Drag horizontally for real-time seek scrubbing with timestamp preview.

### 2. Anti-Doomscroll Psychology & Active Friction
* **Auto vs. Manual Advance:** Choose between `Auto` (effortless playback with 0.5s pause) and `Manual` (playback pauses at reel completion until you deliberately swipe).
* **3-Second Breathing Space:** Optional 3-second countdown toast between reels that breaks unconscious hypnotic trance states.
* **Hardware-Accelerated Grayscale:** Instantly strips hyper-saturated colors across the entire app with zero performance penalty.
* **Neutrality Mode:** Hides `#01`, `#02` rank numbers to remove comparative judgment and let you appreciate content for its intrinsic value.

### 3. Session Time-Boxing & Hard Lockout
* **Daily Watch Quotas:** Select a healthy daily media limit: **10 min**, **20 min**, **30 min**, or **45 min**.
* **Gentle Pause vs. Hard Lockout:**
  - `Gentle Pause`: Displays a calm mindful reminder toast with an option to take a break or snooze.
  - `Hard Lockout`: Full-screen blur that requires entering a private 4-digit PIN to unlock, preventing impulsive over-consumption.

### 4. Channels You Actually Value & Zero-Cookie Import
* **Additive Creator Onboarding (`--import-following`):** Log in once via an interactive, headed browser window to import creators you follow. Pre-existing channels are safely preserved — no wiped lists, zero stored passwords, zero saved cookies.
* **Intentional Archetypes:** Categorize creators into `Educational`, `Craft`, `Lifestyle`, and `Vlog`. The fair-share ranking engine ensures no single creator dominates your feed.
* **High-Density 3-Column Explore Grid:** Tap `⊞` (or press `V` on desktop) to browse all curated reels at once with instant search by creator handle or caption keywords.

### 5. Permanent Telegram Cloud Bookmarks
* **Permanent Cloud Saving:** Tap `🔖 Save` on any reel to bookmark it forever.
* **Automatic Telegram Bot Forwarding:** Saved reels are forwarded directly to your private Telegram chat, serving as an unlimited, free, permanent cloud media archive that doesn't consume your phone storage.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Ingestion ["1. Scheduled Ingestion & Fair-Share Ranking"]
        A[Cookie-Free Public Ingestion] -->|Extract Top Reels| B[extractor.py]
        A2[Headed Following Importer] -->|Audit & Add Creators| B
        B -->|Archetype Tagging & Fair-Share Quota| C[ranker.py]
        C -->|Deduplication & Storage Check| D[storage_r2.py]
    end

    subgraph Storage ["2. Zero-Egress Cloudflare Storage"]
        D -->|Upload Curated MP4s & Posters| E[(Cloudflare R2 Bucket)]
    end

    subgraph Hosting ["3. Static Manifest & Site Compilation"]
        D -->|Generate manifest.json & Static Web Shell| F[site_builder.py]
        F -->|Deploy to gh-pages| G[GitHub Pages CDN]
    end

    subgraph Clients ["4. Dual-Client Architecture"]
        subgraph PrimaryClient ["★ Primary: Native iOS App (Swift & SwiftUI)"]
            H1[iOS Native App] -->|Fetch manifest.json| G
            H1 -->|120Hz ProMotion Feed & 3-Slot AVPlayer| E
            H1 -->|SwiftData Offline Media Cache| I1[Local iPhone Storage]
            H1 -->|Anti-Doomscroll Controls: Grayscale, Breathers, PIN Lockout| H1
        end

        subgraph FallbackClient ["Web: Progressive Web App (PWA)"]
            H2[PWA / Mobile Safari / Desktop Chrome] -->|Load Static Web Shell| G
            H2 -->|HTTP 206 Byte-Range Video Stream| E
            H2 -->|Offline Service Worker Cache| I2[Browser Cache Storage]
        end
    end

    subgraph CloudSync ["5. Optional Telegram Cloud Bookmarks"]
        H1 -.->|Save Bookmark| J[Cloudflare Worker API]
        H2 -.->|Save Bookmark| J
        J -->|Persist SQL Metadata| K[(Cloudflare D1 Database)]
        J -->|Forward Video URL or Stream R2 Multipart| L[Telegram Bot API]
        L -->|Private Message| M[Your Telegram Saved Feed]
    end
```

---

## 📲 App Installation

### ★ Native iOS App Installation (Primary)

The native iOS app is compiled and packaged automatically on every release via GitHub Actions.

1. **Download the latest IPA:**
   - Go to your repository's [Actions Tab](../../actions) -> **Build InstagramDigest iOS App**.
   - Download the `InstagramDigest-Unsigned-IPA` artifact (or grab it from `dist/ipa/InstagramDigest-latest.ipa`).
2. **Install on your iPhone:**
   - **AltStore / Sideloadly / TrollStore:** Drag and drop `InstagramDigest.ipa` to install and sign with your free Apple ID.
   - **Xcode (Mac):** Open `InstagramDigest.xcodeproj`, select your connected device, and press `Cmd+R`.
3. Launch the app and enjoy 120Hz ProMotion playback, 0ms switching, and full offline caching!

---

### 🌐 Web PWA (Backup / No Install)

If you cannot sideload the native app, the Progressive Web App (PWA) gives you instant access from any browser:

#### iOS Safari
1. Open your digest URL in Safari: `https://<user>.github.io/Instagram_digest/?pin=1234`
2. Tap **Share** -> **Add to Home Screen**.
3. Tap **Add**. Launching from your home screen opens the briefing in full-screen standalone mode without browser chrome.

#### Android Chrome
1. Open your digest URL in Chrome.
2. Tap the three-dot menu -> **Install app** or **Add to Home Screen**.

---

## 🚀 Setup & Weekly Ingestion Pipeline

### Step 1: Clone Repository & Install Dependencies

```bash
git clone https://github.com/<your-username>/Instagram_digest.git
cd Instagram_digest

# Create virtual environment with Python 3.10+
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies and browser tools
pip install -r requirements.txt
playwright install chromium
```

---

### Step 2: Cloudflare Setup (R2 Video Storage)

1. Log into your [Cloudflare Dashboard](https://dash.cloudflare.com/) and go to **R2**.
2. Click **Create bucket** and name it `instagram-digest`.
3. Under **Settings** -> **Public access**, enable your **R2.dev subdomain** (or connect a custom domain).
4. Create an API Token under **Manage R2 API Tokens** with `Object Read & Write` permissions.

---

### Step 3: Configure Environment (`.env`)

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Fill in your Cloudflare R2 credentials and GitHub Pages URL:
```env
# Cloudflare R2 Video Storage (10 GB Free Tier, $0.00 Egress)
R2_ACCOUNT_ID=<your-cloudflare-account-id>
R2_ACCESS_KEY_ID=<your-r2-access-key-id>
R2_SECRET_ACCESS_KEY=<your-r2-secret-access-key>
R2_BUCKET_NAME=instagram-digest
R2_PUBLIC_DOMAIN=https://<your-bucket-domain>.r2.dev

# GitHub Pages Deployment
GH_PAGES_REPO=https://github.com/<your-username>/Instagram_digest.git
PAGES_BASE_URL=https://<your-username>.github.io/Instagram_digest

# Security PIN for Web PWA Gating
VIEWING_PIN=1234
```

---

### Step 4: Import Channels & Run Ingestion

```bash
# Additive import: import followed channels interactively without saving credentials
python main.py --import-following

# Run weekly sync, score videos, upload to R2, and deploy manifest to GitHub Pages:
python main.py --sync --deploy
```

Once deployment completes, enable GitHub Pages in your repository under **Settings** -> **Pages** (Branch: `gh-pages`, Folder: `/ (root)`).

---

### Step 5: Automated Weekly Scheduling

To update your digest every week automatically, add a cron job on your computer or server (`crontab -e`):
```cron
# Runs weekly on your preferred schedule (ex: Saturdays 18:00)
0 18 * * 6 cd /path/to/Instagram_digest && ./run_weekly.sh >> logs/cron.log 2>&1
```

---

## 💰 The $0.00 Forever Zero-Cost Architecture

Every infrastructure component was selected to fit entirely within permanently free tiers:

| Component | Provider & Service | Free Tier Allowance | Instagram Digest Usage | Monthly Cost |
| :--- | :--- | :--- | :--- | :--- |
| **Manifest & Web Hosting** | **GitHub Pages** | Unlimited public bandwidth, 1 GB storage | ~2 MB static bundle | **$0.00** |
| **Video Streaming** | **Cloudflare R2** | 10 GB storage / month, **$0.00 egress fees** | ~2–4 GB active weekly digest | **$0.00** |
| **Bookmarks API** | **Cloudflare Workers** | 100,000 requests / day | ~50–100 requests / day | **$0.00** |
| **Bookmarks Database** | **Cloudflare D1** | 5M read rows / mo, 100k write rows / mo | < 1,000 rows / mo | **$0.00** |
| **Media Cloud Archive** | **Telegram Bot API** | Unlimited cloud file storage & bandwidth | Unlimited bookmarked video forwarding | **$0.00** |
| **Scheduled Ingestion** | **Local Machine / VPS** | Runs on your existing computer | ~15 min cron once per week | **$0.00** |
| **TOTAL** | | | | **$0.00 / mo** |

> [!TIP]
> Traditional video streaming platforms incur heavy bandwidth egress fees ($0.05–$0.09 per GB). Cloudflare R2's **zero-egress-fee policy** is the critical architectural pillar that allows gigabytes of video streaming at absolute zero cost.

---

## ⌨️ Desktop Keyboard Shortcuts

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `Space` | **Play / Pause** | Toggles video playback |
| `ArrowDown` / `J` | **Next Reel** | Advances to next reel in active category |
| `ArrowUp` / `K` | **Previous Reel** | Returns to previous reel |
| `V` | **Toggle Grid View** | Opens / closes 3-column explore grid |
| `F` | **Fullscreen** | Toggles native browser fullscreen |
| `Escape` | **Close Overlays** | Closes Grid View, Bookmarks, or Modals |

---

## 🛠️ CLI Reference

* **Full Weekly Sync & Deploy:** `python main.py --sync --deploy`
* **Midweek Refresh (New reels only):** `python main.py --ad-hoc --deploy`
* **Expand Digest (+N reels):** `python main.py --expand 50 --deploy`
* **Local Offline Server (Port 8080):** `python main.py --serve --port 8080`
* **Rebuild Static Site (Zero network calls):** `python main.py --build-only`
* **Run Test Suite:** `.venv/bin/pytest tests/ -q`

---

## 🔒 Privacy & Security Posture

* **No Credentials Stored:** Account passwords and authentication cookies are never persisted.
* **Client-Side PIN Gate:** Obfuscated with PBKDF2 (200k iterations) in memory; deters casual scrapers from viewing video feeds or metadata. It is not access control — treat the Pages URL as public.
* **Zero Analytics / Tracking:** No analytics SDKs, ad networks, or invasive trackers. (Google Fonts serves the typeface — the only third-party request.)
* **Private Cloud Forwarding:** Bookmarked videos stream directly to your personal Telegram chat via encrypted bot tokens.

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for more information.
