# Frontier Plan Review: 250-Reel Expansion & Similar Creators

Source: `.workflow/REQUIREMENTS.md` + `.workflow/IMPLEMENTATION_PLAN.md` (v5.1.0)
Date: 2026-09-19
Method: static grounding against `config.py`, `main.py`, `extractor.py`, `ranker.py`, `local_server.py`, `Sources/`, `templates/dashboard.html`, `run_weekly.sh`, `resume_pending.sh`, `agy --help` (v1.2.7 installed).

## Verdict

Plan is well-scoped for the single-user anchor (no new DB/queues/auth — good). No over-engineering in structure. But there are **3 blocking correctness gaps**, **2 sequencing risks**, and **1 wrong verification step** to fix before implementation. Details below, ordered by severity.

---

## BLOCKER 1. Tier 2 "up to 8 reels" is silently capped to 4 by the ranker

- REQ #5 / Plan Phase 4 promise viral-weighted 1–8 reels per recommended creator.
- But `ranker.rank_top_reels(..., max_per_creator=config.MAX_PER_CREATOR)` with `MAX_PER_CREATOR=4` (`config.py:113`) enforces the cap at ranking time. Any combined ranking of Tier 1 + Tier 2 with the default cap discards reels 5–8 of every recommended creator — the extra Tier 2 scrape budget (up to ~240 extra discoveries + enrichments) is burned for zero digest slots.
- Phase 4 must make an explicit choice:
  - **A (recommended): rank Tier 2 in a dedicated pass** with `max_per_creator=8`, then merge with Tier 1's top picks before Tier 3 fill. Keeps the 4-cap character of the followed digest intact.
  - **B:** raise global `MAX_PER_CREATOR` to 8. One line, but changes digest character for followed creators too — contradicts current 4-cap expectation.
  - **C:** drop REQ #5 to 4/creator and save ~1–2 hrs of scrape time. Cheapest if 8 was aspirational.
- Whichever is chosen, the decision must be recorded in REQUIREMENTS.md §3 decision log — currently silent on it.

## BLOCKER 2. Tier 2 pinned reels will be discarded by the 7-day cutoff

- REQ says Tier 2 includes "pinned + new reels", but `extract_creator_reels` (`extractor.py:1025-1029`) drops every reel with `timestamp < cutoff` unconditionally. Pinned reels are typically months old → Tier 2's pinned allowance is dead on arrival if Tier 2 reuses this path with `days_back=7`.
- Fix: thread an `is_pinned` flag from `discover_creator_reels` through to the cutoff filter and **exempt pinned reels from the date gate** (ranker viral score will still order them honestly). Alternative `days_back=365` for Tier 2 also admits stale non-pinned duds — worse.
- Note the same function hard-skips pinned today (`extractor.py:744-746`); Phase 2's `include_pinned` param is the right seam, but the cutoff exemption must land in the same phase or the param is untestable.

## BLOCKER 3. `agy -p` invocation as specced ignores the CLI's own guardrails

Verified `agy` 1.2.7 is installed and `agy -p` exists, with two flags the plan must use but doesn't:
- `--json-schema` (accepts a schema string or file path) — enforces structured output at the CLI layer instead of prompt-praying for JSON. Phase 3 should pass a schema file, keeping the strip-fences/regex validation only as defense-in-depth.
- `--print-timeout` — **default `0` waits forever**. Six sequential unattended calls with no timeout is a hang-until-morning risk. Set an explicit per-call timeout (e.g. 180s) *plus* the `subprocess` timeout + 1 retry the plan already has.
- Two further preconditions missing from Phase 3:
  - **Auth preflight:** unattended cron `agy -p` needs non-interactive auth (project/API context) verified *before* Friday night. Add a fail-fast preflight (`agy -p` smoke prompt at sync start); on failure, skip Tier 2 and mark `recommendations_stale: true` rather than hanging or poisoning Tier 2.
  - **Thin-category proportionality:** `food` has 2 followed channels vs 19 entertainment. 10 recs × up-to-8 reels per category lets Tier 2 food (80 max) rival the entire followed base. The 50% category ceiling (`ranker.py:106-109`) contains the damage, but consider scaling rec quota to category size or skipping categories with <N followed channels.

