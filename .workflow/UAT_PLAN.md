# UAT Plan — Cookie-Free Instagram Pipeline (Option 2)

Date: 2026-09-27 (UTC) · Scope: `.workflow/REQUIREMENTS.md` §2 (single-user personal CLI) + `.workflow/AUDIT_AND_REMEDIATION_REPORT.md` R1–R13
Scale anchor: personal CLI / weekly cron. No enterprise overhead, no new services, no live-Instagram-gated CI.

## 1. Independence principles

- Black-box first: verify via CLI exit codes, stdout logs, JSON checkpoints/caches, and absence of side effects (no `cookies.json` read, no R2 mutation, no email/popup). Do not assert on internal function names except where the contract names them (`get_cookie_args`, `fetch_media_info_batch`, `send_cookie_alert_email`, `raise_cookie_attention`).
- Isolation: every case runs against a temp `DATA_DIR` (see `tests/conftest.py` pattern). Never touch live `data/`. Tripwire: no new `data/sync_progress_*`, `data/expand_checkpoint_*`, or `data/digests/*.json` after the run.
- Mock the network, not the contract: Playwright / yt-dlp / SMTP are stubbed or run with `--dry-run` where noted. No live-Instagram assertion may gate pass/fail (nondeterministic 429s).
- Regression symmetry: each cookie-free case has a cookie-mode mirror proving the old path is unchanged (abort still aborts, popups still fire).

## 2. Entry criteria / environment

- Python env installs `requirements.txt`; `pytest` collects cleanly.
- Baseline: `git status` recorded; live `data/` snapshotted before run.
- Env scrubbed: `VIEWING_PIN=""`, `SMTP_*=""`, `NOTIFICATION_EMAIL=""` (conftest does this automatically).
- Entry probe (must pass before matrix): `config.COOKIE_FREE_MODE` defaults to `False` when `COOKIE_FREE_MODE` is unset (REQUIREMENTS §2, audit R1). NOTE (observed 2026-09-27): working tree shows `config.py:117` default `True` — UAT-CFG-01 below fails until corrected.

## 3. Exit criteria

- All P0 cases pass. P1 may have at most documented non-blocker waivers (§7).
- No live-state tripwire firing; `py_compile` clean on touched modules.
- Any failure reproduced on a clean checkout before filing as a defect (dirty-tree guard).

## 4. Test matrix

Conventions: `CF` = cookie-free mode, `CB` = cookie-based mode. `Mode source` = CLI flag > `COOKIE_FREE_MODE` env > default `False`.

### A. Config & CLI resolution (REQUIREMENTS §2 bullets 1–2; audit R1)

| ID | Requirement | Procedure | Expected (observable) | P |
|---|---|---|---|---|
| UAT-CFG-01 | Default `False` | Unset `COOKIE_FREE_MODE`; import config / run `main.py --help`-adjacent dry-run, inspect startup log | `config.COOKIE_FREE_MODE is False`; startup logs `mode=cookie-based` | P0 |
| UAT-CFG-02 | Env parsing | Set `COOKIE_FREE_MODE` to each of `1/true/yes/on/0/false/no/off/garbage/unset` | Truthy set → CF; falsy set → CB; garbage/unset → default `False` | P0 |
| UAT-CFG-03 | Mutual exclusion | `main.py --cookie-free --use-cookies ...` | argparse error, exit 2, no pipeline work started | P0 |
| UAT-CFG-04 | Precedence CLI > env | `COOKIE_FREE_MODE=1 main.py --use-cookies ...` and `COOKIE_FREE_MODE=0 main.py --cookie-free ...` | Flag wins; startup log states `source: --use-cookies` / `source: --cookie-free` | P0 |
| UAT-CFG-05 | Startup log | Each run | Exactly one line `Pipeline mode: cookie-free\|cookie-based (source: ...)` | P1 |

