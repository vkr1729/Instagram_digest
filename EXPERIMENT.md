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

- F1: `audit_channels.py --yield` + `--yield-window N` (+ tests).
- F2: `GET /api/storage` + dashboard widget (+ tests).
- F3: notifier recommended section (+ tests).
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
14. **Stale pipeline-lock breaker** — `data/.pipeline.lock` held by dead PID gets
    cleared with logging instead of exit-3 forever. FIT: medium (ops robustness).
    → IMPLEMENT (F9, careful: only break provably-dead locks)
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

## Round 2 — implementation log

- F7: exposure/DNR transparency in Recommended section (+ tests).
- F8: shortfall expand suggestion banner (+ tests).
- F9: dead-pipeline-lock breaker (+ tests).
- F10: media integrity spot-check script (+ tests).
- F11: health-report recommendation stats (+ tests).

(Total after Round 2: 11. Round 3 brainstorms 10 more, implements to reach 15.)
