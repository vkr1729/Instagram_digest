# UAT Plan — Direct-Meta Stream (Stage 4e, Independent)

Date: 2026-10-24 (UTC) · Branch: `arch/direct-meta-stream` · Scope: `.workflow/REQUIREMENTS.md` (§2 scope matrix, §3 decisions, §4 offline matrix) + `.workflow/AUDIT_AND_REMEDIATION_REPORT.md` (Stage 4d)
Scale anchor: single-person personal use (Kedarnath Reddy) on iPhone, iOS 17+. No multi-user, no enterprise gates, no live-Instagram-gated CI.

Supersedes: the 2026-10-11 Anti-Doomscroll UAT plan previously in this file (preserved in git history).

## 1. Independence principles & safety rails

- Black-box first: verify via observable surfaces — served `data_direct.json`, built `site/`, CLI exit codes + stdout, on-device playback behavior, badges, storage used. Assert on internals only where the contract names them (`DIRECT_STREAM_MODE`, `MIN_DEPLOY_ITEMS`, `data_direct.json`, `BookmarkBadge_<i>_<state>`).
- Never run bare `python main.py` or `python main.py --sync` (full production sync: real downloads, R2 quota, Pages deploy, durable state). Allowed pipeline probes only:
  - `.venv/bin/python main.py --dry-run --limit-per-creator 1 --days-back 1`
  - `.venv/bin/python main.py --build-only`
  - `.venv/bin/python main.py --expand 3 --dry-run`
- After any interrupted run: `rm -f data/sync_progress_*.json data/expand_checkpoint_*.json data/expand_progress_*.json`. Tripwire: no new files under `data/` and no `data.json` byte change after any UAT probe.
- Secrets: never inspect `.env`; use `.env.example` + `config.py` for schema.
- Network mocking: pytest and CI stub the network. Only §6 (on-device) uses live network, and only the user's own phone on their own data/Wi-Fi.
- Any red automated suite is a P0 regardless of matrix results.

## 2. Entry criteria / environment

- Branch `arch/direct-meta-stream` checked out; `git status` + `git log --oneline -5` recorded.
- Stage 4d ship gates (a)+(b) met: §4 one-line test fix landed, `build-ipa.yml` green on the branch.
- Python: `.venv` installed per `requirements.txt`; `pytest` collects cleanly.
- iOS: unsigned IPA artifact from the green CI run, installed per `docs/RE_SIGN_AND_INSTALL.md`; iPhone on iOS 17+, no native Instagram app installed (per REQ).
- Entry probes (must pass before the matrix):
  - `.venv/bin/python -m pytest tests/test_direct_stream_mode.py -q` → 23 passed.
  - `.venv/bin/python main.py --build-only` clean.
  - Served production `data.json` sha256 recorded before/after (must be identical).

## 3. Automated verification

Run in this order. All must be green (see §7 for the single accepted skip).

```bash
# 3a. Direct-stream Python suite (bypass, parity, gates, byte-preservation, expand, URL edges)
.venv/bin/python -m pytest tests/test_direct_stream_mode.py -q

# 3b. Adjacent suites (builder + JIT publish path)
.venv/bin/python -m pytest tests/test_site_builder.py tests/test_jit_publish.py -q

# 3c. Full Python regression (optional but recommended pre-ship; ~3 min)
.venv/bin/python -m pytest -q -x --ignore=tests/test_browser_uut.py

# 3d. Static compile probe (no network, no deploy)
.venv/bin/python main.py --build-only

# 3e. Pollution tripwire (must print nothing / no such files)
git status --short data/; ls data/sync_progress_*.json data/expand_checkpoint_*.json data/deployed_direct_week.json
# NOTE: data/deployed_direct_week.json may exist from earlier runs; it must be byte-identical to HEAD (git status clean).
```

Swift / Xcode (delegated to CI — no SwiftUI SDK on Linux):

```bash
# 3f. CI gate (runs on push to arch/direct-meta-stream): unit tests + XCUITest + unsigned IPA
# .github/workflows/build-ipa.yml — requires green run; artifact: InstagramDigest-Unsigned-IPA
# Local equivalent on a Mac with Xcode 16.2:
xcodegen generate
xcodebuild test -project InstagramDigest.xcodeproj -scheme InstagramDigest \
  -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:InstagramDigestTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGN_IDENTITY="" CODE_SIGNING_REQUIRED=NO
```

## 4. Test matrix (REQ-traceable)

