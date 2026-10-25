# Audit Findings: branch `arch/direct-meta-stream`

Lead-auditor adversarial review against `.workflow/REQUIREMENTS.md` and
`.workflow/IMPLEMENTATION_PLAN.md`. Scope: single-person personal use
(Kedarnath Reddy, offline MRT iPhone playback). No enterprise-bloat
recommendations. Read-only audit: no source files were modified.

- Full machine-readable ledger: `.workflow/AUDIT_LEDGER.json`
  (88 entries, all `PENDING`).
- Counts: **P0: 6, P1: 36, P2: 46.**
- By domain: python_pipeline 32 (2/13/17), swift_engine 27 (2/13/12),
  swift_ui 24 (2/7/15), ci_cd 5 (0/3/2).

## Top risks (read first)

1. `AUDIT-001` — a missing `gh-pages` branch triggers a fresh orphan push
   that wipes production Pages (no `data.json`, no `index.html`).
2. `AUDIT-002` — the 60% deploy quorum is vacuous on writer-produced
   manifests and there is no absolute count floor, so a shrunken week can
   deploy and then wipe the device's good week on Download.
3. `AUDIT-004` + `AUDIT-006` — every rollover deletes all bookmark
   thumbnails, and stale persisted CDN URLs play to a black-screen hang
   with no failure observer (including offline in the MRT).
4. `AUDIT-005` — the headline 1-tap Download Week banner, auto-prompt, and
   progress indicator do not exist in the feed UI.
5. `AUDIT-014` — `~/.env` silently overrides repo `.env`, so a stale
   `DIRECT_STREAM_MODE=false` can flip the branch into legacy mode
   (including the destructive R2 purge) with no startup log.

## P0 (6) — must fix before merge/use

| ID | Domain | Title | Location | Failure in one line |
|---|---|---|---|---|
| AUDIT-001 | python_pipeline | Bootstrap path wipes production gh-pages branch | `site_builder.py:767` | Missing remote branch -> fresh orphan push with only direct files; production Pages goes offline. |
| AUDIT-002 | python_pipeline | No MIN_DEPLOY count gate; 60% quorum is vacuous | `site_builder.py:667` | Writer drops invalid items first, so quorum always passes; shrunken weeks deploy and wipe the good device week. |
| AUDIT-003 | swift_engine | GitHub sync force-unwrap crash on user-supplied repo/ID | `Engine/GitHubBookmarkSyncService.swift:187` | `URL(string: ...)!` on user input crashes on spaces/empty owner (3 sites). |
| AUDIT-004 | swift_engine | Rollover purge deletes preserved bookmark thumbnails | `Engine/MediaCacheManager.swift:276` | Single-extension stem strip never matches `.thumb.jpg`, so every rollover wipes all bookmark thumbnails. |
| AUDIT-005 | swift_ui | 1-tap banner + auto-prompt + progress indicator missing | `InstagramDigestApp.swift:292` | `OfflineCacheBanner` is defined but never embedded; no auto-prompt, no progress, dead header action. |
| AUDIT-006 | swift_ui | Stale persisted CDN URL plays to black-screen hang | `Views/Modals/BookmarksSheet.swift:907` | Expired/offline stored URL plays with no failure observer; bypasses the honest offline badge path. |

## P1 (36) — fix before release

Python pipeline (13):

