# Requirements Specification — Option 2: Cookie-Free Instagram Pipeline

## 1. Project Overview & Target User Anchor
- **Project Name:** Instagram Digest — Cookie-Free Anonymous Pipeline Toggle
- **Target User & Scale:** Single-user personal CLI / weekly cron automation. Strictly avoid enterprise overhead, multi-tenant databases, microservices, or complex auth servers.
- **Primary Objective:** Provide a seamless, robust anonymous (cookie-free) pipeline mode for the weekly Instagram digest that harvests and enriches public creator reels without requiring Instagram account credentials, session cookies, or triggering account challenge checkpoints (`update_risky_contactpoint`).
- **Core Value Proposition:** When Instagram flags or challenge-gates the authenticated account, the pipeline can run 100% anonymously via public browser scraping and direct progressive CDN stream resolution, while retaining the ability to toggle back to cookie-based extraction whenever desired.

---

## 2. Functional Requirements (Scope Matrix)
- **Configuration & CLI Control:**
  - Introduce `COOKIE_FREE_MODE: bool` in `config.py` (read from environment variable `COOKIE_FREE_MODE`, defaulting to `False`).
  - Introduce CLI flags in `main.py`:
    - `--cookie-free`: Explicitly force cookie-free anonymous operation for this invocation.
    - `--use-cookies`: Explicitly force authenticated cookie-based operation for this invocation.
    - Resolution precedence: CLI flag > `COOKIE_FREE_MODE` env > default `False`. Startup logs resolved mode and source.
- **Gating All Identity Touchpoints:**
  - In cookie-free mode, ensure 100% anonymous execution across all paths:
    1. Do not read or inject `data/cookies.json` or `cookies.txt`.
    2. `get_cookie_args()` in `downloader.py` returns `[]` (explicitly preventing fallback to `--cookies-from-browser chrome`).
    3. Skip `fetch_media_info_batch` API (which requires an authenticated `sessionid`).
    4. Refuse `--sync-following` with a clear user-facing error message (following requires an authenticated user account).
    5. Bypass session healthchecks, login validation probes, follow-cooldown checks, and `cookie_exporter.py` invocations.
    6. Ensure all yt-dlp metadata and fallback downloads run without cookie arguments.
    7. Skip Tier 3 personal feed top-up (`extract_external_reels_from_feed`).
- **Failure Handling & Alert Suppression:**
  - In cookie-free mode, empty creator grids or anonymous rate-limits are handled softly: record per-creator skip, bounded single-retry, and proceed through creator roster without multi-hour sleeps.
  - Never trip the account-safety circuit breaker `_GATE` on anonymous errors (there is no account at risk).
  - Suppress cookie-alert emails (`notifier.send_cookie_alert_email()`) and attention popups (`local_server.raise_cookie_attention()`) in cookie-free mode.
- **Checkpoint & Resume Contract:**
  - Stamp `cookie_free: bool` into all sync checkpoints (`data/sync_progress_<week>.json`) and candidate caches.
  - On `--resume`, if the banked checkpoint mode does not match the active invocation mode, log an explicit warning and refuse to mix unless `--force` is provided or `--resume` explicitly matches the banked mode.
- **Shortfall & Metadata Contract:**
  - Since Tier 3 feed top-up is skipped, increase anonymous discovery depth (more scrolls per public creator reels grid) to harvest sufficient candidates.
  - Maintain `MIN_DEPLOY_ITEMS = 150` as a strict floor. If candidate yield falls below 150, stage progress as `shortfall_paused` without mutating Cloudflare R2 or overwriting the weekly digest.
  - Metadata degradation contract: if like counts are hidden on anonymous web pages, ranker falls back to grid view counts and marks `metrics_estimated=True`; reels lacking timestamps are dropped under standard cutoff rules to preserve the finite weekly briefing promise.

---

## 3. Interview Record & Decision Log
| # | Functional Question | Recommended Approach | Evaluated Alternatives | User Decision / Rationale |
|---|---------------------|----------------------|------------------------|---------------------------|
| 1 | Target Persona & Scale | Single-user personal desktop CLI | Multi-tenant SaaS | Confirmed: Personal single-user |
| 2 | Control Mechanism | Config toggle `COOKIE_FREE_MODE` + CLI flags `--cookie-free` / `--use-cookies` | Separate codebase / script | Confirmed: Single codebase controlled by flag |
| 3 | Anonymous Failure Handling | Soft per-creator skips on empty/blocked grids; suppress account circuit breaker, cookie emails, and popups. Keep 150 hard floor. | Hard abort on first failure; or multi-hour sleeps | Confirmed: Soft skips, suppress alerts, keep 150 floor |
| 4 | Resume & Checkpoint Mode Matching | Stamp `cookie_free: bool` into checkpoints; refuse mixing unless `--force` | Transparent mixing across modes | Confirmed: Stamp and refuse mode mixing |
| 5 | Tier 3 Feed Top-up & Shortfall | Deepen per-creator reel scrolling in cookie-free mode; maintain `MIN_DEPLOY_ITEMS=150` floor. Below 150, pause at `shortfall_paused` without mutating R2. | Lower `MIN_DEPLOY_ITEMS` or scrape explore feed | Confirmed: Deepen creator depth, keep 150 hard floor, pause on shortfall |

---

## 4. Frontier Model Probing Insights & Scope Gaps
1. **Explicit Identification of Cookie Leak Paths:** Addressed in §2 (`data/cookies.json`, `cookies.txt`, `--cookies-from-browser`, media info API, following sync).
2. **Alert Suppression:** Formally scoped in §2 to suppress cookie alert emails and popups in cookie-free mode.
3. **Metadata Contract:** Acknowledged that anonymous profiles may omit exact likes, relying on view counts and progressive `.fbcdn.net` streams.
4. **Checkpoint Stamp:** Implemented to prevent silent mode mixing between cookie and cookie-free runs.

---

## 5. UI Specification
- Not applicable (UI remains the existing static viewer deployed to GitHub Pages and local viewer; no visual layout modifications required).
