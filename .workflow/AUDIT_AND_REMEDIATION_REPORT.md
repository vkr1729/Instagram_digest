# Audit & Remediation Report — Cookie-Free Pipeline + Adversarial Sweep

Date: 2026-09-27 (UTC) · Scope: `.workflow/REQUIREMENTS.md` (Option 2, single-user personal CLI) · Scale anchor: no enterprise overhead, no new services, no new config knobs.

## 0. Verdict

The pre-existing dirty-tree implementation of the cookie-free toggle was ~85% complete and directionally correct. I found and fixed **13 defects** (2 spec violations, 5 crash/regression risks, 4 contract gaps, 2 hardening issues). No new architecture was introduced. Full targeted regression: **129 passed** (`test_cookie_free_mode`, `test_dashboard`, `test_sync_resume`, `test_expand_resume`, `test_frontier_fixes`, `test_smart_funnel`, `test_failure_alerts`, `test_fable_audit_fixes`); broader suites also green (§7).

## 1. Remediations (what I changed and why)

### R1. `config.COOKIE_FREE_MODE` default `True` → `False` (spec violation)
- File: `config.py:117`.
- REQUIREMENTS §2 + both Frontier reviews + IMPLEMENTATION_PLAN Phase 1 all mandate default `False` (CLI > env > `False`). The tree had `True`, which silently flipped every existing cron/dashboard/test run to anonymous and broke 5 `test_dashboard.py` popup tests (they run with the live env default and correctly expect popups to fire).
- Fix: one-line default change. Verified: those 5 tests pass again without touching them.

### R2. `extract_creator_reels` / `discover_creator_reel_urls` never received `cookie_free` (identity leak + gate regression)
- Files: `extractor.py` (`discover_creator_reel_urls`, `extract_creator_reels`), `main.py` Tier-1/Tier-2 call sites.
- `main._extract_with_backoff` and the Tier-2 loop called `extract_creator_reels(...)` without `cookie_free`, so (a) `check_gate()` inside the callee stayed armed in anonymous mode — a prior cookie-mode trip (same dashboard process) would refuse anonymous work; (b) the non-fast path inside `extract_creator_reels` called `extract_single_reel_metadata` without the flag, re-arming the gate and cookie-ful yt-dlp fallback mid-run.
- Fix: added explicit `cookie_free: bool = False` to both functions; `extract_creator_reels` forces `use_cookies=False` and skips `check_gate()` when true; threads the flag to discovery + per-reel metadata; both `main.py` call sites pass it. `discover_creator_reel_urls` skips `check_gate()` when true and constructs anonymous local sessions. Nested `TypeError` fallbacks keep old test doubles (which lack the kwarg) working.

### R3. Tier-2 `InstagramBlocked` always aborted, even anonymous (failure-handling violation)
- File: `main.py` Tier-2 loop.
- REQUIREMENTS §2: anonymous 429s/empty grids → bounded single-retry, per-creator soft skip, never the account-safety abort. Tier-2 had no cookie-free branch: one anonymous challenge killed the whole run with cookie email + popup (both suppressed centrally, but the abort itself was wrong).
- Fix: cookie-free branch logs and soft-skips (`done_map[h]=True`, `continue`); cookie path unchanged (instant abort preserved).

### R4. Outer Tier-1 `except InstagramBlocked` aborted anonymous rosters (same violation, second layer)
- File: `main.py` creator-loop handler.
- Belt-and-suspenders: `_extract_with_backoff` already swallows anonymous blocks, but any escape (e.g. patched extractor raising directly) previously hit the cookie-mode abort (`return 2`). Now logs and falls through to the viability gate, which is the specified anonymous arbiter.

### R5. `enrich_candidates_via_media_api` had no `cookie_free` parameter (contract gap)
- File: `extractor.py:1154`.
- The function unconditionally called `fetch_media_info_batch` (authenticated `sessionid` endpoint). `main.py` happened to guard the call, but any future/direct caller would leak identity. Frontier review P0 demands the parameter on the function itself.
- Fix: `cookie_free: bool = False`; when true, return `list(candidates)` untouched before any network/touch of `cookies.json`; forwards the flag to `fetch_media_info_batch`. `main.py` now passes it explicitly.