| ID | Title | Location | Failure in one line |
|---|---|---|---|
| AUDIT-007 | TypeError on non-string candidate | `site_builder.py:103` | Truthy non-string URL field crashes `build_site` mid-write via `in`. |
| AUDIT-008 | Truthy-preference shadows valid fallback URL | `site_builder.py:103` | Invalid `video_cdn_url` hides a valid `video_url` in 4 filter sites; silent reel loss. |
| AUDIT-009 | `--expand` deletes expired reels, saves w/o MIN gate | `main.py:3018` | Routine expand can clobber a 250-item digest down to ~53 items. |
| AUDIT-010 | Sync shrink saved w/o MIN gate when deploy=False | `main.py:2393` | Interactive `--sync` without `--deploy` can persist a 10-item digest. |
| AUDIT-011 | `build_site` clobbers `video_url` with R2 URLs first | `site_builder.py:223` | R2 annotation destroys the only good Meta URL before the direct writer runs. |
| AUDIT-012 | Prune deletes PROD thumbnails on parse failure | `site_builder.py:827` | `except: pass` falls back to direct-only keep-set; prod thumbs 404 while hash check passes. |
| AUDIT-013 | Playwright-discovered CDN URL discarded | `main.py:2149` | Bool-only extractor return drops the fresh URL; downloaded reels then filtered as unplayable. |
| AUDIT-014 | `~/.env` overrides repo `.env`, no mode log | `config.py:45` | Stale home value can silently flip the branch to legacy + R2 purge. |
| AUDIT-015 | Three tests simulate the filter, not production | `tests/test_direct_stream_mode.py:359` | Inline re-implementations stay green if production filters are deleted. |
| AUDIT-016 | Bootstrap test locks in destructive behavior | `tests/test_direct_stream_mode.py:309` | Test asserts the production-wiping path succeeds; correct fix fails it. |
| AUDIT-017 | Migration records FAILED downloads as bookmarks | `scripts/migrate_r2_bookmarks_to_gh.py:87` | Fall-through append + no size check creates ghost release assets. |
| AUDIT-018 | Migration omits thumbnails | `scripts/migrate_r2_bookmarks_to_gh.py:1` | Hardcoded `_portrait.jpg` URLs with no uploads; broken offline thumbs. |
| AUDIT-019 | healthcheck ignores the direct channel | `healthcheck.sh:83` | 200 on `/` passes while `data_direct.json` 404s or is stale. |

Swift engine (13):

| ID | Title | Location | Failure in one line |
|---|---|---|---|
| AUDIT-020 | Duplicate-tap guard covers `.preparing` only | `Engine/DownloadAllCoordinator.swift:130` | Tap during `.downloading` cancels + purges + restarts, losing hours. |
| AUDIT-021 | Preflight overestimates reclaimable bytes | `Engine/DownloadAllCoordinator.swift:100` | Counts preserved Bookmarks/pinned files as reclaimable; batch may not fit. |
| AUDIT-022 | 403/410 refresh race; breaks sticky user pause | `Engine/DownloadAllCoordinator.swift:585` | Concurrent refreshes corrupt the queue; success lifts explicit user pause. |
| AUDIT-023 | Direct remove deletes a bound (playing) file | `Engine/BookmarkController.swift:18` | No active-pool check; unbookmarking the playing reel stalls AVPlayer. |
| AUDIT-024 | Add records `.cached` when video copy fails | `Engine/BookmarkController.swift:91` | Ignored `copyItem` failure still marks cached via the week copy. |
| AUDIT-025 | Cap is not LRU, kills playing video, 0 coerced to 50 | `Engine/BookmarkController.swift:134` | Wrong sort key, no bound-file skip, phantom evictions, and Keep-All deletes past 50. |
| AUDIT-026 | Every URLError maps to `.offline` | `Engine/BookmarkResolver.swift:176` | Bad URLs and cancellations surface as "offline"; hides bugs. |
| AUDIT-027 | Release-Asset video backup missing | `Engine/GitHubBookmarkSyncService.swift:299` | `video_asset_name` recorded but never uploaded; deleted-source reels unrecoverable. |
| AUDIT-028 | Spec conflict: isolated copies vs Plan "no copies" | `Engine/BookmarkController.swift:90` | Plan 4.2 and the Requirements addendum contradict; blocks acceptance. |
| AUDIT-029 | Rollover resume in detached Task (suspension race) | `Engine/MediaCacheManager.swift:211` | Caller drains the queue while suspension may still hold. |
| AUDIT-030 | Ledger decremented before verifying deletion | `Engine/MediaCacheManager.swift:689` | Failed unlink leaves bytes uncounted; 1.5 GB cap drifts. |
| AUDIT-031 | Thumb PUT omits SHA; sync never deletes remotely | `Engine/GitHubBookmarkSyncService.swift:282` | 422 on re-upload hidden; local deletes resurrect on new devices. |
| AUDIT-032 | Swift tests overclaim coverage | `Tests/.../DirectStreamTests.swift:177` | Routing/playback/rollover/coordinator names promise unasserted behavior. |

Swift UI (7):

