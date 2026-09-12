# Changelog — Desktop Ops Dashboard (2026-09-12)

Standalone local-only `/dashboard` page (Mock A + status-strip + responsive
collapse, per approved grill); viewer header stripped to pure viewing.

- New `templates/dashboard.html` (raw static, `/channels` pattern): run
  buttons (ad-hoc, expand with count, cookies, retrigger link), live
  pipeline status (5s poll), pending-resume lane, recent-activity strip.
- New `GET /api/resume-state` (read-only checkpoint/progress inventory),
  `GET /dashboard`, `GET /viewer` routes in `local_server.py`. Root still
  serves the viewer; no UA sniffing.
- Viewer cleanup: +100/cookie/ad-hoc buttons and their JS removed from the
  header; offline download kept everywhere; local-only Ops Dashboard link.
  Deployed Pages build carries no dashboard surface (one JS comment only).
- `launch.sh` opens `/dashboard` (desktop default landing).
- Tests: `tests/test_dashboard.py` (8 tests: resume-state, live routes,
  header/JS contract, launcher URL).

# Changelog — Crash-Safe Resume (2026-09-12)

Interrupted runs no longer lose their work. Both pipelines checkpoint
"done but not yet in the digest" atomically, and the next run tops up
instead of restarting. A login-time service finishes leftovers unattended.

## +100 expansion (`run_expand`)

- `extractor.CookieExpiredException` carries its partial finds; mid-scroll
  cookie death checkpoints them instead of discarding the run.
- Discovery stream-checkpoints every 10 finds, persists the full list before
  downloads, and keeps download/upload leftovers for retry.
- Checkpoints are envelopes (`version`, `target_count`, `reels`) so resume
  knows the original target; stale weeks pruned, newest week adopted.
- Tests: `tests/test_expand_resume.py` (8 tests).

## Weekly sync (`run_full_sync`)

- Staged progress (`extracting` → `enriched` → `ranked`) in
  `data/sync_progress_*.json`, keyed on matching run parameters (anchor
  window + per-creator limit). Aborts bank progress instead of deleting the
  candidates cache; viability-gate failure and hopeless runs clear it.
- Extraction skips visited creators (streaming writes every 5), enrichment
  reuses banked metadata by id (writes every 25), ranked stage jumps
  straight to downloads. Dry runs never touch progress.
- Tests: `tests/test_sync_resume.py` (4 tests).

## Auto-resume at login

- New `resume_pending.sh`: reruns `--sync --deploy` for pending sync
  progress, then `--expand <target> --deploy` per checkpoint (target from
  the envelope). Single-flights with flock, skips while any pipeline is
  alive (ancestor-aware pgrep guard), quiet no-op when nothing is pending,
  desktop notification on start/finish. Only a pipeline that integrates the
  work clears its checkpoint, so failed resumes stay retryable.
- New `systemd/instagram-digest-resume.service` (user unit, runs at login).
  Install: copy to `~/.config/systemd/user/`, `daemon-reload`, `enable`.

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