### B. Identity isolation (REQUIREMENTS §2 gating 1–3, 6; audit R2/R5/R10/R11/R13)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-ID-01 | CF run with `data/cookies.json` + `cookies.txt` present (seed decoys) | Neither file read/injected; Playwright context gets zero cookies (`add_cookies` never called) | P0 |
| UAT-ID-02 | `get_cookie_args(cookie_free=True)` with cookies present and absent | Always `[]`; never `--cookies-from-browser chrome` | P0 |
| UAT-ID-03 | CB mirror of ID-02 | Returns `--cookies <cookies.txt>` when present, else `--cookies-from-browser chrome` | P0 |
| UAT-ID-04 | CF enrichment path | `fetch_media_info_batch` returns `{}` with zero network; `enrich_candidates_via_media_api` returns candidates untouched | P0 |
| UAT-ID-05 | CF yt-dlp metadata + download | Cookie args empty; `download_reel_video(..., cookie_free=True)` forces `use_cookies=False`; legacy doubles without the kwarg still work (no `TypeError` cascade) | P0 |
| UAT-ID-06 | CF session construction | `InstagramSession(cookie_free=True)` skips `_inject_cookies`; legacy zero-arg session doubles still constructible | P1 |
| UAT-ID-07 | CF gate silence | Force anonymous 429/`InstagramBlocked` in discovery, per-reel metadata, Tier-2 | `_GATE` never trips; run continues (see §C) | P0 |
| UAT-ID-08 | CB gate mirror | Same fault in CB mode | Gate trips / cookie abort path preserved | P0 |

### C. Anonymous failure handling (REQUIREMENTS §2 failure bullets; audit R3/R4)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-FH-01 | CF Tier-1 creator raises `InstagramBlocked` (incl. direct raise escaping backoff) | Log + soft-skip creator, continue roster; exit code NOT driven by this alone | P0 |
| UAT-FH-02 | CF Tier-2 recommended handle challenged | Log + soft-skip (`done_map` marked), continue; no whole-run abort | P0 |
| UAT-FH-03 | CF rate-limit waits | `waits == ()`; at most one ~3 s retry per creator, then skip. Assert no 20/40/80 min sleeps scheduled | P0 |
| UAT-FH-04 | CF empty creator grid | Per-creator skip recorded; roster completes | P0 |
| UAT-FH-05 | CB mirror Tier-2 block | Instant whole-run abort (unchanged behavior) | P0 |

### D. Orchestration refusals & bypasses (audit R8/R12)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-OR-01 | `main.py --cookie-free --sync-following` | Exit 2, message `Following sync requires an authenticated account...`, failure alert fired (`_alert_sync_abort` called) | P0 |
| UAT-OR-02 | `main.py --cookie-free --expand` | Exit 2, message `Expand feed discovery requires an authenticated account...`, failure alert fired | P0 |
| UAT-OR-03 | CF `run_full_sync` pre-flight | No session login probe, no follow-cooldown check, no `cookie_exporter.py` subprocess spawned | P0 |
| UAT-OR-04 | CF dashboard workers (`trigger_adhoc_sync_task`, `trigger_sync_resume_task`, `trigger_expand_task` with `COOKIE_FREE_MODE=1`) | Workers skip cookie refresh/cooldown/trust-warming, thread `cookie_free` into `run_full_sync`; expand worker fails fast with `failed` state + error log | P0 |
| UAT-OR-05 | CF dashboard `/api/sync-following` | HTTP 400 refusal | P1 |
| UAT-OR-06 | CF Tier-3 skip | `extract_external_reels_from_feed` never called at either `run_full_sync` site; skip logged | P0 |

