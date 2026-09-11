# Instagram Digest v1.0 — High-Signal Top 300 Reel Viewer & Rolling Retention

## Outcome
Curate a high-signal, finite weekly batch of the Top 300 Instagram reels across followed creators into a clean, mobile-first web viewer deployed to GitHub Pages and streamed via Cloudflare R2 (with an identical zero-bandwidth local desktop dashboard). Replaces Instagram's algorithmic addiction with a structured, high-efficiency media briefing that auto-advances at 1.25x speed, filters by 4 thematic buckets, and eliminates doom-scrolling.

## Scope
- **Current module (v1.0 Core Pipeline):**
  1. **Source Ingestion & 30-Day Auto-Sync:** Extract followed accounts directly from your authenticated Chrome browser session (filtering out private personal accounts) with 30-day background refresh and an on-demand dashboard sync button.
  2. **Fair-Share Viral Ranking:** Rank the weekly Top 300 using a creator-normalized viral velocity algorithm (guarantees ≥1 reel per active creator, caps max 4 per creator to prevent feed takeover).
  3. **R2 Media Sync & 8-Day Rolling Purge:** Download Top 300 MP4s to local disk and upload to Cloudflare R2 free-tier object storage. Enforce a strict <5 GB pre-flight safety guard and automatically purge reels and site archives older than 8 days from both R2 and local storage.
  4. **Variant 1A Instagram-Styled Viewer (PWA & Desktop):**
     - **Clean Video Canvas:** Full-bleed 9:16 vertical video with zero intrusive rectangular boxes or floating plastic buttons obstructing the subject.
     - **Natural Text Scrim:** Subtle bottom-left soft dark gradient containing creator handle (`@handle • #01`) and clean 1-line expandable caption.
     - **Top App Header:** Instagram script logo, jump-to-reel, offline download, and week archive badge (plus +100/refresh/channels where available). Default 1.25x speed; no header speed pill.
     - **5 Story Category Circles:** Gradient-ringed filter bubbles (`🔥 All Top 300`, `💻 Tech & AI`, `🏋️ Health & Wellness`, `🧠 Deep Explainer`, `🎨 Creative & Culture`).
     - **Hands-Free Playback:** Default **1.25x speed** with **0.5-second auto-advance** to the next unread reel upon completion.
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
| `ranker.py` | Normalizes views by creator baseline, applies fair-share caps, outputs Top 300. | Selection intelligence; feeds Top 300 list. |
| `storage_r2.py` | S3-compatible client for R2 upload, <5 GB quota check, and 8-day purge. | Media delivery engine with local fallback. |
| `site_builder.py` | Compiles `templates/viewer.html` into static `index.html` and deploys to `gh-pages`. | Static presentation and deployment runner. |
| `templates/viewer.html` | Variant 1A clean Instagram-styled PWA with story bucket circles, 1.25x speed, 0.5s auto-advance, read tracking. | UI presentation layer. |
| `local_server.py` | Lightweight local HTTP server supporting local disk video streaming and on-demand sync. | Local dashboard backend. |
| `install_launcher.sh` | Installs `InstagramDigest.desktop` to Desktop and GNOME application dock. | Ubuntu desktop integration. |
| `main.py` | CLI orchestrator: `--sync`, `--serve`, `--sync-following`, `--build-only`, `--deploy`. | Main entrypoint. |

## Implementation
1. **Scraping, Following Sync & Ranking Core (`extractor.py`, `ranker.py`, `sources.json`):**
   Implement `yt-dlp` session cookie extraction, Instagram following auto-sync with private-account filtering, and fair-share viral multiplier ranking.
2. **Storage & Purge Engine (`storage_r2.py`, `config.py`):**
   Implement S3-compatible client targeting Cloudflare R2 for uploading MP4s, quota safety check (<5 GB), and 8-day rolling retention purge across remote R2 and local directories.
3. **Variant 1A Clean Viewer, Local Server & Launcher (`site_builder.py`, `templates/viewer.html`, `local_server.py`, `install_launcher.sh`):**
   Build the clean responsive PWA template (story buckets, 1.25x default speed, 0.5s auto-advance, watched state, 2-reel preloading), local server, desktop launcher, and GitHub Pages deployer.

## Proof checks
1. `python main.py --dry-run` — verifies profile extraction, session cookie access, and fair-share ranking without downloading full videos or calling remote cloud APIs.
2. `python main.py --build-only` — compiles `site/index.html` locally using mock/cached data and verifies valid HTML5 `<video>`, 1.25x speed controls, story buckets, and 0.5s auto-advance.
3. `pytest tests/` — verifies viral multiplier math, R2 8-day retention purge filtering, and static site template rendering.

## Run and limitations
- **Run:** `python main.py` (full sync, upload, and deploy), `python main.py --serve` (local dashboard), or double-click Desktop launcher.
- **Limitations:** Requires logging into Instagram on Google Chrome for automatic session cookie detection.

