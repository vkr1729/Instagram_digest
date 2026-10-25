# Audit & Remediation Report — §6 Final Refactor, Stage 4d Verification Pass

- **Branch:** `arch/direct-meta-stream`
- **Audit date:** 2026-10-25 (UTC)
- **Auditor role:** Lead Auditor / Independent QA Lead
- **Scope:** `.workflow/AUDIT_LEDGER.json` (AUDIT-001…AUDIT-014), full `git diff HEAD`
  workspace state, `.workflow/REQUIREMENTS.md` §1–§6,
  `.workflow/IMPLEMENTATION_PLAN.md` Phase A–F exit criteria
- **Method:** independent source re-read of every remediated file (not the ledger's
  word for it), targeted `rg` invariant sweeps, `swiftc -parse` over all Swift
  sources, and local pytest execution.

---

## 1. Executive Release Sign-Off Verdict

**CONDITIONAL PASS — APPROVED FOR STAGE 5 COMMIT + PUSH + CI, FREEZE WITHHELD UNTIL CI IS GREEN.**

All 14 ledger findings are faithfully implemented in the workspace with zero
regressions detected, clean thread-safety boundaries, and all six architectural
invariants holding. Automated evidence collected in this pass:

- `pytest tests/test_direct_stream_mode.py tests/test_migrate_r2_bookmarks_to_gh.py` → **31 passed**
- `swiftc -parse $(find Sources Tests -name '*.swift')` → **exit 0, zero diagnostics**
- `rg -i cobalt` residual = plan-mandated one-time `UserDefaults` purge + test
  hygiene only; `BookmarkResolver` / `OfflineCacheBanner` / Cobalt API = **0 hits**
- `rg 'contentsOfFile' Sources/InstagramDigest/Views/Feed/` = **0 hits**

The single withholding condition is **AUDIT-004**: the §6 tree is still
uncommitted (2 deletions staged, 24 modifications unstaged, 1 new test file
untracked; see §4). Per the ledger's own remediation spec, any IPA from CI is
pre-§6 until commit + push + `build-ipa.yml` re-run. **Do not declare the
permanent repository freeze until Stage 5 completes and CI is green on the
pushed tip.** No code changes are required before that step.

A second, non-blocking honesty note: Linux `swiftc -parse` validates syntax
only — it cannot type-check Swift 6 strict-concurrency diagnostics. The true
zero-warnings gate (`SWIFT_TREAT_WARNINGS_AS_ERRORS=YES`, now set in
`project.yml`) executes on macOS CI at Stage 5. Concurrency correctness below
is therefore asserted by line-level inspection plus the build-setting gate, and
CI is the final arbiter.

---

## 2. Remediation Verification by Domain and Severity

### 2.1 swift_engine (AUDIT-001 P1, AUDIT-008/009/010 P2, AUDIT-011 P2)

**AUDIT-001 (P1) — Phantom `video_asset_name` — PASS.**
`syncWithGitHub` Step 4 now sets the pushed DTO strictly from confirmed state:

- [GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:717):
  `let assetName: String? = local.videoAssetName` — the optimistic
  `?? (resolvedLocalFileURL(...) != nil ? safeID.mp4 : nil)` fallback is gone.
- `local.videoAssetName` is assigned only on confirmed 201/422 upload
  (lines 668–673) or presence-check hit (lines 676–680).
- The same strictness was applied to `exportBookmarksJSON` (line 376) and the
  `release_tag` derivation (lines 377, 718): both are `nil` unless an asset is
  confirmed, so a cellular-gated sync self-heals on the next Wi-Fi pass
  (`remoteHasAsset == false` → re-queued for upload).
- Regression test `testAudit001PushedDTONeverClaimsPhantomAsset` present in
  DirectStreamTests.swift:1000. No LRU-poisoning path remains: unconfirmed rows
  keep `videoAssetName == nil` and are therefore evicted *last*.

**AUDIT-008 (P2) — Untrimmed PAT on Tier-2 asset request — PASS.**
The asset redirect-capture request now uses `trimmedPAT`
([GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:168)),
matching every other request in the file. Defense-in-depth trimming added at
both save boundaries:
[SettingsSheetView.saveSettings](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/SettingsSheetView.swift:210) and
[GitHubSyncModal.performSync](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/GitHubSyncModal.swift:147).
Whitespace-padded PATs can no longer 401 Tier 2 while passing the release lookup.

