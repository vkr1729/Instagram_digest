# Frontier Requirements Review: Instagram Digest 250-Reel Expansion

Source: `.workflow/REQUIREMENTS.md` (v5.1.0)
Date: 2026-09-19

## 0. Target User Scale Anchor

Strictly single-person personal use. Local Linux overnight Friday sync (unattended), iOS app via SideStore → LiveContainer, web dashboard for curation only. Implications:

- Reject multi-tenant auth, distributed DBs, queues, hosted schedulers.
- Prefer local files (`sources.json`, `data/recommended_creators.json`), flat JSON schemas, idempotent scripts.
- Failure handling should favor degraded-but-usable weekly digest with zero babysitting over strict correctness.
- Every probing question below is scoped to this anchor.

## Q1. What happens when Tier 1+2+3 cannot reach 250 (or 150 minimum)?

Ambiguity / failure modes:
- REQ sets `TOP_DIGEST_COUNT = 250`, `MIN_DEPLOY_ITEMS = 150` but does not define behavior below 150: abort deploy, ship short digest, or keep prior week?
- Tier 3 global `/reels/` fallback is unbounded in spec — on a single Linux box overnight this risks rate-limit / login challenge / infinite scroll stall, which is exactly the class of bug Q4's session-hijack fix addresses.
- Partial-week staleness: Tier 1 capped at last-7-days; a thin week could silently ship stale Tier 3 filler as high-signal.

Recommended approach:
- Define explicit shortfall policy: if items >= 150 ship with `shortfall: true` flag in manifest + dashboard banner; if < 150 keep prior week's digest live and alert via existing notifier, never deploy empty/partial manifest.
- Timebox Tier 3 (e.g. max N scroll batches / M minutes) and cap its share (e.g. max 30% of digest) so fallback cannot dominate or blow the overnight window.

Alternatives:
- A. Strict gate: abort deploy below 250. Simple but violates zero-babysitting value — one thin week = no digest.
- B. Unbounded Tier 3 scrape until 250. Maximizes count but risks session ban, multi-hour overrun, low-signal filler.

## Q2. How does unattended `agy -p` recommendation survive malformed / hallucinated output?

Ambiguity / failure modes:
- 6 separate headless `agy -p` calls with strict JSON schema, no stdin. No spec for: non-JSON prose wrapper, truncated output, timeout, duplicate / nonexistent / private handles, wrong category, follower_scale free-text drift.
- One bad category run could clobber `data/recommended_creators.json` with partial data, poisoning Tier 2 for the whole week.
- Hallucinated handles flow directly into scraper pipeline → wasted scrape budget + stalls.

Recommended approach:
- Validate-then-commit per category: JSON parse (strip code fences), schema check (required keys, handle regex `^[A-Za-z0-9._]{1,30}$`), dedupe against `sources.json` + across categories, quarantine failures to `data/recommended_creators.quarantine.json`.
- Atomic write only on >= N valid categories (e.g. 4/6); otherwise retain prior week file and mark `recommendations_stale: true`. Per-call timeout + 1 retry, stderr to logs, never prompt.

Alternatives:
- A. Best-effort overwrite: whatever `agy` returns gets saved. Minimal code but single failure poisons Tier 2.
- B. LLM self-repair loop (re-prompt on schema failure). Higher quality but unbounded runtime — bad fit for overnight unattended single-box sync.

## Q3. Watch-timer reset vs. cache purge vs. offline rollover race?

Ambiguity / failure modes:
- Timer resets when manifest `week_id` changes; `MediaCacheManager.purgeOldWeekDirectory` purges prior week on rollover preserving `Bookmarks/`. Underspecified: user offline at rollover (old digest still displayed — does timer reset early?), partial deploy (manifest updated but media missing), downgrade/rollback of `week_id`.
- `UserDefaults` timer vs. filesystem purge are two unsynchronized stores — crash between them = timer reset but old media present, or vice versa.
- LiveContainer vs. SideStore sandbox paths may differ; `LibraryPathResolver` fallback path untested.

Recommended approach:
- Single rollover transaction keyed on successfully loaded manifest: only when new `week_id` manifest + index parse succeeds, in order: (1) write new timer epoch, (2) purge old week dir excluding `Bookmarks/`, (3) swap active manifest pointer. Offline = no rollover, timer keeps counting.
- Derive timer key as `watchSeconds_{week_id}` rather than reset-in-place, so rollback/partial states are naturally isolated.

Alternatives:
- A. Reset timer on first sight of new `week_id` string anywhere (push, filename). Simpler but triggers on partial/failed downloads.
- B. Server-driven purge command from dashboard. Violates scale anchor — adds coordination channel for a single-user app.

## Q4. Does the session-hijack fix isolate metadata extraction without blowing the single-box budget?

Ambiguity / failure modes:
- REQ: "isolate single-reel metadata extraction so it does not navigate main feed away from `/reels/`" + "fix DOM metrics (0 likes stall)". No mechanism specified: new tab, second page in same context, separate browser context, or API fallback?
- Separate contexts/tabs on one Linux box multiply memory + login-session risk; Instagram may flag concurrent sessions. Same-page navigation is the bug; naive new-tab fix may reintroduce it via shared SPA router state.
- DOM selector fragility: "evaluating 0 likes" stall suggests wait-for-selector with no timeout — fix needs bounded waits.

Recommended approach:
- Metadata fallback opens in a dedicated secondary `Page` (same browser, same context to reuse login, but independent navigation history), closed in `finally`; main feed page object never passed to fallback. All metric waits bounded (e.g. 5s) defaulting to `null`/`0` with log, never blocking the pipeline.
- Add regression guard: assert `mainPage.url` still contains `/reels/` after fallback returns; log + reload if violated.

Alternatives:
- A. Fully separate browser context per fallback. Strongest isolation but forces re-login per context — heavy and ban-prone on single box.
- B. In-place navigation + back button. Cheapest but preserves the original bug class (SPA state loss, scroll position reset).

## Cross-cutting note (dashboard mutation race)

`POST /api/channels/add` mutates `sources.json` while Friday sync may be reading it. At personal scale the recommended approach is atomic file replace + sync reads snapshot at start; no locking service. Alternative of live-reload adds complexity with no multi-user payoff — out of scope per anchor.