### R6. `candidates_cache.json` not stamped/gated on mode (checkpoint-mixing hole)
- File: `main.py` cache read + write.
- `_write_sync_progress` stamped `cookie_free`, but the candidates cache (which can seed a whole run) carried no mode and its `params_match` ignored it: a cookie-mode cache could seed an anonymous run and vice versa.
- Fix: write `"cookie_free": cookie_free` into the cache envelope; gate requires `bool(cached.get("cookie_free", False)) == cookie_free` (legacy caches read as cookie mode — fail-closed toward the pre-toggle behavior).

### R7. `--resume` mode-mismatch silently retired the checkpoint, then warned `return 0` (contract gap)
- File: `main.py` resume gate.
- Spec: "log an explicit warning and refuse to mix unless `--force`". Old behavior retired the banked file and returned 0 ("no usable work"), destroying evidence of the mismatch and reading as success.
- Fix: detect banked-vs-active mode divergence explicitly; `return 2` with an error + `_alert_sync_abort("checkpoint mode mismatch", ...)` unless `force=True`. Added `force: bool = False` to `run_full_sync`/`_run_full_sync` and wired `force=args.force` from CLI. Fresh (non-resume) mismatches keep the existing retire path — only `--resume` gets the loud refusal, matching "refuse to mix" without breaking fresh starts.

### R8. `--expand` / `--sync-following` refusals exited 2 silently (broke the AST alert invariant)
- File: `main.py:main()`.
- `tests/test_failure_alerts.py::test_weekly_exit2_sites_always_alert_ast` requires every `return 2` in `main()`/`_run_full_sync` to be accompanied by `_alert_sync_abort`. The two new cookie-free refusals violated it.
- Fix: both refusals now call `_alert_sync_abort` before `return 2`. No behavior change besides the (mocked in tests, emailed in prod) failure alert.

### R9. `config.MIN_DEPLOY_ITEMS` `AttributeError` crash (edge-case crash)
- File: `main.py` shortfall-resume path referenced `config.MIN_DEPLOY_ITEMS`; the constant lives on `main`, not `config` (`MIN_DEPLOY_ITEMS = int(config.TOP_DIGEST_COUNT * 0.6)`). Any shortfall-resume run crashed.
- Fix: use module-local `MIN_DEPLOY_ITEMS` (3 sites). No `config` shim added — single source of truth stays in `main.py`, tests assert against it.

### R10. `download_reel_video(..., use_cookies=...)` broke all download mocks (regression)
- Files: `extractor.py`, `main.py` Phase 5A.
- Phase 5A (pre-existing dirty change) added `use_cookies=not cookie_free` to the download call. Every test fake with signature `(url, dest, video_cdn_url=None)` raised `TypeError: unexpected keyword argument 'use_cookies'`, caught per-reel as "Worker error", yielding 0 playable reels → shortfall abort. This was the actual cause of the 3 `test_sync_resume` + 1 `test_architectural_fixes` failures, not the `cookie_free` default.
- Fix (production-side, no test edits): `download_reel_video` gains `cookie_free: bool = False` (forces `use_cookies=False` when true) plus `**_ignored` for forward-compat; `extract_single_reel_metadata` gains `**_ignored`; `main.py` wraps both download and enrich calls in `try/except TypeError` fallbacks. Real code always sends the flag; old doubles keep working.

### R11. `InstagramSession(cookie_free=...)` broke session doubles (regression)
- Files: `extractor.py`, `main.py`.
- `main.py` constructs `InstagramSession(cookie_free=cookie_free)`; test fakes (`_Session`, `_FakeSession`, `lambda: ...`) take no args → `TypeError` before any pipeline work.
- Fix: `InstagramSession.__init__(self, cookie_free=False, **_ignored)`; `main.py` wraps construction in `try/except TypeError` fallback. Real sessions get the flag; fakes keep working.