## Viewer gesture revamp (Final — accepted 2026-09-11)
Proposed 2026-09-11, pending interview. Code refs: `templates/partials/feed.html` (bottom-scrim, 2x/share buttons), `templates/partials/player.js` (double-tap zones, `toggleReelSpeed`, `toggleUnifiedFullscreen`), `templates/partials/styles.css` (immersive-mode hides top-chrome only).
- [SETTLED Q1] P1: hide bottom-scrim (creator, caption, 2x, share) in immersive (user ask 2026-09-11; superseded by Q6a: immersive is auto-on-play, so scrim hides on every play).
- [SETTLED Q2] P2: remove per-reel 2x button (`toggleReelSpeed` trigger) in favor of gesture 2x (user ask 2026-09-11).
- [SETTLED Q3] P3: drop double-tap ±10s skip + ripples (user ask 2026-09-11; per Q5, center double-tap + scrub survive).
- [SETTLED Q4a] P4 latch mode: LATCHED per-reel (interview 2026-09-11, answer A). Hold enters 2x, stays for the reel, right-tap exits, reel change resets to default.
- [SETTLED Q4b] P4 zone + threshold: rightmost 35% (x > 65% width) + 500ms hold (interview 2026-09-11, answer A). Center strip keeps fullscreen/pause; 500ms clears the 280ms tap and 380ms double-tap windows.
- [SETTLED Q4c] P4 conflict rules (from A answers 2026-09-11): finger-down in right zone starts 500ms hold timer; movement >10px cancels it (scrub wins, unchanged global scrub incl. right zone); successful hold suppresses the 280ms tap-pause; single right-zone tap while latched exits 2x and consumes that tap (no pause); all other taps unchanged; reel change resets to default speed.
- [SETTLED Q5] Post-±10s reto (interview 2026-09-11, answer A): center double-tap fullscreen kept; left/right skip logic + ripple HUD removed; horizontal-drag scrub (and its seek HUD) kept everywhere.
- [SETTLED Q6a] Single auto-immersive mode (user intent 2026-09-11): player auto-enters immersive (chrome + bottom-scrim hidden) as soon as video starts playing — no tap-for-play then double-tap-for-immersive. Prerequisite: current two states (`immersive-mode` vs `playback-active` in `player.js`/`styles.css`) must be merged/fixed first so play implies immersive.
- [SETTLED Q6b] Auto-immersive mechanism: CSS chrome-hiding state only on every play (interview 2026-09-11, answer A; PWA context: hiding top + bottom chrome already feels fullscreen). No programmatic Fullscreen API on advance; native fullscreen stays manual via center double-tap.
- [SETTLED Q6c] Immersive reveal path: pause reveals (interview 2026-09-11, answer A). Single tap pauses and restores top chrome + bottom-scrim (share/caption one pause away); resume re-hides; scroll advance auto-plays next reel straight into immersive.
- [ACCEPTED FOLLOW-UP F1] Fast-scroll autoplay race fix (user authorized 2026-09-11: "make the necessary fixes"). Root causes in `templates/partials/player.js` `playCardVideo()`: (a) neighbor reset set `currentTime = 0` at HAVE_NOTHING, throwing InvalidStateError and aborting the new card's triggerPlay; (b) any play() rejection incl. AbortError hit the muted fallback, replaying stale cards. Fix: `readyState > 0` guard, shared `myGen`/`navGen` generation token dropping stale attempts, AbortError bypass. Covered by `tests/test_playback_race.py`. Implemented 2026-09-11.
- Scope contract ACCEPTED by owner (chat reply "Accept", 2026-09-11): artifact boundary, done-means (a)–(e), one-stage approval, no scope widening via execution words. Section status: Final.
- Implemented 2026-09-11 ("implement the revamp"): auto-immersive via `syncImmersive()` (`player.js`) + scrim-hide rules (`styles.css`); 2x button/±10s/ripples removed (`feed.html`, `viewer.html`, `player.js`, `styles.css`); hold-2x latch (`HOLD_ZONE`/`HOLD_MS`/`engageBoost`/`exitBoost`); center double-tap native-only; scrub kept. Tests: `tests/test_immersive_revamp.py` + updated Playwright UAT/e2e (browser suites authored for real browsers; not runnable in this sandbox — see session notes).
- Review fixes 2026-09-11 ("fix all" = top-3 priorities): silent-death alerts (`notifier.send_failure_alert_email` + `--failure-alert` CLI; `main._alert_sync_abort` on all four exit-2 sites incl. deploy-refusal; `run_weekly.sh` mails on non-2 failures, exit code preserved); credential perms (`cookie_exporter._secure_write_text` 0600 on all three cookie files — root copy kept, `extractor.py` needs it for yt-dlp; `config.check_env_file_permissions` warns on loose `.env`; live files chmodded 600, all git-ignored, never committed); cookie-death alert unified via the blocked-session abort mail. Tests: `tests/test_failure_alerts.py` (9 tests). Held for owner input: unauthenticated local APIs token, exporter on-demand only, in-memory job persistence, share-page noindex, docs value drift (retention/speed/count).
- Audit fixes 2026-09-11 ("fix all", 11 findings, all in `templates/partials/player.js`): P0-1 filter-hide `readyState` guard; P0-2 scrub-commit `readyState` guard; P0-3 single-flight `readyWaiter` token + load-skip while `networkState === 2`; P0-4 same-card early return without `navGen` bump; P0-5 `navGen++` in `prepareCardVideoPaused`; P1-6 gen-guarded pill `play()`; P1-7 unconditional error→`skipDeadCard` with pre-mark index + current-only navigation; P1-8 card-scoped `hideMutePill`; P1-9 `AbortController` bootstrap disarm; P1-10 hold-release flag left for feed consumer; P2-11 single-flight toast timer. Tests: `tests/test_audit_races.py` (11 tests). Browser-dependent suites remain unrunnable here (Chromium SIGTRAPs at launch, pre-existing).
