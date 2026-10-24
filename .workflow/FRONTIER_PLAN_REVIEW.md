# Frontier Plan Review — Zero-Cloud / Direct-Meta Stream Architecture

Date: 2026-10-24 (UTC) · Scope: `.workflow/REQUIREMENTS.md` + `.workflow/IMPLEMENTATION_PLAN.md`, checked against the current Python and Swift implementations.
Scale anchor: single-person personal use. Prefer small changes to the existing pipeline/coordinator, truthful degraded states, and no new owned infrastructure. This review supersedes the prior R2-primary review formerly in this file (preserved in git history).

## Verdict

**Not implementation-ready.** The weekly offline concept is viable and much of the download engine already exists, but the plan currently has four release blockers:

1. Direct mode does not bypass the full R2 call graph and the existing Pages deploy can overwrite/delete the supposedly untouched production channel.
2. A successful workstation download does not prove that the manifest's saved CDN URL is fresh or that `size_bytes` describes that same representation.
3. Existing rollover and permanent-bookmark behavior contradicts the new cache lifecycle; the proposed resolver is wired to a controller that does not own playback.
4. Public Cobalt is an unproven dependency. Its official docs require explicit permission for third-party hosted-instance use and document bot protection/auth; response handling is broader than “extract a URL.”

A live read-only probe on 2026-10-24 found `GET https://api.cobalt.tools/` returns 200, Cobalt 11.7.1, advertises `instagram`, and includes a `turnstileSitekey`. This proves only instance metadata availability, **not** unauthenticated POST resolution or playable output on iOS.

## Existing assets to reuse, not rebuild

- `ReelItem` already decodes absolute HTTP(S) `video_url` through `URL(string:)`, preserving the original string's query, percent escapes, signing tokens, and fragments; `size_bytes`/`sizeBytes` are already supported (`ReelItem.swift`, `DigestManifest.swift`). Add regression tests, not a new URL model.
- `DownloadAllCoordinator` already has bounded concurrency, thermal/playback adaptation, retry accounting, persisted resume data, storage preflight, thumbnail prefetch, and `.part` staging.
- `MediaCacheManager.promotePartFile` already performs non-zero/size validation and atomic promotion. It lacks the promised playability check; extend it rather than adding a second promotion path in the coordinator.
- `AVPlayerPool` already prefers local files. `DigestManifest` already decodes `generated_at`, which can support freshness UI without a new manifest date field.
- Existing `CoordinatorState` and `overallProgress` already drive download UI. Do not maintain four independent progress sources unless the banner exposes a genuine missing state.

## P0 — Architectural and dependency blockers

### P0-1. Make Cobalt feasibility and its exact contract a Phase 0 gate

**Pitfall:** The plan assumes `api.cobalt.tools` is an anonymous, stable app backend. Cobalt's official API docs say hosted instances use bot protection and are not intended for other projects without explicit permission. Auth may be `Api-Key` or short-lived `Bearer`; the live instance advertises Turnstile. A generic iOS URLSession client cannot assume it will pass that challenge.

**Contract gaps:** Successful responses can be `tunnel` or `redirect` (`url`, `filename`), but also `local-processing` (one or more `tunnel` URLs requiring local remux/transcode), `picker` (an array of media), or `error` (`error.code`, optional context). A `local-processing` response is not a single playable MP4 URL. HTTP 401/403, bot-protection HTML, 429, 5xx, malformed JSON, and an unplayable returned URL need distinct outcomes. `.expired` is not a meaningful generic Cobalt error: expiry belongs primarily to the original Meta URL and should trigger resolution.

**Concrete enhancement:**
- Before downstream implementation, obtain explicit instance access/auth or record old-bookmark playback as intentionally best-effort/unavailable. Run one manual canary from a physical iPhone: canonical Instagram reel URL → POST → parse → play the returned media in `AVPlayer`. Do not put live public-API calls in CI.
- Inject `URLSession`, endpoint, and sleeper into `BookmarkResolver`; use an ephemeral session with `waitsForConnectivity = false` and bounded request/resource timeouts. Accept only HTTPS returned URLs.
- Handle `tunnel`/`redirect`; explicitly reject `local-processing` as unsupported. For `picker`, select a video only when the choice is unambiguous, otherwise return `.unplayable`.
- Map offline/timeout, auth-required, rate-limited, service-unavailable, invalid-response, and unplayable separately. Retry at most once only for 429 or transient network/5xx; honor `Retry-After` and Cobalt's `RateLimit-*` headers with a small cap. Never retry auth failures or unsupported responses. Treat reachability as a hint; URLSession errors remain authoritative.
- Do not persist resolved tunnel URLs in SwiftData or the manifest. Resolve per playback attempt and cancel stale resolutions when the selected bookmark changes.

