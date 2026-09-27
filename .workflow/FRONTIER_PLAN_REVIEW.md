# Frontier Plan Review — Cookie-Free Implementation Plan

## 0. Scale Anchor (non-negotiable)
- Target stays single-user personal CLI / weekly cron. No contextvars/thread-local
  predicate machinery, no new micro-layers, no separate anonymous extractor module.
- Control surface stays: one bool (`COOKIE_FREE_MODE`, env-read, default `False`) +
  two CLI flags (`--cookie-free` / `--use-cookies`, mutually exclusive, CLI wins,
  neither → config). One log line at startup: `mode=cookie-free (source: --cookie-free)`.
- Verdict on the plan as written: direction is right, but it names a file that does
  not exist, a helper that does not exist, and a state mechanism that will rot.
  Findings below are ordered by blast radius, each with a concrete fix.

## P0. Plan references `downloader.py` — the code lives in `extractor.py`
- **Pitfall:** IMPLEMENTATION_PLAN Phase 2 puts `get_cookie_args()` in
  `downloader.py`. There is no `downloader.py` in the repo. The real function is
  `extractor.get_cookie_args()` (`extractor.py:240`, zero-arg) with two live call
  sites: the yt-dlp metadata fallback (`extractor.py:1375`,
  `cookie_args = get_cookie_args()`) and the download fallback
  (`extractor.py:1627`, `cookie_args = get_cookie_args() if use_cookies else []`).
  Implementing Phase 2 as written edits a nonexistent file and leaves both real
  leak paths open while appearing to work.
- **Fix:** rewrite Phase 2 to target `extractor.py`: add an explicit
  `cookie_free: bool = False` parameter to `get_cookie_args()` (return `[]`
  immediately when true, before the `cookies.txt` / `--cookies-from-browser`
  fallback), and thread it through both call sites. Note the download path
  already has the `use_cookies` pattern — follow it, don't invent a new one.
- **Sequencing:** this is the first code change after config, because every later
  phase assumes cookie-less yt-dlp.

## P0. `is_cookie_free()` with "thread/context state" is the wrong mechanism
- **Pitfall:** Phase 1 proposes `config.is_cookie_free()` that "respects global
  config override or runtime thread/context state." A module-global mutable flag
  read deep inside `extractor.py` is untestable and racy: `local_server.py`
  runs pipelines in worker threads (`:141`, `:256`, `:454` call
  `refresh_cookies_or_abort` then `main_module.run_full_sync`/`run_expand`), and
  a global leaks mode across concurrent dashboard operations and across tests.
  This is the over-engineering center of the plan — contextvars for a
  single-user cron tool.
- **Fix:** no global predicate, no context state. Resolve the mode once in
  `main()` and thread it as an explicit parameter: `run_full_sync(...,
  cookie_free: bool)`, `InstagramSession(cookie_free=...)`,
  `extract_creator_reels(..., cookie_free=...)`,
  `download_reel_video(..., use_cookies=not cookie_free)`. Pure, greppable,
  testable. `config.COOKIE_FREE_MODE` remains a dumb env-read default only.
- **Exception (narrow):** the alert-suppression guard (P2 below) is the one place
  a central check is justified, because there are 10 email + 13 popup sites.

## P0. `_env_bool` does not exist — plan assumes it
- **Pitfall:** Phase 1 writes `COOKIE_FREE_MODE: bool = _env_bool(...)`, but
  `config.py` only defines `_env_int` (`:83`) and `_env_float` (`:96`). Small,
  but it means Phase 1 as written does not run.
- **Fix:** add a `_env_bool` helper (accept `1/true/yes/on`, case-insensitive;
  garbage → default, matching the defensive style of `_env_int`) in the same
  config change that adds `COOKIE_FREE_MODE`.

## P1. Anonymous failures must bypass the backoff sleeps, not just the gate
- **Pitfall:** Phase 3 says "soft-skip creator after single retry" but never
  touches the actual sleep machinery: `_extract_with_backoff`
  (`main.py:1316-1357`) sleeps `RATE_LIMIT_WAITS_MIN = (20, 40, 80)` minutes per
  creator, and the feed path sleeps `FEED_RETRY_WAIT_MIN = 20` min
  (`main.py:1185`, `:1828`). In cookie-free mode anonymous 429s/empty grids are
  routine — reusing this path turns every anonymous rate-limit into a multi-hour
  stall that sleeps through the overnight cron window. Suppressing `_GATE`
  alone does not fix this; the sleeps happen before any gate trip.
- **Fix:** in cookie-free mode, replace `_extract_with_backoff` with a bounded
  single-retry-then-skip (no `time.sleep` beyond normal pacing), and skip the
  `cooling_down` checkpoint writes. Keep `CREATOR_PAUSE`/`ENRICH_PAUSE` pacing
  (IP rate-limits are real when anonymous) but force the trust-warming
  multiplier to 1 — `_apply_trust_warming_pacing` (`main.py:654`) doubles
  pauses for a young *account*, and there is no account here.