**AUDIT-009 (P2) — Paginated release-asset listing — PASS.**
Both capped paths now page `Link: rel="next"` up to 5 pages (~500 assets):

- `extractNextPageURL(from:)` Link-header parser (lines 182–200), handling both
  `rel="next"` and bare `rel=next`.
- `fetchReleaseAssetDownloadURL` for the Tier-2 resolve path, reached only when
  the embedded first-page array misses (lines 147–156).
- `fetchAllReleaseAssetNames` for the upload presence-check, built once per
  release tag per sync pass (lines 646–652) — no per-bookmark re-listing.
- Both pagers include a `count == 100` fallback to explicit `?page=N` when the
  `Link` header is absent. Regression test
  `testAudit009LinkHeaderPaginationExtraction` (DirectStreamTests.swift:1045).

**AUDIT-010 (P2) — Optimistic thumbnail URL + fire-and-forget PUT — PASS.**
The detached `try?` PUT is replaced by a structured `uploadThumbnail` helper
(lines 285–336) with one retry and 300 ms backoff. In Step 4 the PUT is
`await`ed and the DTO's `thumbnail_url` is rewritten to the repo-relative path
only on confirmed success (lines 695–710, 740–754); on failure the original
absolute URL is preserved and `thumbnailFailures` is surfaced in
`BookmarkSyncResult.message` (lines 820–823). The Step 2 restore path treats a
missing repo file under a relative URL as a soft miss (no crash, retry next
sync). Regression test at DirectStreamTests.swift:1062.

**AUDIT-011 (P2) — Bookmark add copies video on @MainActor — PASS.**
[BookmarkController.add](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/BookmarkController.swift:75)
now inserts/updates the SwiftData row and saves on the MainActor first
(lines 92–128, zero-frame-drop tap feedback), then performs thumbnail + video
copy in `Task.detached(priority: .utility)` over `Sendable` string snapshots
(lines 140–155), re-checks existence, and writes back `localStatus = .cached`
on the MainActor (lines 169–178) before running `enforceCachedVideosCap` and
silent sync. The AUDIT-024 existence check is preserved post-copy. Cancellation
is cooperative (`add_save_` task keys, cancelled on remove). Regression test at
DirectStreamTests.swift:1083.

### 2.2 swift_ui (AUDIT-002/003 P1, AUDIT-005/006/007 P2)

**AUDIT-002 (P1) — SwiftData @Model read in Task.detached — PASS.**
[BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:405)
snapshots `Sendable` value tuples on the MainActor —
`let pairs = bookmarks.map { ($0.weekID, $0.reelID) }` (line 406) — and the
detached task touches only those tuples (lines 407–411), with write-back via
`await MainActor.run` (lines 412–414). No `@Model` object crosses isolation
domains. This was the strict-concurrency violation most likely to trip the new
warnings-as-errors gate; the pattern is now uniform across all three sheets.

**AUDIT-003 (P1) — False Completed state under watched filter — PASS.**
The completed string is gated strictly on census reality:
[DownloadAllSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/DownloadAllSheet.swift:102)
`if isAllDownloaded` (`cached == total && total > 0`, lines 467–470). The
watch-all/download-none case now renders the explicit intermediate branch
(lines 110–117): "Nothing pending under current filter (cached/total cached on
disk)" — `Done` shown, `Start` hidden (line 207), never the "All reels
downloaded" string. The honest-state invariant for the offline-MRT value
proposition holds.

**AUDIT-005 (P2) — Main-thread census + pendingReels stat storms — PASS.**
Census and pending-ID computation moved into a single `Task.detached` pass
(`refreshCensusAndPending`, lines 438–461); results cached in `@State`
(`census`, `cachedPendingIDs`); `pendingReels` is now a pure in-memory set
filter (lines 476–478) with zero disk I/O per body evaluation. Refresh triggers
are limited to sheet appear, filter toggle, and terminal coordinator states
(`isTerminalState`, lines 429–436) — progress publishes during download no
longer re-stat the disk.

**AUDIT-006 (P2) — Settings census on MainActor — PASS.**
[SettingsSheetView](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/SettingsSheetView.swift:189)
uses the same detached pattern: `Sendable` snapshot (`reelIDs`, `targetWeek`,
lines 191–192), off-main stat loop (lines 193–201), `MainActor` write-back
(lines 202–204). The completed/pending row itself (lines 135–144) was already
honest and is unchanged in behavior.