If full feature parity is mandatory, failure of this gate blocks release. Otherwise ship weekly offline first and label older bookmarks honestly rather than hiding an unreliable resolver behind indefinite loading.

### P0-2. `DIRECT_STREAM_MODE` must gate the whole pipeline, not three Phase 5 methods

**Pitfall:** With existing R2 credentials still present, the proposed bypass still performs R2 operations in:
- Startup usage validation (`main.py:956-960`).
- Archive discovery (`site_builder.py:145-157` calls `get_s3_client`/`list_objects_v2`).
- Current-week key listing, upload/outbox handling, persisted `r2_url` assignment, and post-upload verification (`main.py:2261-2406`).
- Post-publish R2 purges and R2 manifest publication (`main.py:2431-2432`, `_publish_digest`).
- Email storage reporting (`main.py:2488-2495`).
- Expand, reconcile, estimate, build/deploy-only paths (`main.py:2808-2934`, `2991-3040`). Expand unconditionally lists/uploads even when the preflight quota block is skipped.

**Concrete enhancement:** Define a direct-mode branch at each public entry point, not just inside Phase 5D/E:
- Full sync: skip all cloud reads/writes while retaining local download, validation, shortfall gates, and local retention. Do not pass an R2 URL map into `build_site`.
- Site builder: direct mode must perform zero S3 calls; archive availability should use local data only.
- Expand: integrate successfully downloaded reels without upload and preserve their direct source URLs. Reconcile/R2-estimate should either get a small direct equivalent or fail fast with “unsupported in DIRECT_STREAM_MODE”; never fall through to R2.
- `_build_only`/`_deploy_only`: compile/publish the direct channel without calling `_publish_manifest_to_r2`.
- Keep the legacy branch unchanged, including its mandated pre-upload purge behavior. Pin legacy tests to `DIRECT_STREAM_MODE=False` and direct tests to `True` so the new default does not silently change existing test semantics.

Use existing `config._env_bool("DIRECT_STREAM_MODE", True)` and document the variable in `.env.example`. Compute the direct manifest path from `SITE_DIR` at call time (tests monkeypatch `SITE_DIR`); a constant captured at import will point tests and custom runs at the wrong location. There is no current `export_data_json()` function; name the actual pure writer/helper in the plan.

**Misplaced bypass:** `notifier.py` handles SMTP email, not Telegram initialization. Preserve completion/failure email. The actual Worker/Telegram paths are in Swift (`DigestDataService`, `BookmarkController`, `FeedMainView`, `BookmarksSheet`) and must be bypassed there.

### P0-3. The current build/deploy flow cannot preserve production `data.json`

**Pitfall:** `build_site()` always writes `site/data.json` (`site_builder.py:469-476`), mutates/renders R2 URLs, and prunes share/thumbnail assets. `deploy_to_gh_pages()` then creates an orphan branch and force-pushes the entire `site/` directory. Omitting `data.json` would delete it remotely; regenerating it would replace it. The deploy guard and poll also hard-code `data.json` (`site_builder.py:541-562`, `608-612`, `650-687`). Merely “also write `data_direct.json`” violates the isolated benchmarking invariant.

