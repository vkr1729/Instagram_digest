# Changelog — Post-Review Hardening (2026-09-11)

All items below close findings from the adversarial review
(`HANDOFF_REVIEW_PROMPT.md`, pre-fix score F/39). Each fix ships with a
regression test; suite state after the work: **84 passed** (non-browser) +
**16 passed** (Playwright, on maintainer hardware — browsers cannot launch
in the sandbox).

## P0-6 — Stored XSS closed

- `templates/partials/feed.html`: share/unselect buttons pass values via
  `data-id`/`data-handle` + `this.dataset` instead of interpolating into
  JS single-quoted `onclick` strings.
- `site_builder.py`: share-page escaper replaced with
  `html.escape(..., quote=True)` (`&`-first, covers `&<>"'`); handle, rank,
  caption, thumb, poster, reel id all escaped.
- `templates/partials/player.js`: `currentWeekId` rendered via `|tojson`.
- Tests: `tests/test_xss_hardening.py` (adversarial corpus).

## P0-5 / P0-7 — Durability, locking, rank/key coherence

- New `atomic_io.durable_write_json`: temp + flush + fsync + `os.replace` +
  directory fsync, temp cleanup on failure only.
- Adopted by `ranker.save_digest_batch`, `local_server._atomic_write_json`,
  `main.save_last_run_info`, and the candidates cache.
- `local_server`: single `_PIPELINE_LOCK` across sync and expand (mutual
  exclusion); `_load_json_tolerant` quarantines corrupt state files to
  `*.corrupt-<ts>` instead of silently resetting them (all 9 state reads).
- `storage_r2`: `_R2_KEYS_LOCK` guards the shared upload fast-path set.
- Orphan pruner matches by `(week, reel-id)` suffix, never by rank-prefixed
  key; unparseable/empty digests are never ground truth; deletes run
  `Quiet=False` with per-key error logging.
- `main.run_expand` is two-phase (download to `_pending_` names → rank after
  filtering → upload with final keys); existing ranks/keys are never
  renumbered (append-only).
- Tests: `tests/test_durability.py` (atomicity, id-matching, torn-digest
  guards, expand coherence with a mid-list download failure).

## P0-4 — Service Worker RFC 7233 correctness

- `templates/sw.js`: suffix ranges, open-ended ranges, real `416` +
  `Content-Range: bytes */size` for unsatisfiable/multipart/malformed
  input. The old clamp-to-last-byte 206 is gone.
- Tests: `tests/test_sw_range.py` executes the shipped JS in Node over a
  12-case matrix.

## P0-1 / P0-2 — Scraper anti-bot posture

- `extractor.py`: Chrome-only UA rotation pool, per-context
  viewport/locale/timezone, webdriver-mask init script.
- `human_pause()`: Gaussian + 10% long tail, replacing all uniform sleeps;
  feed cooldown is randomized (every 18–32 evals, N(12,3)s) with varied
  scroll keys; per-creator and enrichment pacing use it too.
- Enrichment capped at 2 browsers from an exclusive-checkout session pool
  (was: 6 workers × fresh browser per reel).
- Following-API pagination: truncated exponential backoff (3 retries) +
  inter-page pacing.
- Tests: `tests/test_scraper_hardening.py`.

## P0-3 — Parser resilience, fail-closed blocks

- Extended URL block markers + 200-with-soft-block snippet detection;
  empty discovery grids raise `InstagramBlocked` on block markers.
- `_DISCOVERY_SELECTORS` fallback chain; `_extract_shortcode` href variants;
  `_parse_date_flexible` (multi-format, naive dates pinned to UTC);
  canonical-link shortcode recovery; `reel`/`tv` added to reserved handle
  paths.
- Tests: `tests/test_parser_resilience.py` (7 tests).

## D3 — DOM / memory scaling

- `styles.css`: `content-visibility: auto` + `contain` on cards;
  `will-change`/`translateZ` scoped to `.reel-card.is-active` (toggled in
  both playback paths); filter-hide detaches video `src` + `load()`;
  feed `dblclick` honors the scroll-suppression window.
- Tests: `tests/test_dom_perf.py` (static markers).

## Known non-blockers (deliberately deferred)

- R2 keys remain rank-prefixed (pruner is id-based, so this is quota churn
  only — hash-key migration is future work).
- SW still materializes the full blob per range request.
- No live-iOS AVFoundation observation; no device-farm telemetry data.