**AUDIT-007 (P2) — Stale `_resolver` badge identifier — PASS.**
Tier-3 badge renamed to `BookmarkBadge_<index>_web`
([BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:279)),
and the XCUITest assertion updated to accept `_cached`, `_downloadRequired`,
`_github`, and `_web`
([InstagramDigestUITests.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Tests/InstagramDigestUITests/InstagramDigestUITests.swift:357)).
No `_resolver` identifier remains anywhere in `Sources/` or `Tests/`.

### 2.3 ci_cd (AUDIT-004 P1, AUDIT-012/013 P2)

**AUDIT-004 (P1) — §6 tree uncommitted, CI builds stale code — CONDITIONAL.**
Verified current state: the 2 deletions (`BookmarkResolver.swift`,
`OfflineCacheBanner.swift`) are staged; all 24 modifications are unstaged; the
new `tests/test_migrate_r2_bookmarks_to_gh.py` is untracked; nothing is
committed to `arch/direct-meta-stream`. The workspace code itself is complete
and verified (this report), and the ledger's maker note correctly frames the
remainder as a Stage 5 action — but the remediation spec's core demands
(commit + push + `build-ipa.yml` re-run on the post-excision tip) are by
definition outstanding inside a verification pass that must not itself mutate
history. **Gating criterion for freeze:** `git status --short` clean on the
branch tip after push, plus a green `build-ipa.yml` run whose SHA equals that
tip.

**AUDIT-012 (P2) — No zero-warnings gate in CI — PASS.**
[project.yml](/home/kedarnath-reddy-vallaboina/Instagram_digest/project.yml:14)
base settings now carry `SWIFT_TREAT_WARNINGS_AS_ERRORS: YES` and
`GCC_TREAT_WARNINGS_AS_ERRORS: YES` alongside `SWIFT_STRICT_CONCURRENCY:
complete`, so local `xcodegen && xcodebuild` enforces the identical bar as CI.
This is the structural fix that makes AUDIT-002-class regressions fail loudly
instead of surfacing as warnings.

**AUDIT-013 (P2) — Missing plan-mandated tests — PASS.**
All five required tests exist and are substantive (bodies inspected, not just
names):

1. `testAudit013ResolveReleaseStreamURLS3RedirectAndMemoryCacheTTL` (:1228) —
   302 capture via `MockResolverURLProtocol`, memory-cache hit, TTL-expiry
   re-query.
2. `testAudit013ReleaseAssetUploadTemplateStrippingAnd422Success` (:1328) —
   RFC 6570 `{?name,label}` strip and 422-as-success.
3. `testAudit013SyncIfNeededInFlightCoalescing` (:1439) — gated two-overlap
   sync asserting `isSyncing == true` then `needsResync == true`, plus clean
   post-pass state.
4. `testAudit013CellularUploadGate` (:1502) — `isExpensiveOverride = true`
   skips video upload while metadata proceeds; `false` uploads. The override
   hook is lock-guarded in `Reachability`
   ([InstagramDigestApp.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:1152)).