**Concrete enhancement:**
- Add a pure direct-manifest writer that copies items, sets `video_url` from the validated direct source, removes `r2_url`, preserves the canonical reel `url`, `size_bytes`, `generated_at`, `run_date`, and `count`, and writes only `data_direct.json`. It must not call the legacy renderer, S3, asset pruner, or production manifest writer.
- Give direct mode a small Pages deploy path based on the existing `gh-pages` content: overlay only `data_direct.json` and required new direct thumbnails/assets, commit without deleting other files, and push. Keep the legacy orphan deploy for legacy mode. At minimum, snapshot and byte-compare the production `data.json` before/after direct deployment; absence is also failure.
- Poll `DIRECT_PAGES_MANIFEST_URL`, not `/data.json`; validate `run_date`, non-empty/count-consistent items, and several exact direct URLs. Write a separate `deployed_direct_week.json` only after that verification; do not change the meaning of legacy `deployed_week.json`.
- Use absolute GitHub Pages thumbnail URLs in JSON. A literal `/thumbnails/{id}.jpg` is rejected by the app's absolute-URL decoder and triggers its `_portrait.jpg` fallback.
- Treat the direct manifest as public bearer URLs until expiry: do not commit it to the source branch, print signed URLs in logs/emails, or bake it into test fixtures. The Pages-only deployment is compatible with the personal-use threat model, but this exposure should be explicit.

### P0-4. Bind each manifest URL and `size_bytes` to the exact validated representation

**Pitfall:** `download_reel_video` tries the saved `video_cdn_url`, then can succeed via yt-dlp (`extractor.py:1602-1684`) without returning the actual source URL. Direct mode would then publish the original empty/stale URL despite a valid local file. Yt-dlp can also select/remux a different representation, making the local file's `size_bytes` inaccurate for the iOS download. Existing local files can be reused without proving their saved URL still works.

**Concrete enhancement:**
- Preserve the legacy boolean downloader API, but give direct mode a result path that records the actual direct source URL and byte count. Publish an item only when that exact URL/representation was validated; otherwise re-resolve and validate it, or omit/mark the item unready. Never let a yt-dlp-only success silently certify the stale saved URL.
- Include a manifest generation timestamp and sample-test URLs from the actual client stack. Phase 0 should verify on physical iOS that both URLSession download and AVPlayer can fetch the Meta URL without workstation cookies, Referer, client-hint headers, or a custom User-Agent, including normal GET and byte-range behavior.
- Keep the existing deploy shortfall gate after direct-source validation so a batch of locally valid but client-unplayable items cannot replace a healthy direct manifest.

### P0-5. Rollover currently happens at the wrong time and is not coordinated with writers

**Pitfall:** `FeedMainView.loadManifest()` already changes `AppState.currentWeekID` and asynchronously purges the old week as soon as a new manifest arrives (`InstagramDigestApp.swift:570-589`). Adding the proposed download-time purge leaves two purge triggers and wipes offline media before the user taps Download. Current purge also preserves LivePinSet/bookmarked files, so it does not guarantee the new semantics.

`startDownloadAll` currently preflights before any purge; available space therefore excludes reclaimable old-week bytes and can produce a false insufficiency result. A new async purge also needs reentrancy protection against double taps, URLSession completion callbacks, thumbnail prefetch, and active AVPlayer reads.

**Concrete enhancement:** Make rollover one coordinator-owned transaction:
1. Validate the new batch and enter a visible `preparing` state that disables duplicate starts.
2. Pause old playback, cancel old-week downloads/thumbnail work, and generation-fence late callbacks.
3. As the first disk mutation for a new week, delete every previous week directory (MP4, `.part`, thumbnails) and old resume data, except the incoming week. Do not delete the manifest or SwiftData bookmark metadata. Surface deletion failures rather than claiming the cap.
4. Recompute storage and start the new queue. Same-week retries must retain ready current-week files.

Remove/gate the manifest-arrival purge; `AppState` may still roll metadata independently. Preflight may estimate projected post-purge capacity to avoid false failures, but must not reintroduce two-week overlap or defer the mandated wipe until after download.

### P0-6. Bookmark storage and playback routing contradict the new lifecycle

**Pitfall:**
- `BookmarkController.add` always calls `keepBookmarkOffline`, creating a permanent isolated `MediaCache/Bookmarks/{id}.mp4`, then performs Worker backup. `computeLivePinSet` preserves those copies across rollover. This violates “references only,” the ~3 GB total footprint, and older-bookmark resolver behavior.
- `BookmarkController` owns add/remove, not playback. Actual playback is `BookmarkPlayerOverlay.loadVideo()` (`BookmarksSheet.swift:677-727`), which checks only the isolated bookmark copy and otherwise plays the stored expiring URL. Sharing independently downloads that stale URL (`BookmarksSheet.swift:787-809`). Step 4.2 alone would leave both untouched.
- Remote sync remains in `FeedMainView` and pending retry/UI code, so changing the controller does not eliminate Cloudflare calls or prevent remote rows from reappearing.
- `BookmarkItem` stores a signed `videoUrlString`, not necessarily the canonical Instagram permalink required by the new model. `ReelItem` currently discards the manifest's canonical `url`.