Conventions: P0 = ship blocker, P1 = fix soon, P2 = waiver-candidate. "Auto" = pytest/CI; "Sim" = Simulator/XCUITest; "Device" = §6 manual.

### A. Weekly pipeline & manifest channel (REQ §2-pipeline, §3-D5/D7)

| ID | Requirement | Procedure | Expected (observable) | P |
|---|---|---|---|---|
| UAT-PL-01 | Discover/rank Top 250, local processing continues | Scoped dry run (§1) | Ranked reels discovered; local downloads/validation occur; zero R2 calls in log | P0 |
| UAT-PL-02 | R2 bypass via `DIRECT_STREAM_MODE=True` | Auto: poison-stub bypass suite (3a) | All 10 R2 call sites provably untouched; suite green | P0 |
| UAT-PL-03 | Legacy parity (`DIRECT_STREAM_MODE=False` still does R2) | Auto: `test_legacy_parity` (3a) | R2 stubs called in legacy mode; suite green | P0 |
| UAT-PL-04 | `data_direct.json` embeds fresh Meta CDN URLs + thumbs + `size_bytes` | `--build-only`, inspect `site/data_direct.json` | `channel: direct`, Meta-host `video_url`s, `/thumbnails/{id}.jpg`, `size_bytes` present | P0 |
| UAT-PL-05 | Production `data.json` byte-identical | sha256 before/after any probe + Auto byte cases (3a) | Identical hash; mutation/missing-data cases abort deploy | P0 |
| UAT-PL-06 | Dead reels never ship; deploy gates effective | Auto: AUDIT-001/006/008/011 cases (3a) | Unplayable filtered, `count == len(items)`, 60% quorum enforced, expand filtered | P0 |
| UAT-PL-07 | Serve-check failures fail closed | Auto: AUDIT-009 case (3a) | Deploy returns False, no week marker written | P1 |
| UAT-PL-08 | Stale thumbnails pruned on deploy | Auto: AUDIT-024 case (3a) | Unreferenced jpgs deleted, `.nojekyll`/transients kept | P1 |
| UAT-PL-09 | Stranded legacy outbox surfaces, not orphaned | Auto: AUDIT-028 case (3a) | Warning + exit 1 with parked reels present | P1 |
| UAT-PL-10 | Non-destructive refactor (R2/Telegram files intact) | `git ls-files \| grep -E 'storage_r2|cloudflare|telegram'` | Files present; referenced only behind the legacy toggle | P1 |

### B. Bulk caching & storage discipline (REQ §2-iOS, §3-D1/D6/D8)

| ID | Requirement | Procedure | Expected (observable) | P |
|---|---|---|---|---|
| UAT-BC-01 | 1-tap "Download Week for Offline" auto-prompt on new week | Sim/Device: fresh week arrives | Prominent prompt + home-screen action with live progress | P0 |
| UAT-BC-02 | Immediate old-week purge on Download tap (~3 GB cap) | Sim/Device: tap with old week cached | Old week dir gone before first new byte; footprint never spans two weeks | P0 |
| UAT-BC-03 | Pre-purge space abort (never strand with zero weeks) | Sim: fill disk below batch need, tap Download | "Insufficient free space" before any wipe; old cache intact | P0 |
| UAT-BC-04 | Empty batch never wipes, never reports success | Sim: all-watched week, unwatchedOnly on, tap Start | Start/Retry disabled, "already downloaded or watched"; no purge, no false `.completed` | P0 |
| UAT-BC-05 | Partial success honest (`ready` vs `failed`) + targeted retry | Sim: stub 1 of N to fail | "Ready Offline (N-1/N, 1 failed)" + retry-failed-only; banner matches | P1 |
| UAT-BC-06 | Expired-URL 403 recovers once via manifest refresh | Sim: stub first-item 403 then fresh manifest | Single refresh, pending URLs rebuilt, resume; honest "Links expired" fail-fast if unchanged | P1 |
| UAT-BC-07 | Legacy `Bookmarks/*.mp4` one-time migration | Sim: seed legacy files, first launch | Files purged once, ledger zeroed, flag set; never re-runs | P1 |
| UAT-BC-08 | Paused vs failed banner copy distinct | Sim: pause mid-batch; then fail a batch | "Paused — tap to resume" vs "Download failed: msg"; no CTA-where-paused confusion | P2 |

### C. Bookmarks & dynamic resolver (REQ §2-bookmarks, §3-D2/D3/D4/D9)