- **Keep the viability gates as-is** (`MIN_CANDIDATE_RATIO`,
  `MAX_EMPTY_CREATOR_RATIO`, `main.py:37-38`); they will fail more often
  anonymous, and that failure correctly routes to `shortfall_paused`, not to
  relaxed thresholds.

## P1. `_GATE` and yt-dlp gate-trip paths need explicit cookie-free bypass
- **Pitfall:** `check_gate()` is called at the top of every extraction entry
  point, and `download_reel_video` trips the gate on yt-dlp stderr signals
  (`extractor.py:1653-1654`); the metadata fallback does the same (`:1388-1389`).
  In a dashboard process that previously ran a cookie mode trip, `reset_gate()`
  ordering matters, and anonymous 429s must never latch the account-safety gate
  (there is no account to protect).
- **Fix:** never call `trip_gate` for anonymous-origin errors in cookie-free
  mode (guard the two `_ytdlp_stderr_trips_gate` sites + `check_gate`
  semantics), and always `reset_gate()` at run start regardless of mode. State
  this in the plan — currently Phase 3 mentions the breaker only in passing.

## P1. Tier-3 skip has three call sites, plan names one; `run_expand` is missing
- **Pitfall:** `extract_external_reels_from_feed` is invoked from the shortfall
  resume path (`main.py:1128`), the main Tier-3 path (`main.py:1784`), and
  `run_expand` (`main.py:2498`). Making the extractor function "raise or return
  `[]`" without updating callers leaves the shortfall-resume path computing a
  deficit against a top-up that can never arrive, and leaves `run_expand`
  (pure feed discovery — inherently authenticated) runnable under a flag that
  promises anonymity.