**Concrete enhancement:**
- In direct mode, stop remote fetch/save/delete/retry calls and stop creating isolated permanent bookmark files. Keep the legacy code intact but unreachable. Remove Worker owner-key, backup-pending, Telegram, and 1.5 GB bookmark-storage UI from the direct experience.
- Add a one-time device migration: delete legacy isolated bookmark MP4s and stale resume data, reset derived local status, and clear obsolete pending-backup UI state without deleting bookmark metadata. Treat legacy rows with unknown week as older bookmarks.
- Persist the canonical reel permalink (optional/defaulted SwiftData field for lightweight migration) plus existing `weekID`; do not confuse it with the signed stream URL. The resolver may construct the permalink from a validated reel ID, but sharing should use the canonical permalink or a freshly resolved URL.
- Put local-first/remote-resolution in one small playback-resolution path used by `BookmarkPlayerOverlay` and sharing. Check the feed file for `bookmark.weekID` first; only older/missing media invokes Cobalt. Add cancellation/generation checks so a slow resolve for bookmark A cannot start playback after the user selects B.
- Replace the current synchronous “create player and hope” flow with resolving/ready/error states and retry/close actions. Offline older bookmarks must show the required message without creating an AVPlayer spinner.

## P1 — Correctness gaps and sequencing risks

### P1-1. Purging alone does not enforce a ~3 GB cap

The legacy workstation budget remains 5.8 GB, permanent bookmarks can add 1.5 GB, and completed files coexist with up to three in-flight downloads, resume blobs, and thumbnails. Current iOS preflight checks only available device space, not a direct-mode batch ceiling.

**Enhancement:** Define one direct batch budget (including a small documented in-flight/thumbnail margin), reject or trim a manifest whose total exceeds it, and keep the Python direct budget consistent. Enforce actual downloaded-byte limits during URLSession callbacks; unknown `size_bytes` must use a conservative allowance, not an unbounded download. Include reclaimable old-week bytes in preflight math and re-check after purge.

### P1-2. “Resumable” and “background” semantics are underspecified

The coordinator uses `URLSessionConfiguration.default`, suspends on backgrounding, and keeps queue/counts in memory. Resume data is keyed only by `reelID`, not `(weekID,reelID)` (`LibraryPathResolver.resumeDataFileURL`), so a stale blob can cross weeks or a same-week URL refresh. `isIdleTimerDisabled` only helps while foregrounded.

**Enhancement:** Choose and document one contract:
- **Small/default:** foreground, screen-awake downloads with reconstruction after relaunch. Change “background download” copy accordingly.
- **If true background completion is required:** use a named background URLSession, relaunch/event handling, persisted task-to-`(week,reel)` mapping, and `urlSessionDidFinishEvents`; this is a separate prerequisite, not a banner-only change.

In either contract, derive ready state from final files plus manifest, persist only minimal batch state (incoming week and failed/pending IDs; downloading becomes pending on launch), and include week in resume-data identity or delete it during rollover/manifest refresh. Ready counts must include files completed before app relaunch.

### P1-3. Manifest routing needs channel-isolated fallbacks

Changing only `defaultManifestURL` leaves `manifest_cache.json`, bundled `Resources/data.json`, and remote-bookmark behavior tied to the legacy R2 channel. If implementers instead replace `primaryManifestURL`, the existing network fallback can also load production `data.json`.

**Enhancement:** For the direct channel, use only `data_direct.json` → `manifest_cache_direct.json` → an explicitly direct UI-test fixture. Never fall back to R2, production `data.json`, or the legacy bundled manifest. Reject an empty decode when the payload claims items and fall back to the last good direct cache. Test that a fresh valid direct response atomically replaces cache while a failed/empty response leaves it intact. The existing URL model needs no `isDirectMetaStream` behavior switch.

### P1-4. URL refresh needs a closed loop between workstation, manifest, and queue

