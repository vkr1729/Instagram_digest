# Hardened Implementation Plan — Option 2: Cookie-Free Instagram Pipeline Toggle

## 1. Overview & Objective
Implement a robust, toggleable cookie-free pipeline mode (`COOKIE_FREE_MODE` config + `--cookie-free` / `--use-cookies` CLI flags). When active, it completely isolates the pipeline from all Instagram cookies and account identity, harvests reels anonymously via public Playwright browser scraping, suppresses account-specific alert popups/emails centrally, guards checkpoints from mode mixing, and safely falls through to deploy when candidate thresholds are met.

---

## 2. Hardened Architecture & Proposed Changes

### Phase 1: Configuration (`config.py`)
- Add `_env_bool(name: str, default: bool) -> bool` helper (accepts `1/true/yes/on`, case-insensitive; returns default on empty or unrecognized values).
- Add `COOKIE_FREE_MODE: bool = _env_bool("COOKIE_FREE_MODE", False)`.
- No global predicate/contextvar state; `COOKIE_FREE_MODE` serves as a clean environment default.

### Phase 2: CLI Resolution & Parameter Contract (`main.py`)
- In `argparse`, add a mutually exclusive group:
  - `--cookie-free`: Explicitly force cookie-free anonymous operation.
  - `--use-cookies`: Explicitly force authenticated cookie-based operation.
- Mode resolution:
  - If `args.cookie_free` is set: `cookie_free = True` (source: `--cookie-free`).
  - Elif `args.use_cookies` is set: `cookie_free = False` (source: `--use-cookies`).
  - Else: `cookie_free = config.COOKIE_FREE_MODE` (source: `config.COOKIE_FREE_MODE`).
  - Log resolved mode at startup: `mode=cookie-free (source: ...)` or `mode=cookie-based (source: ...)`.
- Thread `cookie_free: bool` explicitly into `run_full_sync()`, `_extract_with_backoff()`, etc.

### Phase 3: Identity Isolation & Downloader (`extractor.py`)
- In `get_cookie_args(cookie_free: bool = False) -> list[str]`:
  - When `cookie_free=True`, return `[]` immediately without checking `cookies.txt` or executing `--cookies-from-browser chrome`.
  - Pass `cookie_free=cookie_free` through the yt-dlp metadata fallback (`extractor.py:1375`) and the video download fallback (`extractor.py:1627`).
- In `InstagramSession.__init__(cookie_free: bool = False)`:
  - Set `self.cookie_free = cookie_free`.
  - If `cookie_free=True`, skip `_inject_cookies()` entirely and provide a clean anonymous browser context.
- In `fetch_media_info_batch()`:
  - If `cookie_free=True`, return `{}` immediately without calling the authenticated GraphQL/REST media endpoints.
- Circuit breaker & gate trips:
  - Do not call `trip_gate()` on anonymous-origin errors when `cookie_free=True` (there is no account to protect).

### Phase 4: Orchestration, Checkpoint Integrity & Alert Guards (`main.py`, `notifier.py`, `local_server.py`)
- **Pre-flight Checks (`main.py`):**
  - If `--sync-following` is invoked when `cookie_free=True`, abort with clear message: `Following sync requires an authenticated account. Disable cookie-free mode or omit --sync-following.`
  - If `run_expand` is invoked when `cookie_free=True`, abort with clear message: `Expand feed discovery requires an authenticated account. Disable cookie-free mode or run full sync.`
  - When `cookie_free=True`, bypass session login probes, follow-cooldown checks, and `cookie_exporter.py` subprocess.
- **Anonymous Extraction & Backoff (`main.py`):**
  - In `cookie_free` mode, bypass multi-hour rate-limit sleeps (`RATE_LIMIT_WAITS_MIN = (20, 40, 80)`). Handle 429s/empty grids with a bounded single retry then soft-skip creator.
  - Skip Tier 3 personal feed top-up (`extract_external_reels_from_feed`) explicitly at both `run_full_sync` call sites (initial discovery and shortfall resume).
  - Use `limit_per_creator` depth without the young-account trust warming multiplier (set to 1 when `cookie_free=True`).
- **Checkpoint Stamping & Resume Refusal (`main.py`):**
  - Stamp `"cookie_free": cookie_free` into `sync_progress_<week>.json` and `candidates_cache.json`.
  - On `--resume`: if checkpoint's `cookie_free` does not match active invocation mode, refuse with clear error unless `--force` is provided (fresh start).
- **Central Alert Guards (`notifier.py`, `local_server.py`):**
  - Inside `notifier.send_cookie_alert_email()`: return early `False` when `cookie_free=True` or when global active mode is cookie-free.
  - Inside `local_server.raise_cookie_attention()`: return early `False` when in cookie-free mode.
  - Keeps all 23 existing alert call sites clean and tests pinned.
  - In `local_server.py` `/api/sync-following`, refuse when server is in cookie-free mode.

### Phase 5: Shell Scripts & Cron (`run_weekly.sh`, `resume_pending.sh`, `healthcheck.sh`)
- `run_weekly.sh`: Check `COOKIE_FREE_MODE`. If enabled, skip the step-1 Chrome cookie export subprocess and pass `--cookie-free` to `main.py`.
- `resume_pending.sh`: Inspect banked checkpoint `cookie_free` field or pass active mode flag.
- `healthcheck.sh`: Step 2 (sessionid check) becomes a soft skip when `COOKIE_FREE_MODE=1`.

---

## 3. Automated Verification Plan
1. **Unit & Flag Precedence Tests** (`tests/test_cookie_free_mode.py`):
   - Test `_env_bool` handles `1/true/yes/on`, `0/false/no/off`, and invalid strings safely.
   - Test CLI parser: `--cookie-free` vs `--use-cookies` mutual exclusivity; precedence over `COOKIE_FREE_MODE`.
   - Test `get_cookie_args(cookie_free=True)` returns `[]` and never triggers `--cookies-from-browser`.
   - Test alert suppression in `notifier.py` and `local_server.py` when cookie-free is active.
   - Test checkpoint mismatch refusal on resume unless `--force`.
2. **Regression Test Suite**:
   - Run `pytest` across existing suites (`tests/test_smart_funnel.py`, `tests/test_mobile_pwa_uat.py`, `tests/test_dashboard.py`).