---

## SEQUENCING RISK 1. Resume/checkpoint changes touch 4 files that must move together

Phase 1 + Phase 4 + Phase 5 split what is actually one atomic change:
- `main.py` has **no `--resume` flag** and `RESUMABLE_SYNC_STAGES` (`main.py:158`) has **no `shortfall_paused`** — both must be added, plus the `tier{1,2,3}_reels` checkpoint keys.
- `resume_pending.sh` auto-resumes any `sync_progress_*.json` via full `main.py --sync --deploy`. A `shortfall_paused` checkpoint will be picked up at next login and **re-run the full pipeline** (re-scraping Tier 1/2) unless the resume gate treats `shortfall_paused` as Tier-3-only *and* `resume_pending.sh` is updated or explicitly left to the dashboard path. Decide and document; the existing cross-process `flock` (`main.py:53-86`) makes double-resume fail-safe (exit 3) but the UX would be confusing.
- `local_server.py` `resume_pipeline_state()` (`local_server.py:641`) only lists stages `extracting/enriched/ranked` — `shortfall_paused` (and `cooling_down`) are invisible to the dashboard resume lane unless added. Same for `live_progress_state()`.
- Recommendations snapshot must be **frozen into the checkpoint**: if Friday refreshes recs then pauses at shortfall, resume must reuse banked Tier 2, not re-refresh (fresh recs + banked Tier 1 = mismatched windows).
- Checklist for the phase that owns this: `main.py` (stages, `--resume`, gate params) + `local_server.py` (2 state readers + `POST /api/sync/resume`) + `resume_pending.sh` (shortfall policy) + `templates/dashboard.html` (banner wiring).

## SEQUENCING RISK 2. Secondary-tab fix needs a new session method, not `context.new_page()` inline

- Plan Phase 2 says `session.context.new_page()`, but `InstagramSession` keeps `_context` private and all access goes through `get_page()`, which **bumps the recycle counter and can tear down + reopen the context** (`extractor.py:663-676`, `RECYCLE_EVERY=40`). Routing secondary pages through `get_page()` pollutes recycle accounting; calling a private `_context` from `extract_external_reels_from_feed` breaks encapsulation.
- Add `InstagramSession.new_isolated_page()` (fresh page, closed by caller in `finally`, invisible to recycle accounting). Also note the same hijack class lives at `extractor.py:1364` (the actual Tier 3 bug — correct target) **and** `extractor.py:1095` (`download_reel_video` probe; benign today since the pipeline closes the session before downloads, but the helper must assert `session is None` there rather than silently probing).
- Page-handle staleness: with ~128 creators + ~500 enrichments there will be ~15 context recycles per run. **Never cache the Tier 3 feed `page` across phases** — re-acquire after enrichment, and keep the plan's post-iteration `/reels/` URL assertion (plus reload-on-violation, per FRONTIER_REQUIREMENTS_REVIEW Q4).

---

## Phase-by-phase enhancements

