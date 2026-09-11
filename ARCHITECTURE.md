# Instagram Digest — Architecture

> Status: post-hardening revision (2026-09-11). Supersedes all earlier
> revisions. Where this document and code disagree, the code wins; where it
> and `CHANGELOG.md` disagree on history, the changelog wins.
> Review baseline: pre-fix adversarial score F/39 → post-fix B+/87.

## 1. What the system is

Instagram Digest converts an unbounded algorithmic Reels feed into a finite
weekly briefing: top 300 reels (expandable by +100), ranked by
creator-normalized viral score, delivered as a zero-dependency static PWA.

Hard constraints the architecture serves:

- **$0 marginal cost.** R2 ($0 egress) + GitHub Pages. Repo stays ~2 MB.
- **Finite by construction.** Watched state + "All Caught Up" terminal.
- **Stable links.** Instagram CDN URLs expire in hours; R2 URLs live all week.
- **Offline on iOS.** Service Worker + CacheStorage + synthetic HTTP 206.

## 2. Pipeline

```
Chrome cookies → cookie_exporter.py → extractor.py ─┬─ tracked creators
                                                     └─ feed discovery
        → ranker.py → main.py (download pool) → storage_r2.py (R2)
        → ranker.save_digest_batch → site_builder.py → site/
        → gh-pages  …  local_server.py serves / builds locally
```

Stage contracts:

1. **Ingest** (`extractor.py`, `cookie_exporter.py`). No login automation;
   reuses the user's Chrome session. Emits candidate reels with
   best-effort metadata.
2. **Rank** (`ranker.py`). Pure function of candidates + sources. Bayesian-
   damped viral score, fair-share caps (max 4/creator), category quotas,
   deterministic shuffle. Assigns final ranks.
3. **Materialize** (`main.py` + `storage_r2.py`). Downloads, uploads,
   drops unplayables. The digest file is written only after filtering, so
   the manifest never references missing media.
4. **Build** (`site_builder.py`). Jinja partials → atomic `index.html` +
   `local_index.html` + share pages + `data.json`. Cards without a
   playable URL are never rendered.
5. **Serve** (`local_server.py` locally; GitHub Pages + `sw.js` remotely).

## 3. Decisions that matter

### 3.1 Extraction without login (`cookie_exporter.py`)

Choice: read the local Chrome SQLite profile, export Netscape + Playwright
JSON. Rationale: Instagram's login surface (2FA, CAPTCHA, TLS heuristics)
is the highest-risk automation point; a pre-authenticated session skips it
entirely. Expiry raises `CookieExpiredException` → email alert → abort, so
the pipeline never challenge-loops.

### 3.2 Anti-bot pacing (post-fix)

Choice: Gaussian `human_pause()` (10% long tail) everywhere; randomized
cooldowns (every 18–32 evals, N(12,3)s); per-context UA/viewport/locale/
timezone rotation from a Chrome-only pool; webdriver-mask init script;
enrichment capped at 2 pooled browsers with exclusive checkout; API
pagination with truncated exponential backoff.

Trade-off stated plainly: uniform jitter was a classifier feature, so it
had to go. What remains is probabilistic defense, not proof — expect to
tune against real 429s. Deliberately not done: cursor biometrics, full
stealth frameworks (fragile, high-maintenance, marginal ROI at this scale).

### 3.3 Parser resilience (post-fix)

Choice: selector fallback chains, pure parse helpers (`_extract_shortcode`,
`_parse_date_flexible` with naive-dates-pinned-to-UTC), canonical-link
recovery, extended URL + soft-block (HTTP-200) detection, fail-closed
empty grids.

Rationale: every Instagram markup dependency is a silent-zero-items risk.
The viability gate in `main.py` (candidate ratio + empty-creator ratio)
remains the backstop that aborts a run before it touches the digest.

### 3.4 Durability (post-fix)

Choice: `atomic_io.durable_write_json` (temp + flush + fsync + replace +
dir-fsync) for all state; one `_PIPELINE_LOCK` across sync/expand;
corrupt files quarantined, never silently reset; R2 key-set access locked.

Rationale: the two worst pre-fix failure modes were torn digests and a
pruner that treated them as truth. Durability is now structural, not
conventional — no code path writes state any other way.

### 3.5 Storage identity: rank keys, id truth (post-fix)

Choice: R2 object keys stay rank-prefixed (`{rank:02d}_{handle}_{id}.mp4`)
for backward compatibility, but **all** matching logic (pruner, dedup) keys
on `(week, reel-id)` suffix. `run_expand` is two-phase and append-only:
existing ranks/keys are immutable; new keys derive from post-filter ranks.

Trade-off: re-ranks still churn key prefixes (quota cost only, correctness
is unaffected). A hash-key migration would fix the churn; deferred as
non-blocking.

### 3.6 Static atomic PWA (`site_builder.py`, `templates/`)

Choice: author in Jinja partials, ship one inlined document. One request
loads 100% of UI; SW caching can't partially corrupt; no build toolchain.

Security discipline (post-fix): no attacker-controlled value is ever
interpolated into a JS string — feed handlers use `dataset`, share pages
use `html.escape(quote=True)`, dynamic JS values use `|tojson`. There is
an adversarial corpus test; any new interpolation must extend it.

### 3.7 Offline video (`sw.js`)

Choice: intercept `.mp4`, slice from CacheStorage, synthesize RFC 7233
ranges — including suffix ranges and real `416 + bytes */size`.

Rationale: WebKit rejects 200s for media and stalls on clamped 1-byte
206s. The range matrix is executed against the shipped file in Node, so
regressions fail loudly. Known limit: full-blob materialization per seek;
no physical-iOS observation yet.

### 3.8 Rendering performance (post-fix)

Choice: `content-visibility: auto` + `contain` on cards, compositing layer
only on `.is-active`, decoder release on filter-hide and outside the
`[-2,+3]` sliding window, scroll-guarded tap handlers.

Rationale: 300 cards × listeners × layers is the actual mobile bottleneck,
not network. A full DOM recycler stays rejected: intersection-driven
`scrollIntoView` + containment gets most of the benefit at none of the
complexity risk.

## 4. Failure modes and backstops

- Instagram blocks session → `InstagramBlocked`/`CookieExpiredException` →
  abort without touching digest/site; viability gate catches silent zeros.
- Upload fails → reel dropped from manifest AND site (never a dead card).
- Digest corrupt → quarantined; pruner skips that week entirely.
- Sync and expand collide → second caller gets `already_running`.
- Deploy threshold: refuses to publish under 60% of target playable items.

## 5. Verification (what "done" meant)

- 84 non-browser tests green; 16 Playwright tests green on maintainer
  hardware (sandbox kills browser processes with SIGTRAP — see CHANGELOG).
- SW range logic executed in Node (12-case matrix), not just read.
- Rendered bundle inspected: fix markers present, inline scripts pass
  `node --check`.
- Live server probed: 7/7 routes 200, watched round-trip, quarantine,
  pipeline exclusion.

## 6. Open questions for the next reviewer

1. **Evasion economics.** Given real 429/soft-block logs (none exist yet —
   add telemetry first), which is cheaper: slower pacing or more sessions?
2. **Key migration.** Is hash-keyed R2 storage worth a one-time copy pass
   over rank-prefix churn within a 5 GB quota?
3. **SW memory.** Does per-seek blob materialization jank on a physical
   iPhone SE during rapid scrub? Needs a device, not a theory.
4. **State sync.** Watched state is per-device (`localStorage` + server
   file). Is multi-device divergence a real user complaint before any
   CRDT talk?