“If links expire, workstation refreshes” has no command, timing, or client recovery contract. `--build-only` cannot invent fresh CDN URLs from an old digest. The coordinator currently retries 5xx but marks 403/404/410 failed without refreshing the manifest.

**Enhancement:** Document the scoped workstation metadata-refresh command/path and publish a new `generated_at`. On 403/410 (and chosen 404 policy), discard stale resume data, perform one debounced direct-manifest refetch, and rebuild only pending/failed items with matching IDs and fresh URLs; preserve ready files. Bound this to one refresh per batch/generation to avoid loops. Show `Refresh required` if no newer manifest exists.

### P1-5. Banner and offline indicators need truthful, derived states

- Home composition lives in `FeedMainView` (`InstagramDigestApp.swift`), not `FeedPagerView`, which is only the UIKit pager. Put the banner where manifest and sheet state already live; reuse `DownloadAllSheet` for retry/details.
- The existing sheet defaults to unwatched-only. A one-tap “Download Week” matching the requirement must download all 250 or explicitly say “Download unwatched”; never show full-week ready after a filtered batch.
- `totalInBatch` currently counts only pending items. Banner progress must be `ready across the full intended set / intended total`, with explicit partial failure (`247/250 ready; 3 failed — Retry`), not a green badge merely because the queue drained.
- Bookmark badges currently use `BookmarkItem.localStatus` for isolated copies. Derive three states from actual feed-file existence and week: `Offline ready`, `Download required` (active week, uncached), and `Online required` (older, uncached). Update when files complete/purge, not only when the sheet opens.
- The feed's offline filter currently returns without changes when zero local items exist, leaving remote playback to stall. Add an empty offline state with Download/Retry; observe the existing Reachability monitor for transitions instead of adding a second monitor. Apply the same honest error treatment to active uncached reels, not just older bookmarks.

### P1-6. Plan test seams before implementation

Destructive purge and download tests are unsafe/difficult with the current singletons: `LibraryPathResolver` has no injectable base directory, and `DownloadAllCoordinator` creates its own private session. Add only the necessary injection points: a resolver base URL, coordinator session/path/state injection, and resolver session/endpoint/sleep injection. Reuse the existing `URLProtocol` stub pattern. Atomic promotion tests belong to `MediaCacheManager`, not `DownloadAllCoordinator`.

The project uses XcodeGen and iOS `xcodebuild`, not `swift test`: CI runs `xcodegen generate`, then `xcodebuild test` with `-only-testing:InstagramDigestTests` and a separate XCUITest invocation. Include the new Swift source through the existing `Sources/InstagramDigest` target and keep public Cobalt/Meta network tests out of CI.

## P2 — Over-engineering to trim

- **No new bulk engine:** adapt `startDownloadAll` and its existing delegate/state machine; do not create a parallel `startBulkDownload` implementation.
- **No duplicate progress sources:** extend `CoordinatorState` or expose one small progress value consumed by both sheet and banner. Avoid independently mutable `readyCount`, `totalCount`, `failedCount`, `isDownloading`, `state`, and `overallProgress` drifting apart.
- **No URL rewrite/helper unless behavior needs it:** exact signed-URL preservation already exists. If host classification is truly needed, use `host == "fbcdn.net" || host.hasSuffix(".fbcdn.net")`; `contains("fbcdn.net")` also accepts lookalike hosts. Do not use the helper to bypass normal HTTPS/playback validation.
- **No generic resolver framework:** one actor with an injected session and a small response enum is enough. No plugin registry, resolver chain, persistent resolved-URL cache, or parallel speculative retries.
- **No broad Python abstraction:** a pure direct-manifest writer plus explicit direct branches at existing entry points is smaller and safer than refactoring the whole materialization pipeline.
- **No extra persistent ledger duplicating filesystem truth:** final MP4s are ready state; persist only week/failure/retry information needed after relaunch.

## Required test matrix

### Python (`tests/test_direct_stream_mode.py` plus focused existing suites)