- **Phase 1:** `config.py:112` default is currently **300**, not 250 — the default change affects `run_weekly.sh`, ranker `top_n` default, and the `All Top {N}` category label. Also reconcile: REQ says fixed `MIN_DEPLOY_ITEMS = 150` but `main.py:39` derives `0.6 × TOP` (== 150 at 250 — consistent, but pick one spelling and use it everywhere). Audit tests for hardcoded 300/100 assumptions before flipping the default.
- **Phase 2:** cap must be threaded through **both** `extract_external_reels_from_feed` call sites (`main.py` sync path *and* `_run_expand`) — the shared-function fix covers both, but the 2,000-eval ceiling needs setting at each call site. `discover_creator_reel_urls` `include_pinned=False` default preserves Tier 1 behavior — correct.
- **Phase 3:** `recommendations.py` needs zero cron/systemd changes (refresh happens inside `run_sync`) — confirm this explicitly so nobody adds a second scheduler. Quarantine file + `>=4/6 valid categories` gate from FRONTIER_REQUIREMENTS_REVIEW Q2 stands.
- **Phase 4:** adopt the requirements review's Tier 3 guardrails — **timebox + max ~30% digest share** — otherwise a hallucinated-Tier-2 week funnels into a 2,000-eval (~4 hr at current pacing: ~4s/eval + 35s cooldowns every 8–14 evals) low-signal filler run. Also: overnight budget is roughly Tier 1 (~1.5 hr @68 creators w/ pacing + breaks) + Tier 2 (~1–1.5 hr @60 creators) + enrichment (~1 hr @500×~7s serial) + Tier 3 fill + 250 downloads/uploads — fits an 8-hr window but with less slack than today; add a timing budget table to the plan.
- **Phase 5:** don't build a tab framework — `dashboard.html` has no tabs today; a **card section reusing existing styles** is the anti-over-engineering move. And **reuse `POST /api/channels/bulk-unselect` (action=remove)** for "Add to Channel List" instead of minting `POST /api/channels/add` — the restore path already appends missing handles. New endpoint only if its validation differs. Server-side handle regex + `atomic_io.durable_write_json` on any `sources.json` mutation (a malformed write aborts the next sync at "no active sources").
- **Phase 6 (independent — can go first):** no dependency on Phases 1–5; consider parallelizing. Findings:
  - Plan wording bug: `.moviePlayback` is the session **mode**, `.mixWithOthers` the **option** (`AudioSessionCoordinator.swift:21-25` already sets mode correctly — this is a one-line option addition, keep it that way).
  - No `addPeriodicTimeObserver` exists in `Models/` — accumulation must hook the active player in `AVPlayerPool` (pool implies multiple players: gate on the visible player's `timeControlStatus == .playing`, accumulate observer deltas, never wall-clock). Pause on background (`scenePhase`), or the counter overcounts.
  - `HeaderBarView` has exactly one call site (`InstagramDigestApp.swift:257`) — prop threading is trivial, but keep the ≥44pt hit-target convention for the new pill.
  - **Wrong verification step:** there is no `Package.swift` / SPM package — iOS builds via `xcodegen` (`project.yml`) + `xcodebuild`. `swift test` will fail; replace with the repo's actual `xcodegen generate && xcodebuild test` flow (cf. `docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md`).
  - `UserDefaults watchSeconds_{week_id}` keying is right (natural isolation on rollover/rollback, matching FRONTIER_REQUIREMENTS_REVIEW Q3). `LibraryPathResolver` already uses `withIntermediateDirectories: true` (`LibraryPathResolver.swift:132`) — Phase 6 there is verification + LiveContainer path testing, not new code.

## Missing pieces (add to plan)

1. **Shortfall email.** Overnight + unattended means nobody sees a dashboard banner. `shortfall_paused` must fire the existing notifier path (`send_failure_alert_email`-style, cf. `main.py:197-206`) or the pipeline stalls silently until manual inspection — violates the zero-babysitting anchor.
2. **Byte-budget visibility at 250.** 250 reels × ~20MB ≈ 5GB vs the 5.8GB `MAX_FEED_BATCH_BYTES` guard — the existing cap (`main.py:895-910`) can silently shrink 250 → 150. Add a `budget_capped: true` manifest flag + dashboard banner when it fires; do not raise the ceiling (8GB free-tier hard limit).
3. **Tier 1 number reconciliation.** REQ says "up to 4/creator" but code discovers `min(limit,5)` (6 for food) and ranker caps at 4 — discovery headroom is intentional, but state it once so a future cleanup doesn't "fix" it into a behavior change.

## Suggested execution order

Phase 6 ∥ (Phase 1 → Phase 2, Phase 3) → Phase 4 → Phase 5. Phase 6 is dependency-free (start immediately); Phases 2 and 3 are mutually independent after Phase 1's checkpoint contract is fixed; Phase 4 is the integration bottleneck; Phase 5 needs Phase 4's endpoint contracts (or mocks).

## Verification corrections

- Replace `swift test` with the xcodegen/xcodebuild flow.
- `pytest` list must include the two new suites (`tests/test_recommendations.py`, `tests/test_scraper_isolation.py`) in the command itself, plus a `--dry-run` gate (must still pass, zero mutations).
- Add: resume-matrix test (`shortfall_paused` × dashboard resume × `resume_pending.sh` login path — exactly one of them proceeds, others exit busy), and an overnight timing-budget estimate before sign-off.