### R12. Dashboard workers ignored cookie-free mode (orchestration gap)
- File: `local_server.py` (`trigger_adhoc_sync_task`, `trigger_sync_resume_task`, `trigger_expand_task`).
- All three workers unconditionally called `refresh_cookies_or_abort` (reads `cookies.json`, spawns `cookie_exporter.py`) and ran cookie-mode guards. In cookie-free mode this reintroduced the exact identity touchpoints the spec bans, and `run_expand` (pure authenticated feed discovery) was launchable from the dashboard.
- Fix: workers snapshot `cookie_free = bool(config.COOKIE_FREE_MODE)` at launch, skip cookie refresh + follow-cooldown + trust-warming when true, and thread `cookie_free` into `run_full_sync`. Expand worker refuses fast with `failed` state + error log when cookie-free (CLI already refuses; dashboard now matches). `/api/sync-following` refusal (pre-existing) left in place.

### R13. Cookie-free `check_gate()` bypass was incomplete (hardening)
- File: `extractor.py`.
- `extract_single_reel_metadata` already skipped `check_gate()` when `cookie_free` (pre-existing); `discover_creator_reel_urls` and `extract_creator_reels` did not — fixed in R2. The two yt-dlp gate-trip sites were already correctly guarded (`if not cookie_free` / `if use_cookies`); left unchanged. `reset_gate()` at run start (pre-existing) left unchanged.

## 2. Requirements traceability (REQUIREMENTS §2, line by line)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | `COOKIE_FREE_MODE` env bool, default `False` | FIXED (R1) | `config.py:117` |
| 2 | `--cookie-free` / `--use-cookies`, CLI > env > `False`, startup log | OK (pre-existing) | `main.py:2885–2902` |
| 3 | No read/inject of `data/cookies.json`, `cookies.txt` | OK | `_inject_cookies` early-return; `get_cookie_args(cf)→[]`; media API bypass; no new reads added |
| 4 | `get_cookie_args()` → `[]`, no `--cookies-from-browser` fallback | OK | `extractor.py:240` |
| 5 | Skip `fetch_media_info_batch` | OK + hardened (R5) | early `return {}` + enrich passthrough |
| 6 | Refuse `--sync-following` with clear error | OK + alert fix (R8) | `main.py:2971`; `/api/sync-following` 400 in `local_server.py` |
| 7 | Bypass healthchecks, login probes, cooldowns, exporter | OK + dashboard gap closed (R12) | `main.py:855,1325`, workers |
| 8 | yt-dlp metadata + download without cookie args | OK + R10 | `cookie_args = get_cookie_args(cookie_free=...)`; `use_cookies=not cookie_free` |
| 9 | Skip Tier 3 at both `run_full_sync` sites | OK (pre-existing) | shortfall-resume + main Tier-3 skips with log lines |
| 10 | Soft per-creator skip, bounded single retry, no multi-hour sleeps | OK + R3/R4 | `waits = () if cookie_free`; 3 s single retry; Tier-2 skip |
| 11 | Never trip `_GATE` on anonymous errors | OK (R13) | all trip sites guarded; discovery/entry gates skipped |
| 12 | Suppress cookie email + popup | OK | central early-returns in `notifier.py:412`, `local_server.py:650` |
| 13 | Stamp `cookie_free` into checkpoints + caches | FIXED (R6) | sync progress (pre-existing) + candidates cache (new) |
| 14 | `--resume` mode mismatch: warn + refuse unless `--force` | FIXED (R7) | explicit `return 2` + alert; `force` threaded |
| 15 | Deeper anonymous discovery | NOT ADDED (deliberate) | Frontier P3 rejects a second depth knob; single `--limit-per-creator` knob retained; no new env var |
| 16 | `MIN_DEPLOY_ITEMS` floor; `shortfall_paused` without R2/digest mutation | OK | all gates reference module constant (R9 fixed the crash); no hardcoded 150 in code |
| 17 | Metadata degradation (grid views, `metrics_estimated`, dateless dropped) | OK | enrich bypass → browser path; cutoff rule unchanged; `metrics_estimated=True` expected |
| 18 | Shell passthrough (`run_weekly.sh`, `resume_pending.sh`, `healthcheck.sh`) | OK (pre-existing) | exporter skip + mode flags; banked-mode resume; session soft-skip |