| ID | Requirement | Procedure | Expected (observable) | P |
|---|---|---|---|---|
| UAT-BM-01 | Bookmarks store reference only (no R2/Telegram) | Auto: routing tests (3f) + inspect stored row | ID/URL/handle/caption/thumb/date only; zero network on save in directMode | P0 |
| UAT-BM-02 | Active-week bookmark plays from local cache | Device: bookmark cached reel, play | Instant playback, zero network (verify in airplane mode, §6) | P0 |
| UAT-BM-03 | Expired bookmark resolves via Cobalt online | Device: play purged-week bookmark on Wi-Fi | Fresh MP4 resolves with bounded retries; plays in-app (no IG app) | P0 |
| UAT-BM-04 | Resolver honors cancellation (swipe-away) | Sim: cancel during backoff | No second request fires after cancel | P1 |
| UAT-BM-05 | Challenge/auth errors honest, not generic | Sim/Device: stub or force 400-auth/HTML-200 | "Auth/challenge" message surfaced, `.failed` mapping kept for true failures | P1 |
| UAT-BM-06 | Endpoint/key override usable (Settings) | Device: set custom endpoint + key, resolve | Request carries `Authorization`, hits custom host; omission path sends none | P1 |
| UAT-BM-07 | Re-bookmark updates week in place, no dup crash | Device: bookmark same reel across two weeks | Single row, current weekID, badge resolves new local copy | P1 |
| UAT-BM-08 | Unsave never plays next after unmount | Device: unsave during playback | Overlay closes, silence; no phantom audio, no wasted resolve | P1 |

### D. Offline matrix (REQ §4) — all P0, verified in §6

| ID | Item state | Offline expected | Online expected |
|---|---|---|---|
| UAT-OF-01 | Active week, cached | 100% playback from local file | Local file, zero bandwidth |
| UAT-OF-02 | Active week, uncached | "Download required — get this week for offline" + Download Week button (never mislabeled "older") | Streams fresh CDN URL or downloads |
| UAT-OF-03 | Older bookmark (purged) | "Online required for older bookmarks" badge, no hang/spinner | Cobalt resolve + stream |
| UAT-OF-04 | Metadata/caption/thumbnail | Instant from local cache | Instant from local cache |

### E. Grid badges & player honesty (REQ §2/§4; audit UI contract)

| ID | Requirement | Procedure | Expected (observable) | P |
|---|---|---|---|---|
| UAT-UI-01 | 3-state badges (cached / download-required / resolver) | Sim/Device: week with all three states | Green/orange/globe badges correct per cell; `BookmarkBadge_<i>_<state>` identifiers present | P0 |
| UAT-UI-02 | Badge agrees with playback (zero-byte files) | Sim: seed zero-byte mp4 | Badge does NOT claim offline-ready; playback falls through consistently | P1 |
| UAT-UI-03 | No silent black screen when nothing playable | Sim: nil local + nil remote URL | Error + Retry affordance in both modes | P1 |
| UAT-UI-04 | No stale R2 gauge in direct mode | Device: bookmarks header | No zero 1.5 GB bar, no dead Free-Storage button | P2 |
| UAT-UI-05 | Per-case error icons | Device: force rateLimited/auth/unsupported/offline | Distinct icons (hourglass/lock/film/wifi.slash), not one wifi.slash | P2 |

### F. Audit-regression sampling (Stage 4d findings → re-verify)

| ID | Finding | Procedure | Expected | P |
|---|---|---|---|---|
| UAT-AR-01 | 002/033/034 rollover safety | CI unit suite (3f) green | Purge isolated to tmp in tests; LivePinSet + resume-data protection hold | P0 |
| UAT-AR-02 | 013 hostile reelID | CI unit suite (3f) green | No crash on hostile IDs; Optional canonical URL | P0 |
| UAT-AR-03 | 022/023 manifest guards | CI unit suite (3f) green | Empty manifests rejected to cache; bundle R2 fallback refused in directMode | P1 |
| UAT-AR-04 | 019 main-actor state writes | Sim: purge + delete flows | No purple runtime warnings; state consistent | P1 |
| UAT-AR-05 | 021/041/042 supply chain | Inspect CI run + repo | No binaries committed, pins logged, runbook linked in logs | P2 |

## 5. Known limitations & pre-accepted waivers (from Stage 4d §3)

These do NOT fail UAT; they are documented and dispositioned. Do not file them as new defects.