| ID | Title | Location | Failure in one line |
|---|---|---|---|
| AUDIT-033 | Export share sheet unstable identity | `Views/Modals/BookmarksSheet.swift:329` | New UUID per body pass tears down the share sheet mid-share. |
| AUDIT-034 | 3-state badges never live-update | `Views/Modals/BookmarksSheet.swift:182` | Coordinator not observed; amber badges stick until reopen. |
| AUDIT-035 | `onAppear` wipes finished state | `Views/Modals/DownloadAllSheet.swift:368` | Opening the sheet destroys the green ready badge it should show. |
| AUDIT-036 | Live failed-count missing from progress | `Views/Feed/OfflineCacheBanner.swift:31` | Spec'd "(2 failed)" never renders; engine value not published. |
| AUDIT-037 | Offline playlist narrows silently, never restores | `InstagramDigestApp.swift:742` | No connectivity observation; narrowed list sticks after reconnect. |
| AUDIT-038 | Cloudflare dialog not excised (only gated) | `InstagramDigestApp.swift:141` | Spec demands removal; any flag flip resurrects prompts. |
| AUDIT-039 | iPad popover crash + tmp leak + silent export fail | `Views/Components/ShareSheetView.swift:12` | Missing anchor crashes iPad; base64 backups accumulate; nil export is silent. |

CI/CD (3):

| ID | Title | Location | Failure in one line |
|---|---|---|---|
| AUDIT-040 | CI never runs the Python suite | `.github/workflows/build-ipa.yml:13` | Phase 6 Python tests (~20) never execute in CI. |
| AUDIT-041 | Xcode version not pinned | `.github/workflows/build-ipa.yml:25` | Image rotation silently moves toolchains; non-reproducible IPA. |
| AUDIT-042 | XcodeGen via bare curl, no checksum | `.github/workflows/build-ipa.yml:31` | No `--fail`, no sha check before `sudo install.sh`. |

## P2 (46) — fix opportunistically / before hardening sign-off

Python pipeline (17): AUDIT-043 poll checks `run_date` only
(`site_builder.py:884`); AUDIT-044 no-change path skips sentinel + verify
(`site_builder.py:866`); AUDIT-045 `int()` weakens quorum for n<5
(`site_builder.py:669`); AUDIT-046 writer keeps id-less items, preserves
non-canonical `url`, passes garbage `size_bytes` (`site_builder.py:100`);
AUDIT-047 R2/Worker vars leak into direct HTML (`site_builder.py:425`);
AUDIT-048 health session red in cookie-free mode (`notifier.py:605`);
AUDIT-049 config drift: frozen URL, dead/unvalidated Cobalt var, dead
sentinel constant (`config.py:130`); AUDIT-050 import-time deploy defaults
(`site_builder.py:719`); AUDIT-051 migration nits: CWD path, hardcoded
count/tag, stale docstring (`scripts/migrate_r2_bookmarks_to_gh.py:27`);
AUDIT-052 test split-brain tmp trees
(`tests/test_direct_stream_mode.py:37`); AUDIT-053 zero-playable cites
phantom outbox, post-drop resume set (`main.py:2273`); AUDIT-054 expand log
lies, leftover from pre-filter list (`main.py:3034`); AUDIT-055 top-level
boto3 import (`main.py:32`); AUDIT-056 padded URLs rejected
(`site_builder.py:61`); AUDIT-057 reconcile skips outbox scan on bad digest
(`main.py:566`); AUDIT-058 `.env.example` drift on GH_TOKEN/COOKIE_FREE_MODE
(`.env.example:1`); AUDIT-059 writer leaks `local_url`, duplicates
`video_cdn_url` (`site_builder.py:108`).

Swift engine (12): AUDIT-060 `r2_url`/`local_url` decode fallback
(`Models/ReelItem.swift:130`); AUDIT-061 unsanitized id in fallback URLs
(`Models/ReelItem.swift:52`); AUDIT-062 `_portrait.jpg` vs `{id}.jpg`
mismatch risk (`Models/ReelItem.swift:157`); AUDIT-063 endpoint
unvalidated + snapshotted (`Engine/BookmarkResolver.swift:38`); AUDIT-064
case-sensitive HTTPS check, no host check
(`Engine/BookmarkResolver.swift:162`); AUDIT-065 explicit R2 URL honored in
directMode (`Engine/DigestDataService.swift:68`); AUDIT-066 promote has
lower bound only (`Engine/MediaCacheManager.swift:807`); AUDIT-067 resume
keyed by reelID, pending conflates bookmark copy
(`Engine/DownloadAllCoordinator.swift:413`); AUDIT-068 dead `??`, task
leaks, date-now fallback (`Engine/BookmarkController.swift:101`); AUDIT-069
weak test assertions (`Tests/.../DirectStreamTests.swift:209`); AUDIT-070
no 48h staleness gate (`Engine/DownloadAllCoordinator.swift:586`); AUDIT-071
duplicated manifest-URL contract (`Engine/DigestDataService.swift:18`).