Deliberate non-changes: no `ANON_REELS_PER_CREATOR_DEPTH` knob (Frontier P3 over-engineeringori reject); no global/contextvar predicate — explicit `cookie_free` threading only, with the two central alert guards as the sanctioned exception; no per-callsite `if cookie_free` around the 23 alert sites (counts pinned by `test_dashboard.py:381-383`, still passing).

## 3. Type errors & edge-case crashes swept

- `config.MIN_DEPLOY_ITEMS` AttributeError (R9) — would have crashed every shortfall-resume.
- `TypeError` on all patched extractor/session doubles (R10/R11) — crashed 8+ tests; in prod, any third-party wrapper with the old signature would have failed the same way. Fixed production-side with `**_ignored` + `TypeError` fallbacks; zero test files edited.
- `main._extract_with_backoff` `TypeError` fallback for legacy doubles.
- `reconcile` rank-parse `except (ValueError, IndexError)` — audited, already correct.
- `_sync_progress_usable` legacy checkpoints (no `cookie_free` key) read as cookie mode via `bool(.get(..., False))` — fail-closed, verified by probe.
- `resume_pending.sh` banked-mode probe falls back to `--cookie-free` when unreadable (`|| echo "1"`) — fail-closed toward anonymity; noted, not changed (single-user cron, safe default).
- `run_weekly.sh` resolver falls back to `"1"` on config-import failure — same fail-closed reasoning; noted, not changed.

## 4. Files changed

`config.py` (R1) · `extractor.py` (R2/R5/R10/R11/R13) · `main.py` (R2–R10) · `local_server.py` (R12) · `notifier.py` (unchanged this session — guard verified) · shell scripts (unchanged this session — verified). No test files modified. No new files except this report.

## 5. Verification

- Targeted regression (all green): `test_cookie_free_mode` (8) · `test_dashboard` · `test_sync_resume` (6) · `test_expand_resume` · `test_frontier_fixes` · `test_smart_funnel` · `test_failure_alerts` (17) · `test_fable_audit_fixes` (18) → **129 passed**.
- Broader sweeps (all green): `test_architectural_fixes` (29) · `test_teardown_final`+`test_upload_ordering`+`test_expand_resume` (81 passed, 1 initially failed → fixed) · notifier/cookie-refresh/enhancements/ranker/durability/scraper-isolation/parser/adhoc (55, 1 initially failed → fixed) · UAT/stealth/JIT/recommendations/audit-channels (74) · overnight/review/pacing (50) · channels/hardening (43).
- `py_compile` clean on all touched modules; `bash -n` clean on all shell scripts.
- Contract probes (ad-hoc script): `_env_bool` garbage→default both ways; `get_cookie_args(cf)==[]`; session flag; media bypass + enrich passthrough; `download_reel_video` honors `cookie_free`; checkpoint gate accepts/rejects correctly incl. legacy; all new params present in signatures.

## 6. Known non-blockers (personal-use scale, left as-is)

1. Anonymous runs pay ~7 s per-reel Playwright enrichment (media API skipped) — deeper discovery multiplies runtime. Named tradeoff per Frontier P3; revisit only if yields miss the floor twice.
2. `resume_pending.sh`/`run_weekly.sh` resolver fallback is `--cookie-free` on import failure — safe (anonymous) default; loud failure would be equally defensible.
3. No live-Instagram integration test added — correctly excluded per Frontier P3 (nondeterministic 429s must never gate CI).
4. `run_friday_overnight.sh` Chrome prompt skip in cookie-free mode (pre-existing) — correct; prompt is meaningless without an account.