### E. Alert suppression vs. failure alerts (REQUIREMENTS §2 alert bullets; audit R8)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-AL-01 | `send_cookie_alert_email(cookie_free=True)`; and with `config.COOKIE_FREE_MODE=True` | Returns `False`, sends nothing | P0 |
| UAT-AL-02 | `raise_cookie_attention(..., cookie_free=True)`; and with env CF | Returns `False`, no popup file/state | P0 |
| UAT-AL-03 | CF run hitting refusals / checkpoint-mismatch (OR-01/02, RS-02) | `_alert_sync_abort` (failure email path) STILL fires — suppression covers cookie alerts only | P0 |
| UAT-AL-04 | CB mirror: trigger cookie alert conditions | Email + popup fire normally | P0 |

### F. Checkpoint & resume contract (REQUIREMENTS §2 checkpoint bullets; audit R6/R7/R9)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-RS-01 | CF + CB runs write `data/sync_progress_<week>.json` and `candidates_cache.json` | Both envelopes contain `"cookie_free": true/false` matching the run | P0 |
| UAT-RS-02 | `--resume` with banked mode ≠ active mode, no `--force` | Explicit `Checkpoint mode mismatch: banked cookie_free=X vs active cookie_free=Y` error, `return 2`, failure alert; checkpoint NOT retired/deleted | P0 |
| UAT-RS-03 | Same mismatch with `--force` | Proceeds (fresh-start semantics), mismatch logged | P0 |
| UAT-RS-04 | Fresh (non-resume) run over opposite-mode checkpoint | Existing retire path (no loud refusal) | P1 |
| UAT-RS-05 | Legacy checkpoint/cache without `cookie_free` key | Read as cookie mode (`False`); CF resume refuses, CB resume accepts — fail-closed | P0 |
| UAT-RS-06 | Candidates-cache seeding across modes | `params_match` requires mode equality; opposite-mode cache ignored | P0 |
| UAT-RS-07 | Shortfall-resume path (`stage == shortfall_paused`) | No `AttributeError`; uses module-local `MIN_DEPLOY_ITEMS`, never `config.MIN_DEPLOY_ITEMS` | P0 |

### G. Shortfall & metadata contract (REQUIREMENTS §2 shortfall bullets; audit R9)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-SF-01 | CF yield < `MIN_DEPLOY_ITEMS` (= `int(TOP_DIGEST_COUNT*0.6)`, 150 at defaults) | Stage `shortfall_paused`, exit 2, zero R2 mutation, weekly digest file untouched | P0 |
| UAT-SF-02 | CB mirror below floor | Same gate behavior (shared constant, no hardcoded 150) | P0 |
| UAT-SF-03 | Anonymous pages hide like counts | Ranker falls back to grid view counts, marks `metrics_estimated=True` | P1 |
| UAT-SF-04 | Reels lacking timestamps | Dropped under standard cutoff rules (finite weekly promise preserved) | P1 |
| UAT-SF-05 | No second depth knob | `--limit-per-creator` is the only depth control; no `ANON_REELS_PER_CREATOR_DEPTH` env exists | P1 |

### H. Shell / cron passthrough (REQUIREMENTS via IMPLEMENTATION_PLAN Phase 5)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-SH-01 | `COOKIE_FREE_MODE=1 run_weekly.sh` (or traced equivalent) | Skips Chrome cookie-export step, passes `--cookie-free` to `main.py`; `bash -n` clean | P1 |
| UAT-SH-02 | `resume_pending.sh` with banked checkpoint | Passes banked mode (`--cookie-free` iff banked `cookie_free=true`); unreadable checkpoint fails closed to `--cookie-free` | P1 |
| UAT-SH-03 | `healthcheck.sh` step 2 with `COOKIE_FREE_MODE=1` | `sessionid` check soft-skips instead of failing | P1 |
| UAT-SH-04 | `run_friday_overnight.sh` CF | Chrome prompt skipped | P2 |

### I. Automated regression gate (pre-existing suites)

`test_cookie_free_mode` (8) · `test_dashboard` · `test_sync_resume` (6) · `test_expand_resume` · `test_frontier_fixes` · `test_smart_funnel` · `test_failure_alerts` (17) · `test_fable_audit_fixes` (18) — audit baseline **129 passed**. Plus broader sweeps per audit §5. Any red case is P0 regardless of matrix result.