Swift UI (15): AUDIT-072 banned "All N cached" copy in banner
(`Views/Feed/OfflineCacheBanner.swift:48`); AUDIT-073 dead header params
(`Views/Feed/HeaderBarView.swift:9`); AUDIT-074 overlapping `loadManifest`
race (`InstagramDigestApp.swift:575`); AUDIT-075 triple `.task` stale-write
race (`Views/Feed/OfflineCacheBanner.swift:106`); AUDIT-076 fixed 215pt
breaks 9:16 off-iPhone (`Views/Modals/GridView.swift:71`); AUDIT-077
grayscale covers pager only, dual sources (`InstagramDigestApp.swift:252`);
AUDIT-078 sort refires on Settings open+close
(`InstagramDigestApp.swift:492`); AUDIT-079 sync modal gaps
(`Views/Modals/GitHubSyncModal.swift:87`); AUDIT-080 no restore CTA on
empty state (`Views/Modals/BookmarksSheet.swift:169`); AUDIT-081 settings
split-brain persistence (`Views/Modals/SettingsSheetView.swift:15`);
AUDIT-082 sticky import/export banners
(`Views/Modals/BookmarksSheet.swift:146`); AUDIT-083 filtered grid titled
"All Reels" (`Views/Modals/GridView.swift:127`); AUDIT-084 timed
sheet-chaining can drop (`InstagramDigestApp.swift:435`); AUDIT-085
uncancellable share downloads (`InstagramDigestApp.swift:1068`); AUDIT-086
hygiene: ObservedObject-on-singleton, dead inputs, Reachability default
(`InstagramDigestApp.swift:104`).

CI/CD (2): AUDIT-087 `.gitignore` omits xcodeproj/xcresult
(`.gitignore:58`); AUDIT-088 sim-boot flake + no fail-fast on missing .app
(`.github/workflows/build-ipa.yml:55`).

## Verified clean (spot-checked, no finding)

- Phase-5 R2 bypass, startup usage-check gate, `_publish_manifest_to_r2`
  early-return, `_verify_digest` skip, post-publish purge gate,
  `_estimate_run` and reconcile bypasses, `build_site` s3=None, `data.json`
  write gated to legacy, `durable_write_json` atomicity, byte-verify happy
  path; no Telegram/Worker calls in listed Python files.
- ReelItem exact URL preservation + canonicalUrl; direct manifest routing +
  `manifest_cache_direct.json`-only fallback + atomic empty rejection +
  channel-gated bundle fallback; rollover preserves incoming partials +
  SwiftData rows; coordinator concurrency 3, `.part` keyed by pair,
  debounced refresh flag; resolver POST shape, tunnel/redirect accept,
  local-processing/picker reject, 429/401/403 mapping, single retry, no
  persisted resolved URLs; base64 export/import round-trip; new-device
  metadata+thumb restore path; path sanitization and layout.
- No production "Offline N downloaded" pill (test-flag-only); no
  manifest-arrival purge in feed; chronological sort reorders + persists;
  auto-sync on sheet open; overlay teardown cancels tasks/observers.
- Manifest URL strings and cache filename match across Python/Swift;
  `deployed_direct_week.json` fully isolated; `data_direct.json` schema
  (video_url, canonical url, absolute thumbs, size_bytes, run_date,
  channel=direct) confirmed; signing off; distinct bundle IDs/names;
  minimal CI permissions; both test suites exceed the Phase 6 matrix in
  count; no enterprise bloat.

## Method

Four parallel read-only domain audits (python_pipeline, swift_engine,
swift_ui, ci_cd/integration) against REQUIREMENTS.md and
IMPLEMENTATION_PLAN.md, then lead-auditor deduplication (5 overlaps merged)
and spot-verification of all P0s plus sampled P1s against the code
(bootstrap, quorum, force-unwraps, thumb wipe, banner usage, stale-URL
path, coordinator guard, cap coercion, caption optionality, fallback
chain). One subagent claim corrected: the Keep-All (0) coercion is a live
data-loss path (direct mode does write isolated copies), not muted.