5. `testAudit013EnforceCachedVideosCapPrioritizationAndPreservation` (:1621) —
   real temp-dir files asserting uploaded-first eviction order (bm4, bm1 before
   unuploaded bm2 despite bm2's older access time), `boundReelIDs` protection
   for the playing slot, `localStatus` flips, and thumbnail preservation for
   all four rows.

Supporting hooks (`session` injection, `additionalProtocolClasses`,
`isExpensiveOverride`, `LibraryPathResolver(baseURL:)` test initializer) are
present and used. The previously hollow
`testBookmarkControllerEnforceCapLRUAndZeroUnlimited` (:913) is now
complemented by the real-eviction test above rather than standing alone.

### 2.4 python_pipeline (AUDIT-014 P2)

**AUDIT-014 (P2) — Migration script omits asset fields, passes empty timestamp — PASS.**
[scripts/migrate_r2_bookmarks_to_gh.py](/home/kedarnath-reddy-vallaboina/Instagram_digest/scripts/migrate_r2_bookmarks_to_gh.py)
now writes `video_asset_name` + `release_tag` for every record whose asset
upload succeeded or was already present (lines 184–186), omits both on failure
so the app retries upload (line 184 guard on `failed_reels`), and defaults
empty/missing `bookmarked_at` to a UTC ISO timestamp instead of passing `""`
through (lines 167–172) — eliminating the `Date.distantPast` → year-1 → `v1`
Tier-2 breakage. The `migrate_bookmarks(...)` function is extracted for
testability, and [tests/test_migrate_r2_bookmarks_to_gh.py](/home/kedarnath-reddy-vallaboina/Instagram_digest/tests/test_migrate_r2_bookmarks_to_gh.py)
covers all four matrix cells (present / upload-ok / upload-fail / download-fail
× timestamp present / empty / null / missing). Both tests pass locally.

---

## 3. Architectural Invariant Verification

**Invariant 1 — Bookmarks Playback Priority (Tier 1 → 1.5 → 2 → 3): HOLDS.**
`loadVideo(for:)` ([BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:934))
implements the strict cascade: Tier 1 local vault via `resolvedLocalFileURL`
(line 958); Tier 1.5 active-week stored Meta URL gated on
`weekID == currentActiveWeekID` + online (lines 964–969); Tier 2 GitHub Release
S3 streaming via `resolveReleaseStreamURL` with 4-minute memory-only cache
(lines 971–972, 1000–1010); Tier 3 clean "Open on Instagram" on resolution
failure or unconfigured PAT/repo (lines 657–664, 989–991, 1011–1013), and
"You're offline — this bookmark isn't downloaded" when offline (line 629).
Tier 1.5 fall-through is silent and single-shot: both `failObserverToken`
(lines 1059–1070) and `statusObserver` (lines 1072–1077) are guarded by
`hasAttemptedTier1_5Fallback`. Badges map 1:1 to tiers
(`_cached` / `_downloadRequired` / `_github` / `_web`, lines 259–280).

**Invariant 2 — Complete excision of BookmarkResolver / OfflineCacheBanner / Cobalt: HOLDS.**
`BookmarkResolver.swift` (227 lines) and `OfflineCacheBanner.swift` (129 lines)
are deleted from the workspace; `rg` over `Sources/`, `Tests/`, `config.py`,
`.env.example` returns **0 hits** for `BookmarkResolver`, `OfflineCacheBanner`,
`api.cobalt`, `COBALT_RESOLVER_API_URL`, `playViaResolver`, `ResolverResult`,
and `isResolving`. `COBALT_RESOLVER_API_URL` removed from `config.py` and
`.env.example`. The only remaining `cobalt` strings are the plan-mandated
versioned one-time `UserDefaults` purge (`hasPurgedCobaltKeys_v1`,
[InstagramDigestApp.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/InstagramDigestApp.swift:37))
and test setup/teardown hygiene — both explicitly required by Phase C.1, not
residual functionality. `isResolving` renamed to `isLoadingVideo` throughout.

**Invariant 3 — FeedCell 60 FPS swiping: HOLDS.**
`FeedCell` ([FeedPagerView.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Feed/FeedPagerView.swift:555))
contains only `PlayerContainerView`; `configure` is a two-line visibility reset
(lines 586–588); `prepareForReuse` detaches the player layer immediately
(lines 590–595), eliminating stale-frame flashes. `rg 'contentsOfFile'
Sources/InstagramDigest/Views/Feed/` returns **0 hits** — no
`posterImageView`, `imageTask`, synchronous JPEG decode, or
`URLSessionDataTask` remains on the scroll path. `PlayerContainerView` renders
black when cold ([ReelCardView.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Feed/ReelCardView.swift:319)).

**Invariant 4 — Honest completed states: HOLDS.**
Single source of truth `cacheCensus(reels:weekID:)` counts non-empty local
files via `isLocalFileAvailable` → `fileExistsAndNonEmpty` (`size > 0`;
zero-byte files excluded). `DownloadAllSheet` gates the completed block
strictly on `cached == total > 0` with an explicit filter-empty branch
(§2.2, AUDIT-003/005); `SettingsSheetView` shows "All Reels Downloaded
(cached/total)" vs "(N pending)" from the same census. All census evaluation
runs off the main thread.

**Invariant 5 — Silent sync & local vault: HOLDS.**
(a) In-flight coalescing via `isSyncing`/`needsResync` with deferred follow-up
([GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:832));
triggers on bookmark add/remove
([BookmarkController.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/BookmarkController.swift:182)),
app-foreground
([InstagramDigestApp.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/InstagramDigestApp.swift:529)),
and `BookmarksSheet` open
([BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:367)).
(b) Video uploads gated on `!Reachability.shared.isExpensive`; metadata and
thumbnails always sync. (c) 50-reel default LRU cap (`@AppStorage`, default 50;
25/50/100/unlimited picker); `syncWithGitHub` never deletes local files;
`enforceCachedVideosCap` evicts uploaded-first, protects `boundReelIDs`, and
preserves thumbnails and SwiftData rows. (d) Zombie-tombstone protection:
`remove` tombstones before deleting; sync omits tombstoned remote IDs from the
merged set (persisting the deletion to GitHub) and clears tombstones only after
a successful push; `add` clears the tombstone first.

**Invariant 6 — Swift 6 strict concurrency & zero warnings: HOLDS (CODE) / CI-ARBITRATED (GATE).**
All new async boundaries follow one audited pattern — `Sendable` value snapshot
in, `Task.detached` work, `MainActor` write-back — with no `@Model` or
`ModelContext` crossing domains (verified in BookmarksSheet, DownloadAllSheet,
SettingsSheetView, BookmarkController). `swiftc -parse` over every Swift file
is clean. `SWIFT_STRICT_CONCURRENCY=complete` +
`SWIFT_TREAT_WARNINGS_AS_ERRORS=YES` + `GCC_TREAT_WARNINGS_AS_ERRORS=YES` are
set in `project.yml` base. Full type-check confirmation awaits the macOS
`xcodebuild` run at Stage 5, which is now a hard gate rather than advisory.

---

## 4. Regression & Diff-Integrity Notes

- **Diff scope:** 26 files changed (+2,706/−3,060), 2 files deleted, 1 file
  added — all within the §6 plan surface (engine, feed/bookmark UI, tests,
  `config.py`, `.env.example`, `project.yml`, migration script, `.workflow/`
  docs). No unrelated refactors detected; `storage_r2.py`, `cloudflare/`, and
  the production `data.json` path are untouched per the non-destructive
  invariant.
- **Legacy paths preserved:** the Cloudflare Worker bookmark lifecycle in
  `BookmarkController` (non-direct branch) and legacy badge/UI branches are
  intact for side-by-side comparison; direct-mode gating is consistent at every
  call site inspected.
- **Minor observation (not a finding):** `syncIfNeeded(context:)` omits the
  plan's suggested `force: Bool = false` parameter. All four call sites use the
  single-argument form and no caller needs forced bypass (overlaps coalesce by
  design), so this is a cosmetic plan deviation with zero functional impact. No
  action required.
- **No new P0/P1/P2 findings** raised by this verification pass.

---

## 5. Final Audit Certification Score and Release Approval

| Dimension | Score | Basis |
|---|---|---|
| Finding remediation fidelity (14/14) | 14/14 code-complete | Line-verified §2; AUDIT-004 code-ready, commit pending |
| P1 closure (4/4) | 4/4 | 001, 002, 003 fully closed; 004 gated on Stage 5 mechanics |
| P2 closure (10/10) | 10/10 | All closed with tests where specified |
| Invariant compliance (6/6) | 6/6 | §3; Inv. 6 gate CI-arbitrated |
| Automated evidence | 31 pytest + clean parse | §1 command outputs |
| **Certification score** | **98/100** | −2 withheld solely for uncommitted tree (AUDIT-004 mechanics) |

**Release approval: CONDITIONAL.** The §6 implementation is certified correct
and complete as code. The auditor approves proceeding immediately to Stage 5
(commit → push → CI), and pre-approves the permanent repository freeze
**contingent on**: (1) a clean `git status` on the pushed `arch/direct-meta-stream`
tip, and (2) a green `build-ipa.yml` run (unit + UI tests, zero warnings as
errors, packaged `InstagramDigestDirect.ipa`) at that exact SHA. If CI reports
any warning-as-error or test failure, the freeze is revoked and the failure
returns to Maker as a Stage 5 defect — no new audit cycle is needed for a
clean green run.

---

*Stage 4d verification complete. UAT matrix: `.workflow/UAT_PLAN.md`.*