- **Fix:** skip Tier 3 explicitly at both `run_full_sync` call sites with a
  clear log line (don't rely on the callee returning `[]`), and refuse
  `run_expand` in cookie-free mode with the same style of error as
  `--sync-following`. Cover all three in tests.

## P2. Alert suppression: central guard, not 23 scattered `if`s
- **Pitfall:** Phase 4 says "guard `send_cookie_alert_email()` and
  `raise_cookie_attention()`" at the call sites. There are 10 email and 13
  popup sites (`tests/test_dashboard.py:381-383` pins these counts), plus
  `_ensure_valid_session`, `_probe_session_once`, `refresh_cookies_or_abort`,
  and `healthcheck.sh` step 2. Scattered `if not cookie_free` checks rot; the
  next contributor adds site #14 unguarded.
- **Fix:** put the suppression *inside* `notifier.send_cookie_alert_email`
  (early-return `False` when the resolved cookie-free flag is set) and inside
  `local_server.raise_cookie_attention` (early-return `False`), threaded from
  the same explicit parameter — not a global. Keep the call sites untouched so
  the count-pinning test keeps passing, and add one unit test per guard
  asserting no email/popup escapes in cookie-free mode. Separately: bypass
  `_ensure_valid_session`/`_probe_session_once`/follow-cooldown/exporter in
  `main.py`, bypass `refresh_cookies_or_abort` in the three `local_server.py`
  workers, skip `healthcheck.sh` step 2 (sessionid check) when the mode is
  cookie-free, and skip the `run_weekly.sh` step-1 cookie refresh.

## P2. `--sync-following` refusal must cover the dashboard endpoint too
- **Pitfall:** Phase 4 gates only the `main.py --sync-following` CLI. The same
  operation is reachable via `local_server.py:1980`
  (`/api/sync-following` → `extractor.sync_following_accounts(force=True)`),
  which reads `data/cookies.json` and runs the exporter subprocess
  (`extractor.py:414-420`).
- **Fix:** refuse with a clear error at the API handler as well when the
  server is in cookie-free mode (or document that the dashboard always runs
  cookie mode — but then say so explicitly).

## P2. Shell scripts and healthcheck are unmentioned dependency-order risks
- **Pitfall:** `run_weekly.sh` unconditionally refreshes Chrome cookies (step 1)
  and invokes `main.py --sync --deploy` with no mode flags; `resume_pending.sh`
  resumes with `--sync --resume --deploy` inheriting whatever env it has. If
  these ship unchanged, cron silently defeats cookie-free mode (exporter run +
  authenticated resume of an anonymous checkpoint, or vice versa).
- **Fix (same commit as CLI resolution, not a follow-up):** `run_weekly.sh`
  skips the exporter step and passes the mode flag through when cookie-free;
  `resume_pending.sh` passes the banked/resolved mode through; `healthcheck.sh`
  step 2 becomes a soft skip with a logged line in cookie-free mode. These are
  the highest silent-mixing risks in the whole change.

## P3. Checkpoint contract must land atomically with resolution
- **Pitfall:** `_write_sync_progress` (`main.py:989`), `_sync_progress_usable`
  (`main.py:222`), and the `candidates_cache.json` `params_match` gate
  (`main.py:1243-1249`) must all learn `cookie_free` in the same commit as CLI
  resolution. A staggered landing (stamp now, gate later) produces checkpoints
  that claim a mode no reader enforces, i.e. the exact silent-mixing bug the
  stamp exists to prevent.
- **Fix:** one commit: resolve → stamp (`sync_progress_<week>.json` +
  candidates cache) → refuse-on-mismatch unless `--force` (fresh start,
  banked work retired) or `--resume` adopting banked mode. Also define the
  CLI conflict: `--cookie-free` + `--use-cookies` together should be an
  `argparse` mutually-exclusive error, not a silent precedence rule.

## P3. `ANON_REELS_PER_CREATOR_DEPTH` is a new knob for a solved problem
- **Pitfall (over-engineering):** Phase 1 adds `ANON_REELS_PER_CREATOR_DEPTH`
  (`_env_int`, clamp 5–50) to "compensate for skipping Tier 3." But discovery
  depth is already controlled by `--limit-per-creator` / `limit_per_creator`,
  capped at `min(limit_per_creator, 5)` (6 for food) in `main.py:1286`, with
  final caps via `caps_map`/`MAX_PER_CREATOR`. A second depth knob interacts
  with all three and needs its own wiring through `per_source`, Tier 2
  (`max_reels=8`), and ranker caps — new config surface for a single-user tool.
- **Fix:** drop the new env var. In cookie-free mode, raise the effective
  discovery depth by reusing `limit_per_creator` (e.g. treat the cap as the
  existing `--limit-per-creator` default of 15 instead of 5). One knob, zero
  new config, same compensation. Revisit only if anonymous yields miss 150
  twice in a row.
- **Related cost to state honestly:** with the media-API prefilter skipped,
  every shortlist reel pays the ~7s per-reel Playwright fallback for its
  timestamp. Deeper discovery multiplies enrichment time — the plan should name
  this tradeoff instead of implying depth is free.

## P3. Don't hardcode 150 — reference `MIN_DEPLOY_ITEMS`
- **Pitfall:** Phase 4 and the verification plan hardcode "150." The real floor
  is `MIN_DEPLOY_ITEMS = int(config.TOP_DIGEST_COUNT * 0.6)` (`main.py:39`),
  and `TOP_DIGEST_COUNT` is env-tunable. Hardcoding 150 in code or tests breaks
  the moment anyone tunes the digest size.
- **Fix:** always compare against `MIN_DEPLOY_ITEMS` (150 is just its current
  default value); tests assert against the imported constant.

## P3. Verification plan needs hardening
1. The "anonymous Playwright extraction on a known public shortcode" test hits
   live Instagram — mark it integration-only (skip by default, never gate CI),
   or anonymous 429s will red the suite nondeterministically.
2. Add the regression test FRONTIER_REQUIREMENTS_REVIEW Q3 already asks for:
   with the flag on, assert no `cookies.json`/`cookies.txt` read and no
   `--cookies-from-browser` arg construction (patch `pathlib.exists` /
   `subprocess.run` and inspect).
3. Add a checkpoint-mismatch test (bank cookie mode, resume cookie-free
   without `--force` → refusal; with `--force` → fresh start).
4. Add a shell-level assertion that `run_weekly.sh` skips the exporter step in
   cookie-free mode (even a grep test beats nothing).
5. Keep the "full suite passes" gate, but update `test_dashboard.py:381-383`
   counts only if call sites actually change — under the central-guard
   recommendation they should not.

## Suggested Phase Reordering (dependency-safe)
1. `config.py`: `_env_bool` + `COOKIE_FREE_MODE` (dumb default, no predicate).
2. `main.py` CLI: mutually-exclusive `--cookie-free` / `--use-cookies`,
   resolution (CLI > env > `False`), startup log line. Defines the
   `cookie_free` parameter contract every later phase threads.
3. `extractor.py` identity isolation: `get_cookie_args(cookie_free)`,
   `InstagramSession(cookie_free)` skipping `_inject_cookies`,
   `fetch_media_info_batch` bypass, both yt-dlp call sites, no-`trip_gate`
   on anonymous errors.
4. `main.py` orchestration: probe/cooldown/exporter bypass, backoff bypass +
   single-retry skip, Tier-3 skips (both `run_full_sync` sites) +
   `run_expand` refusal, checkpoint stamp + resume gate, central alert guards.
5. `local_server.py` workers + `/api/sync-following` refusal +
   `run_weekly.sh` / `resume_pending.sh` / `healthcheck.sh` passthrough.
   Ships in the same release as (4) — never a follow-up.
6. Tests: unit (precedence, stamp/refusal, guards, no-cookie-construction) +
   integration-marked anonymous scrape + full suite.