1. **Full call-graph bypass:** run direct sync with R2 credentials present and every S3/R2 method mocked to raise; cover startup, build, publish, post-publish, expand, and guarded reconcile/estimate paths. Assert local download/validation still occurs and SMTP remains usable.
2. **Legacy parity:** with direct mode false, existing R2 ordering tests still pass unchanged semantically (including the mandated pre-upload purge).
3. **Manifest correctness:** exact signed URL round-trip with `&`, `=`, `%2F`, and fragment; canonical reel URL retained; no `r2_url`; absolute Pages thumbnails; real `size_bytes`; `run_date`/`generated_at`/count correct; caller's digest not mutated.
4. **Freshness/source binding:** stale saved CDN + yt-dlp-only success cannot publish the stale URL or wrong byte count; reused local files still require a valid direct source before publication.
5. **Channel isolation/deploy:** sentinel production `data.json` and legacy assets remain byte-identical after direct build/deploy; only direct files are overlaid; poll targets `data_direct.json`; separate direct deployed-state write occurs only after verified serving.
6. **Safe entry points:** `--build-only`, dry-run, expand, and deploy-only have explicit direct behavior and no accidental production writes. Run only mocked pytest tests, never a bare production sync.

### Swift unit tests (`DirectStreamTests.swift` or focused existing files)

1. **Models/data service:** signed URL exactness, `size_bytes`, direct-only network/cache/bundle routing, legacy-cache rejection, non-empty valid cache replacement, corrupt/empty response fallback.
2. **Rollover transaction:** temp-root old/new weeks with MP4, `.part`, thumbnails, resume data, legacy bookmark MP4s, manifest, and SwiftData rows; assert old media is deleted before the first new request, incoming ready files survive, metadata survives, and failures/double taps cannot start overlapping batches.
3. **Promotion/resume:** valid tiny MP4 promotes; HTML/zero-byte/unplayable/undersized files never replace a good final file; actual-byte ceiling enforced; relaunch reconstructs ready/failed counts; resume data cannot cross week or refreshed URL generation.
4. **Cobalt contract:** `tunnel`, `redirect`, `local-processing`, `picker`, structured `error`, 401/403/auth codes, 429 headers, 5xx, HTML/malformed JSON, non-HTTPS URL, offline/timeout, cancellation, and proof that retry/backoff is bounded without real sleeps.
5. **Bookmark routing:** active-week local hit performs zero resolver calls; older item resolves; offline older item performs zero network request and shows the required state; stale async completion cannot replace the currently selected bookmark; share does not use an expired signed URL.
6. **UI smoke:** fresh-week banner → partial progress/failure → full ready; relaunch restores counts; zero-cached offline feed shows Download required; bookmark badges change after completion/purge. Keep XCUITest coverage to these high-value flows and use deterministic seams rather than repeatedly tuning timing-sensitive tests.

## Dependency-safe implementation order

0. **Feasibility gates:** physical-device Meta URL GET/range + AVPlayer canary; Cobalt permission/auth + POST + playback canary. Decide whether older-bookmark parity is a release blocker or an explicit degraded feature.
1. **Freeze contracts/tests:** direct manifest schema/channel, post-purge budget, background wording, bookmark states. Add config example and test seams; pin legacy tests to legacy mode.
2. **Python direct path:** pure direct writer, actual-source/byte binding, full R2 bypass at every entry point, production-preserving direct Pages deploy and verification. Run mocked Python CI before Swift depends on the manifest.
3. **Swift manifest/bookmark cloud bypass:** direct-only network/cache routing, canonical bookmark permalink, disable all Worker/Telegram/pending-backup paths, and migrate legacy device bookmark media/state.
4. **Storage and queue:** single download-time rollover transaction, direct byte budget, playability promotion, week-scoped resume/relaunch reconstruction, and the chosen foreground/background contract.
5. **Playback and UI:** bookmark local-first resolver integration, refresh-on-expiry loop, banner derived from queue/filesystem, three bookmark states, and empty/offline feed errors.
6. **Verification:** focused Python tests, existing legacy regression suite, XcodeGen + iOS unit tests, minimal XCUITest smoke, then one physical-device fresh-week download and MRT-style offline check. Do not declare completion from decoder tests alone.

## Sources

- [Cobalt official API documentation](https://github.com/imputnet/cobalt/blob/main/docs/api.md) — hosted-instance warning, authentication, request/response variants, and rate-limit headers.
- [Cobalt public instance metadata](https://api.cobalt.tools/) — read-only live capability probe described above; not evidence of POST permission or playback success.
