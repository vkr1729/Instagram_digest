# Overnight Innovation Experiment (branch: `experiment/overnight-innovation`)

Autonomous loop: brainstorm 10 → assess fit → implement the great ones → repeat to 15.
Rules: additive only, no breaking changes, every feature tested, single-person-repo
discipline (no speculative frameworks). iOS changes avoided (no device farm here).

## Round 1 — brainstorm (10 ideas)

1. **Zero-yield channel detector** — sources with 0 reels across last 3 digests →
   dashboard "Quiet channels" + reuse existing bulk-unselect. FIT: high (finite,
   high-signal goal; audit_channels.py covers membership, not yield). → IMPLEMENT (F1)
2. **Storage gauge API + widget** (local `videos/` bytes + R2 bytes/objects, fail-open).
   FIT: high (quota guard exists, zero visibility). → IMPLEMENT (F2)
3. **Recommended creators in digest email** (`notifier.build_email_message` += section).
   FIT: medium-high (closes discovery loop). → IMPLEMENT (F3)
4. **R2 pre-deploy URL sample check** (bounded HEAD sample, standalone script, warn-only).
   FIT: medium (early signal on mass-expiry/credential rot). → IMPLEMENT (F4)
5. **Category watch-progress API** (per-category totals + watched from `watched.json`).
   FIT: medium-high (mindful-progress visibility). → IMPLEMENT (F5)
6. **Sources hygiene audit** (`audit_channels.py --hygiene`: case/@ dupes, invalid
   handles; report-first, `--fix` gated). FIT: medium (prevents silent misconfig). → IMPLEMENT (F6)
7. **PWA grid "hide watched" toggle** — touches playback-adjacent PWA core. FIT: medium,
   risk high unsupervised. → DEFER
8. **iOS background manifest refresh** — iOS background limits make this unreliable.
   → REJECT (platform-infeasible)
9. **Cross-device watched sync** — no backend; CRDT overkill for one user; explicitly
   deferred in ARCHITECTURE.md. → REJECT
10. **Per-reel watch-time analytics** — needs client instrumentation + new storage;
    speculative value for one user. → REJECT (speculative)

## Round 1 — implementation log

- F1: one-click quiet bulk-unselect in channels.html reusing `inactive_creators()`
  (`--inactive-weeks N`) + the bulk-unselect endpoint (+ tests).
- F2: `GET /api/storage` + dashboard widget (+ tests).
- F3: notifier recommended section (+ tests). Production-wired: `send_digest_email`
  forwards `recommended=`/`target=` and the weekly pipeline passes the
  finalized set + `TOP_DIGEST_COUNT` (review fix).
- F4: `scripts/check_media_urls.py` (+ tests).
- F5: `GET /api/category-progress` (+ dashboard strip + tests).
- F6: `audit_channels.py --hygiene [--fix]` (+ tests).

## Round 2 — brainstorm (10 ideas)

11. **Recommendation exposure dashboard** — show exposures/DNR lists in dashboard
    Recommended section (transparency for the 5-strike rule). FIT: high. → IMPLEMENT (F7)
12. **Bookmark JSON export endpoint** (`GET /api/bookmarks/export` from local cache).
    FIT: medium — local bookmark store is iOS-side; Python side has no local bookmark
    rows (D1/worker only). → REJECT (no local source of truth)
13. **Expand auto-suggest on shortfall** — after sync, if digest < target, dashboard
    banner suggests `+N` expand with precomputed N. FIT: medium-high, tiny. → IMPLEMENT (F8)
14. **Stale pipeline-lock visibility** — flock self-heals on process death, so
    what an operator actually needs is holder attribution (pid/since/cmd), not
    a breaker. FIT: medium (ops robustness). → IMPLEMENT as holder sidecar (F9)
15. **Local media integrity spot-check** (`ffprobe` duration gate on a sample of
    `videos/`, mirroring the download validator). FIT: medium. → IMPLEMENT (F10)
16. **Weekly email watch-stats line** — needs PWA watch telemetry; doesn't exist
    server-side. → REJECT (no data source)
17. **Digest archive index** — check `site_builder` archives first; implement only if
    missing. → PROBE, then decide (F11?)
18. **Cookie-health preflight on dashboard refresh** — `refresh_cookies_status`
    already exists and banners. → REJECT (exists)
19. **Quiet-hours for failure emails** — single user, wants pages on failure.
    → REJECT (against alerting intent)
