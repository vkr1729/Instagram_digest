# Changelog — Probe-session fix + recommendations batch (2026-09-26)

- Fixed: step-0 session probe was never closed, so any run passing validation
  crashed at extraction with "Playwright Sync API inside the asyncio loop"
  (second start in one thread). `_probe_session_once()` always closes the
  probe; regression tests pin it. This was the dashboard resync failure.
- Added (rec #3): post-publish R2 key verification — missing keys are dropped
  before deploy so a corrupt digest never ships.
- Added (rec #4): session fast-fail now names the exact 5-minute fix plus the
  desktop popup. Added (rec #6 slim): success email carries R2 used-vs-quota.
  Added (rec #2): resume lane shows "banked work kept until" dates.
- `tests/test_dashboard.py` attention-site count 10 → 11.

# Changelog — Frontier review fixes (2026-09-26)

- Fixed P0: same-day re-run stray purge could delete the live digest's R2
  videos (`main._current_week_stray_keep_ids` unions live ids, skips purge
  when unreadable). Ghost audio: async slot loads now honor a
  `wantsPlayback` intent flag. Bookmark pin claimed `.cached` before the
  isolated copy existed; rows start `.evicted`, the copy always runs
  (remote branch included), rollover pins all bookmarked rows.
- Fixed P1: Chrome-epoch cookie expiry for yt-dlp; resume gate no longer
  retires banked work on anchor drift; full-set resume reuses banked
  download/upload maps; `run_weekly.sh` retries exit-3 lock collisions;
  audio-deactivation flag clears on throw; Download sheet `.paused` gets
  Resume/Cancel; corrupt-feed eviction no longer self-blocked.
- Fixed P2 batch: explicit run-kind tagging, R2-unavailable fail-closed,
  429 batch break, seen-ledger ordering, audit reads cache only, quarantine
  renames (not copies), local-purge date+mtime agreement, ranker dedup,
  stale-trigger alerts, resume_pending scoping, healthcheck strictness,
  watched-API validation, retrigger/resume routing honesty, systemd unit
  check-ins, launcher/top-up hazards. Dashboard: plain-language help per
  card, `~AI-estimate` follower counts, "Free laptop videos" action
  (`POST /api/storage/free-local`, pipeline-guarded, outbox-aware).
- App P2: bookmark overlay route-change pause, cap-ledger holes closed,
  un-bookmark tombstones, empty-category no longer plays full feed,
  watchdog-vs-purge race, LRU access timestamps.
- New `tests/test_frontier_fixes.py` (B1/B4/B5); updated audit, storage,
  and replay expectations to the fixed semantics. Full suite green.

# Changelog — Tap-intent race fix (2026-09-15)

- Fixed: tap committed on a paused/loading video paused it the instant
  autoplay won the 320ms debounce (field traces Sep 12 + Sep 15:
  ev-play -> ev-pause ~320ms at t~0; the manual-pause cooldown then
  wedged it until the next tap). The click handler now captures
  `video.paused` synchronously plus a first-tap bootstrap play-stamp,
  and the timer no-ops when the state flipped mid-debounce. Tap
  decisions are now traced (`tap-play`/`tap-pause`/`tap-noop`/
  `tap-unmute-keep`) — taps were invisible in `?mediadebug=1` before.
  New `tests/test_tap_intent_race.py` (4 static + 3 behavioral; all 7
  fail pre-fix). Slow first-byte on cold cache (8.5s in trace) is
  delivery latency, not logic; MP4s verified faststart.

# Changelog — PWA resume-after-unlock fix (2026-09-15)

- Fixed: PWA always opened at reel #1 instead of the last-active reel.
  The PIN lock hides the feed at player init so the initial scroll is
  deferred, but `unlockScreen()` never performed it — and the autoplay
  observer then overwrote the saved position with #1. `unlockScreen()`
  now runs the deferred `resumeInitialPosition()` one-shot after
  revealing the feed (also repairs `?reel=` deep links through the
  lock). New `tests/test_resume_after_unlock.py` pins it (unit + e2e;
  both fail pre-fix).

# Changelog — Release 5.0.0 (2026-09-15)

- Release version established: `config.APP_VERSION = "5.0.0"` (single
  source of truth), surfaced in the PWA manifest, README badge, and
  `PROJECT.md` title. `ARCHITECTURE.md` updated for the teardown release
  (rank ceiling, atomic share/archive/manifest, 0600 credential creates,
  archive SW scope, dry-run zero-mutation, single-flight following sync,
  266-test suite).
- Docs cleanup: removed 11 stale handover prompts, review briefs, and
  fix-plan MDs (FRONTIER_*, HANDOFF_*, REVIEW_*, INSTAGRAM_DIGEST_REVIEW_*,
  FINAL_TEARDOWN_*, FINAL_FRONTIER_*, MUSE_SPARK_HANDOFF). Root docs are
  now README / PROJECT / ARCHITECTURE / CHANGELOG / UAT_PLAN; the
  `Fable Feedback/` audit and `docs/superpowers/` design history stay.
- Worker `cloudflare/worker.js` (Telegram retry, bounded POST bodies,
  id↔source binding, parallel cron heads) deployed to production;
  liveness verified (403 shape + CORS preflight + R2 manifest serving).

# Changelog — Final Teardown Fixes (2026-09-15)

Implements `FINAL_TEARDOWN_REVIEW_AND_FIXES.md` (post-hardening adversarial
review): 4 P1 ship-blockers, 11 P2s, 40 P3s. New
`tests/test_teardown_final.py` (54 tests) pins each fix; suite is
266 passed with only the pre-existing Playwright sandbox failures.

- P1: `--dry-run` returns before the site compile (live share pages /
  thumbnails / `data.json` / archives untouched); playback-failure
  affordance selector fixed to `.play-pause-indicator`; `topup_digest.py`
  gains the C2 unplayable filter, F2 no-shrink/deploy gates, paced
  enrichment (`ENRICH_PAUSE`), a sanitized delete glob, and the pipeline
  file lock.
- P2: bookmark outbox persists attempts and never timers while offline
  (+ POST→DELETE coalescing); builder prefers persisted `r2_url` and
  sanitizes derived names; archive pages get `../` asset refs + a
  same-scope `sw.js` copy; `/api/sync-following` is single-flight;
  yt-dlp TLS verification restored; feed fallback reuses the session;
  expired-purge reports confirmed deletes only; credential files are
  created 0600; ranker caps the fair-share guarantee at `top_n`; Telegram
  archive retries once and the client paints "Saving…" while unarchived.
- P3: bounded POST bodies, no wildcard CORS on mutating endpoints, 416s
  carry `Content-Range`, `stat()` TOCTOU guards, fail-closed quota
  sentinel, defensive env parsing, CDN size cap + HTML rejection,
  PKCS#7 validation, atomic share/archive/manifest writes, deploy
  timeouts, balanced modals, honest PIN/README docs, and small client
  hardening (selector escaping, `switchWeek` validation, unobserve,
  0.5s advance contract, batched pre-scan, SW image cap + fetch timeout,
  Worker body/id/cron hardening).
- Worker changes (`cloudflare/worker.js`) are committed but NOT deployed —
  run `wrangler deploy` from `cloudflare/` and verify one bookmark stamps
  `telegram_message_id`.

# Changelog — Fable Hardening-Audit Fixes (2026-09-12)

Addresses the post-hardening adversarial audit (`Fable Feedback/`): two P0s
in the routine operator flow plus P1/P2 follow-ups. All 11 runnable audit
probes now fail (defects gone); new `tests/test_fable_audit_fixes.py`
(15 tests) inverts each probe.

- P0 expand week drift: sync persists `r2_url`/`video_url` on the digest;
  `run_expand` anchors to the digest's `run_date` instead of today, so a
  weekend +100 no longer re-points existing reels at non-existent keys.
  Expand also gains the `MIN_DEPLOY_ITEMS` viability gate.
- P0 cross-process lock: `main._pipeline_file_lock()` (flock on
  `data/.pipeline.lock`, exit 3 on contention) complements the threading
  lock, covering cron / resume / CLI vs dashboard threads.
- P1: `channels.html` escapes name/handle/category (`esc()`), drops inline
  handle interpolation; `GET /retrigger` renders only (page POSTs to
  `/api/sync-adhoc`); checkpoint resume filters ids already in the digest;
  soft-block detector strips scripts/styles/comments/paragraphs and the reel
  page requires absent validity signals before dropping.
- P2: corrupt expand checkpoints and `last_run.json`/blacklists are
  quarantined (`*.corrupt-*`); expand ranks start at max+1; `/videos/` and
  static routes reject dot-segment escapes; `serve_video_file` sends the
  status line on full GETs; pruner reports only confirmed deletes;
  `save_sources`, following cache, `index.html`/`local_index.html`/
  `data.json` write durably; locale/timezone pinned per session with
  locale-matched stealth script; disjoint discovery selectors.
- Docs: enrichment documented as serial (`ENRICH_WORKERS = 1`); collision
  behavior documents both lock layers.

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

- `extractor.py`: Chrome-only UA rotation pool, per-context UA/viewport,
  locale/timezone pinned per authenticated session (rotating them across
  contexts on one account is a linking signal), locale-matched
  `navigator.languages`, `webdriver: false`, PluginArray-shaped `plugins`.
- `human_pause()`: Gaussian + 10% long tail, replacing all uniform sleeps;
  feed cooldown is randomized (every 18–32 evals, N(12,3)s) with varied
  scroll keys; per-creator and enrichment pacing use it too.
- Enrichment serial (`ENRICH_WORKERS = 1`) through an exclusive-checkout
  session pool (was: 6 workers × fresh browser per reel).
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