1. Cobalt has one hardcoded default instance (overridable in Settings); no fallback doc or live smoke yet — needs your self-host-vs-API-key call (UAT-BM-05/06 cover the honest-error and override paths).
2. After an unwatched-only subset download, the banner may print the full manifest count ("All N cached") — cosmetic; failed/paused/partial copy is correct.
3. A double-403 inside one bulk batch fails the batch with retry (single-403 recovery works).
4. Grid scroll may show minor jank on full 250-cell grids during progress updates (banner path is async).
5. The legacy-remote-bookmark Swift test skips under `directMode` by design (AUDIT-005) — an accepted skip, not a gap.

## 6. On-device manual checklist (iPhone, iOS 17+, no IG app installed)

Setup: install the CI unsigned IPA per `docs/RE_SIGN_AND_INSTALL.md`. Connect to Wi-Fi. Have one full week cached and ≥1 older-week bookmark saved from a previous week.

- [ ] **M1. 1-tap bulk download (UAT-BC-01/02).** With a new week available, confirm the auto-prompt appears; tap "Download Week for Offline". Confirm: old week storage drops first, live progress shows, completion banner states the true ready count. (P0)
- [ ] **M2. Overnight-scale soak (UAT-BC-05/06).** Let a full ~250-reel batch run to completion on Wi-Fi. Confirm: it finishes inside the fresh-URL window, any failures show `ready/failed` counts with retry-failed-only, and no silent zero-byte files (spot-play 5 reels). (P0)
- [ ] **M3. Underground MRT simulation (UAT-OF-01..04).** Enable Airplane Mode. Confirm: cached reels play instantly with zero buffering; an uncached current-week reel shows "Download required — get this week for offline" with a Download Week button (NOT "older bookmarks"); an older bookmark shows "Online required for older bookmarks" with no hang; captions/thumbnails render. Disable Airplane Mode and confirm the older bookmark then resolves and plays. (P0)
- [ ] **M4. 3-state badges (UAT-UI-01/02).** In the bookmarks grid with cached + uncached-current + older bookmarks present, confirm green/orange/globe badges match reality cell-by-cell, and tapping each behaves per §4-D. (P0)
- [ ] **M5. Older-bookmark resolver playback (UAT-BM-03/05/06).** On Wi-Fi, play 3 purged-week bookmarks. Confirm: each resolves and plays in-app; kill Wi-Fi mid-resolve once and confirm an honest error (no spinner forever). If you use a custom Cobalt endpoint/key, confirm it is honored. (P0)
- [ ] **M6. Storage ceiling (UAT-BC-02/03).** In iPhone Settings → General → iPhone Storage, confirm the app holds roughly one week (~2.5–3.5 GB), never two. If the device is nearly full, confirm a pre-wipe "insufficient space" refusal that preserves the old week. (P0)
- [ ] **M7. Unwatched-only empty batch (UAT-BC-04).** With unwatchedOnly on and everything watched, open the download sheet. Confirm Start/Retry are disabled with the "already downloaded or watched" state, and nothing is purged. (P1)
- [ ] **M8. Pause/resume + failed copy (UAT-BC-08).** Pause mid-batch, confirm "Paused — tap to resume" and clean resume. (P2)
- [ ] **M9. Personal-scale sanity.** No interaction should require the Instagram app, an account login beyond your own Apple ID signing, or any cloud/R2/Telegram artifact. App must feel instant on cached content. (P0 on violation)

Record: device model, iOS version, IPA run number, wall-clock batch duration, storage before/after, and any deviation with screenshot.

## 7. Pass/fail criteria for shipping

**SHIP (GO) requires all of:**

1. §3 automated suites green: 3a (23 passed), 3b, 3d clean, 3e tripwire silent; 3c recommended.
2. §3f CI green on `arch/direct-meta-stream` (unit + XCUITest + IPA artifact); the single accepted skip is the AUDIT-005 legacy test under `directMode`.
3. All P0 matrix cases pass (§4-A/B/C/D/E/F): no dead-manifest deploy path, byte preservation holds, offline matrix exact, badges exact, empty-batch safe.
4. All §6 P0 manual items (M1–M6, M9) pass on the physical iPhone.
5. Zero new defects beyond the §5 waiver list; any new crash, data-loss, or wrong-purge observation fails the run regardless of matrix score.

**P1/P2 handling:** a P1 may ship with a dated waiver note appended to §5 (owner: future session); a P2 may ship with §5 listing alone. Waivers never cover §7(5) violations.

**On failure:** record the failing ID, observed vs expected, and environment (§6 record line); fix on the branch; re-run the failed leg plus §3a/§3f before re-grading. Do not bulk-waive P0s at personal scale — each P0 maps to a real commute-day failure (dead feed, wiped cache, stranded offline).