20. **Recommendation counts in health report** — `send_health_report_email` exists;
    add rec stats (fresh/stale, counts, DNR size). FIT: medium, tiny. → IMPLEMENT (F11)

## Round 2 — implementation log (done, all tests green)

- F7: exposure/DNR transparency in Recommended section (+ tests).
- F8: shortfall expand suggestion banner (+ tests).
- F9: lock-holder sidecar + busy detail + --lock-status (+ tests). Note: flock
  self-heals on death, so this names holders instead of breaking locks.
- F10: media integrity spot-check script (+ tests).
- F11: health-report recommendation stats (+ tests).

## Round 3 — brainstorm (4 needed to reach 15; backups noted)

21. **Lock-holder dashboard widget** — `/api/lock-status` reads the F9 sidecar
    (no main import; os.kill liveness inline) + Server-card line. FIT: high,
    tiny, coherent with F9. → IMPLEMENT (F12)
22. **Digest-email shortfall line** — notifier shows count/target + expand hint
    when short. FIT: medium-high, tiny, coherent with F8. → IMPLEMENT (F13)
23. **Recommendation stale badge** — dashboard flags rec sets older than 7d via
    recommended_at/refresh_state. FIT: medium, tiny. → IMPLEMENT (F14)
24. **Top-channels-by-yield table** — extend `/api/digest-status` with top 8
    contributors from the latest digest. FIT: medium-high (pairs with F1 quiet
    list), tiny. → IMPLEMENT (F15)
25. Digest archive index page — PROBED: archives ship per-week pages; an index
    adds navigation but duplicates gh-pages week switcher. → BACKUP (skip)
26. Wire media checks into deploy gate — unsupervised deploy blocking risks
    false-alarm failures at 3am. → REJECT (warn-only scripts stay standalone)

## Round 3 — implementation log (done, all tests green — 15/15 features)

- F12: lock-status endpoint + Server-card line (+ tests).
- F13: email shortfall line (+ tests).
- F14: rec stale badge (+ tests).
- F15: digest-status top channels (+ tests).

## Final tally

15 features, all additive, all tested: F1 quiet bulk-unselect · F2 storage
gauge · F3 email recommendations · F4 URL sample check · F5 category progress
· F6 hygiene audit · F7 exposure transparency · F8 shortfall suggestion ·
F9 lock holder · F10 media spot-check · F11 health rec stats · F12 lock-status
API · F13 email shortfall · F14 stale badge · F15 top channels.
Rejected with reasons: iOS background refresh, cross-device sync, PWA grid
toggle (core risk), archive index (duplicates Pages switcher), quiet hours
(against paging intent), deploy-gate wiring (3am false-alarm risk).

## Pre-merge adversarial review (3 tracks: backend, frontend, integration)

Fixed, all pinned by `tests/test_review_fixes.py` (+ additions to
`test_notifier.py`, `test_channels_manager.py`):
- P0 lock sidecar ownership: failed contenders no longer delete the holder's
  attribution (`acquired` flag).
- P0 feedback lost-update: flock-guarded read-modify-writes (reentrant-safe)
  for feedback + rec-cache mutations.
- P0 email prod wiring: `send_digest_email` forwards `recommended=`/`target=`;
  weekly pipeline passes the finalized set + target.
- P0 pipeline bypasses: `finalize_recommendations()` applied on cache-hit,
  auth-fail, quarantine-fallback, and Tier 2 use-site (frozen checkpoints
  exempt for resume consistency).
- P1 lock-status pid-0, prune list-shape, DNR/add payload guards, normalizer
  unification, health verdicts (missing cache reads "none yet", stale flips
  unhealthy), week_id containment, hygiene preservation, quiet endpoint test,
  media-script hardening (r2 preference, repo-root alias, clamps, .MP4).
- Frontend: empty-digest shortfall line, duplicate poller removed, esc/array/
  finite guards, pill classes on all paths, membership esc order, quiet-button
  disable, degraded flag surfaced.
- Docs corrected to code truth (250 reels, 8 GB quota, 8-day retention);
  README maintenance-scripts section; new `python-ci.yml` (pytest non-e2e on
  all branches).
- Declined with reasons: storage-walk caching (bounded by quota in practice),
  PID-recycle corroboration (status text only, flock is truth),
  Tier 2 cap-8 and all-seen fallback (deliberate pipeline design, digest
  composition risk unsupervised), sys.path convention (matches topup script).