## 5. Copy-paste manual probes

```bash
# Entry probe (UAT-CFG-01)
env -u COOKIE_FREE_MODE python -c "import config; assert config.COOKIE_FREE_MODE is False, config.COOKIE_FREE_MODE; print('default False OK')"

# Env parsing spot-check (UAT-CFG-02)
for v in 1 true YES on 0 false NO off garbage; do COOKIE_FREE_MODE=$v python -c "import config; print('$v' , config.COOKIE_FREE_MODE)"; done

# Refusal contracts (UAT-OR-01/02, expect exit 2 + alert)
python main.py --cookie-free --sync-following; echo "exit=$?"
python main.py --cookie-free --expand; echo "exit=$?"

# Mode-mismatch resume (UAT-RS-02, expect exit 2, checkpoint retained)
python main.py --cookie-free --resume; echo "exit=$?"
ls -la data/sync_progress_*.json

# Full automated gate (UAT §I)
python -m pytest tests/test_cookie_free_mode.py tests/test_dashboard.py tests/test_sync_resume.py \
  tests/test_expand_resume.py tests/test_frontier_fixes.py tests/test_smart_funnel.py \
  tests/test_failure_alerts.py tests/test_fable_audit_fixes.py -q
```

## 6. Defect triage

- P0 (blocker): wrong default (CFG-01), any identity leak (ID), abort-instead-of-skip (FH), silent mode mixing (RS-02/05/06), shortfall mutating R2 (SF-01), missing failure alert on refusals (AL-03/OR-01/02), suite regression.
- P1 (fix soon): log wording, dashboard 400 text, shell-script edge fallbacks, metadata-marking gaps.
- P2 (waiver-candidate): Friday-overnight prompt nuance, doc polish.
- Single-user rule: a P1 failing more than ~3 verification rounds without showing a crash or data-loss risk gets relaxed or converted to a test seam rather than line-tuned further.

## 7. Traceability

| REQUIREMENTS §2 item | UAT cover | Audit fix re-verified |
|---|---|---|
| `COOKIE_FREE_MODE` default `False` | CFG-01/02 | R1 |
| CLI flags + precedence + startup log | CFG-03/04/05 | — (pre-existing) |
| No cookie read/inject | ID-01 | R2/R11/R12 |
| `get_cookie_args → []` | ID-02/03 | — |
| Skip `fetch_media_info_batch` | ID-04 | R5 |
| Refuse `--sync-following` | OR-01/05 | R8 |
| Bypass healthchecks/probes/cooldowns/exporter | OR-03/04, SH-01/03 | R12 |
| yt-dlp without cookie args | ID-05 | R10 |
| Skip Tier 3 (both sites) | OR-06 | — |
| Soft skip, bounded retry, no long sleeps | FH-01–04 | R3/R4 |
| Never trip `_GATE` anonymously | ID-07/08 | R13 |
| Suppress cookie email + popup | AL-01/02/04 | — |
| Stamp checkpoints + caches | RS-01/05/06 | R6 |
| Resume mismatch refuse unless `--force` | RS-02/03/04 | R7 |
| `MIN_DEPLOY_ITEMS` floor + `shortfall_paused` w/o R2 mutation | SF-01/02, RS-07 | R9 |
| Metadata degradation contract | SF-03/04 | — |
| Shell passthrough | SH-01–04 | — (verified) |
| No second depth knob | SF-05 | deliberate non-change |

## 8. Out of scope / known non-blockers

- Live-Instagram end-to-end (excluded: nondeterministic 429s must never gate CI).
- ~7 s per-reel anonymous Playwright enrichment cost (accepted tradeoff; revisit only on repeated floor misses).
- Resolver fallbacks failing closed to `--cookie-free` on config-import failure (accepted safe default).
- No UI changes (static viewer untouched).
