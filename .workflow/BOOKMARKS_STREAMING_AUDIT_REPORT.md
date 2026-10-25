# BOOKMARKS STREAMING & RELEASE TAG AUDIT REPORT

- **Branch:** `arch/direct-meta-stream`
- **Commit under audit:** `f8a4488` ("fix(bookmarks): support explicit MIME override, release tag candidates with latest fallback, and on-demand local caching")
- **Base:** `02b128f` (diff: `git diff 02b128f HEAD`, 3 files, +215/−32)
- **Auditor role:** Lead Frontier Auditor (adversarial)
- **Date:** 2026-10-25 (UTC)
- **Method:** full diff re-read; independent re-read of both changed source files (not the brief's word for it); call-site sweep for every changed symbol; invariant check against `.workflow/AUDIT_LEDGER.json` (AUDIT-001…AUDIT-014) and the §6 six-invariant list from `.workflow/AUDIT_AND_REMEDIATION_REPORT.md`; `swiftc -parse` over all three changed files (exit 0); Apple/SDK documentation verification for the AVFoundation MIME keys via external sources. No Apple-SDK compile was possible on this Linux host, so type-level strict-concurrency diagnostics and AVPlayer runtime behavior are asserted by inspection plus required CI/device gates below.

---

## 1. Executive Verdict (PASS / CONDITIONAL PASS / FAIL)

**CONDITIONAL PASS — merge-blocked until the two P1 remediations (§5, F-P1-1 and F-P1-2) land and CI + on-device playback proof are green.**

The core approach is correct and well-targeted at the reported symptom:

- `AVURLAssetOverrideMIMETypeKey` is genuine **public** AVFoundation API whose documented semantics ("only the specified MIME type is considered… URL path extension or server-provided MIME type is ignored") exactly match the extension-less `application/octet-stream` release-asset URL problem. This is the right fix, not a hack.
- Candidate tags (`bookmark.releaseTag` → `v{year}` → `{year}`) plus `/releases/latest` fallback directly addresses the 54-of-55 missing-`release_tag` population.
- `parseDate` no longer yields `Date.distantPast` (year 1 → tag `v1` → permanent 404), closing the AUDIT-014 client-side tail.

But the diff ships two P1 defects: an **AUDIT-002-class strict-concurrency regression** (`Task.detached` capturing non-Sendable `self`/`ModelContext`/`[BookmarkItem]`) that the now-relaxed CI gate cannot catch, and a **deterministic breakage of the existing AUDIT-013 TTL test** via cache-key format drift. Neither is architectural; both have small, specified fixes. Three P2s (candidate-shadowing on asset-miss, stale-callback player clobbering, cellular-gate bypass) should also be fixed before merge. The twelve P3 hardening items may follow.

**Gating criteria for sign-off:** (1) F-P1-1 + F-P1-2 fixed; (2) `build-ipa.yml` unit + UAT green on the pushed tip (this also proves the `#available(iOS 17.0, *)` guard on the MIME key compiles under the pinned Xcode 16.4 SDK — unverifiable from Linux); (3) on-device reproduction proof that a legacy bookmark which previously spun 1–2 s then dropped to Tier 3 now plays Tier 2 (no unit test can assert AVPlayer parsing behavior).

---

## 2. Concurrency & Concurrency Safety Verification

### 2.1 F-P1-1 (P1, Invariant 6 regression): `cacheVideoOnDemand` captures non-Sendable state across `Task.detached`

[BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:1131) (`BookmarkPlayerOverlay.cacheVideoOnDemand`, lines 1131–1163):

```swift
Task.detached(priority: .utility) {
    ...
    await MainActor.run {
        let desc = FetchDescriptor<BookmarkItem>(...)
        if let item = (try? modelContext.fetch(desc))?.first { ... }   // non-Sendable ModelContext
        if self.player == nil, let current = self.currentBookmark, ... { // non-Sendable View struct
            self.playMedia(...)
        }
    }
}
```

`Task.detached`'s closure is nonisolated `@Sendable`. Captured across that boundary:

1. `modelContext` — `@Environment(\.modelContext)`, a MainActor-bound, non-Sendable `ModelContext`;
2. `self` — the `BookmarkPlayerOverlay` struct (a non-Sendable SwiftUI `View` holding `@State`, including the failed/success observer tokens and `player`);
3. transitively, `self.bookmarks: [BookmarkItem]` — the **entire `@Model` object array** crosses threads, since it is a stored property of the captured struct.

This is the same violation class as **AUDIT-002** ("SwiftData @Model objects read on background thread inside Task.detached", P1), whose remediation spec mandates: *"Snapshot Sendable value tuples on the MainActor before the detached task and pass only those in."* The established codebase pattern (AUDIT-011, [BookmarkController.add](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/BookmarkController.swift:136)) is outer `Task` on the MainActor + inner `Task.detached` touching only `Sendable` strings + `LibraryPathResolver` (a `Sendable` struct), with the MainActor write-back **after** `.value` returns — i.e. never reaching *back* into captured MainActor state from inside the detached task. `cacheVideoOnDemand` inverts this by doing the write-back *inside* the detached task via `MainActor.run` over captured `self`/`modelContext`.

**Runtime impact today: none observed by inspection** — `ModelContext` is only *touched* inside `MainActor.run`, so there is no active cross-thread Core Data access; dynamically this is safe. The violation is static (Swift 6 `complete` diagnostics) plus latent: any future edit touching `modelContext`/`bookmarks` outside the `MainActor.run` block races.

**Why CI will not catch it:** AUDIT-012's `SWIFT_TREAT_WARNINGS_AS_ERRORS`/`GCC_TREAT_WARNINGS_AS_ERRORS` were removed from [project.yml](/home/kedarnath-reddy-vallaboina/Instagram_digest/project.yml:8) by `24aa87c` ("relax warning-as-errors for SwiftData macro"), and [build-ipa.yml](/home/kedarnath-reddy-vallaboina/Instagram_digest/.github/workflows/build-ipa.yml:99) passes no warnings-as-errors flags. Under `SWIFT_STRICT_CONCURRENCY=complete` without errors-as-errors, these violations surface as warnings only. The §6 gate is therefore advisory at exactly the moment this diff needs it to be a hard gate. (The relaxation itself predates this diff and is out of scope to revert here, but it must be recorded: **Invariant 6 is currently CI-unverifiable.**)

**Remediation:** restructure to the AUDIT-011 shape — snapshot `reelID`/`dest`/`remoteURL` (all `Sendable`), run download+move in `Task.detached`, write back on the outer `@MainActor` task. Concrete sketch in §5.

### 2.2 ModelContext safety (runtime): PASS

All `ModelContext` *uses* execute on the MainActor: `playViaGitHubRelease`'s resolver runs in `Task { @MainActor in }` against the `@MainActor` `GitHubBookmarkSyncService.shared`; the cache write-back runs inside `await MainActor.run`; `syncWithGitHub`'s merge path is unchanged in threading. No new background-context or cross-thread fetch was introduced. The F-P1-1 issue is capture (static), not use (dynamic).

### 2.3 `streamTask` lifecycle / rapid-tap cancellation: PASS with note

- `loadVideo` cancels + nils `streamTask` before rebuilding; `playViaGitHubRelease` cancels any prior task before assigning the new one (no overwrite-cancel bug); the resolver task checks `Task.isCancelled` after the network await before touching the player. Stale resolutions cannot install a player for a swiped-away bookmark through this path.
- Note (P3, F-P3-8): cancellation is not honored *inside* `resolveReleaseStreamURL` — every await is `try? await`, so `CancellationError` is swallowed and a cancelled task still burns through remaining candidate tags + `/latest` + pagination + redirect capture before returning. Bounded (~4–6 requests) and result-discarded, but wasteful on rapid swipes. Recommend `try Task.checkCancellation()` per loop iteration.

### 2.4 `BookmarkPlayerOverlay` self-capture in `Task { @MainActor in }`: PASS (pre-existing pattern)

The resolver task's `self.playMedia`/`self.player` accesses ride the same `@MainActor` task shape the file already used; no new isolation domain is introduced there.

---

## 3. Playback & Caching Mechanics Assessment

### 3.1 MIME override: CORRECT FIX, device proof still required

[BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:1036) (`playMedia`, lines 1036–1043) now builds remote assets as:

```swift
assetOptions[AVURLAssetOverrideMIMETypeKey] = "video/mp4"          // iOS 17+, public API
assetOptions["AVURLAssetOutOfBandMIMETypeKey"] = "video/mp4"       // string literal, private API
```

Verified against external SDK sources:

- `AVURLAssetOverrideMIMETypeKey` is **public API** ([Apple docs page exists](https://developer.apple.com/documentation/avfoundation/avurlassetoverridemimetypekey); objc2 bindings quote the header: *"Indicates the MIME type that should be used to identify the format of the media resource. When a value for this key is provided, only the specified MIME type is considered… Any other information… such as the URL path extension or a server-provided MIME type, is ignored."*). This is precisely the reported failure (GUID path, no `.mp4`, `application/octet-stream` → CoreMedia rejects → `status == .failed` → Tier 3). Correct tool for the job.
- `AVURLAssetOutOfBandMIMETypeKey` is a **private WebKit-internal** option with longstanding string-literal use in the wild. As a raw string it links no private symbol (low App Store-scan risk) and carries the same value (no conflict if both are honored). Redundant but harmless belt-and-braces; keep or drop at will.
- Unknown option keys are ignored at runtime, so worst case the private literal is a no-op — but note the converse: **no unit test can prove the MIME theory end to end**. The reported symptom (1–2 s spinner → Tier 3) must be re-tested on device against a legacy bookmark. This is gating criterion (3).
- Compile caveat (honest unknown): this host has no Apple SDK, so I could not confirm the symbol's introduction version. It sits in `AVAsset.h` alongside longstanding keys (strong hint it predates iOS 17, making the `#available(iOS 17.0, *)` guard harmless), but if it were newer than iOS 17 the guard would be insufficient and CI compilation would fail. Gating criterion (2) — a green CI build on the pinned Xcode 16.4 toolchain — closes this.

Also in `playMedia`: `preferredForwardBufferDuration` 8 s → 4 s for remote (faster start; benign, note it was unmentioned in the brief), and the spinner now clears on `.readyToPlay`/`.failed` instead of at resolve time — correct for a buffering indicator, with one gap noted in F-P3-12 (no stall timeout: a stream that neither readies nor fails spins forever with no Tier-3 escape).

### 3.2 Observer / player lifecycle: PASS with one P2 race

Teardown symmetry holds everywhere it must: `loadVideo` (lines 940–953), `playMedia` entry (1024–1034), and `teardownPlayer` (1251–1274) all remove `endObserverToken`/`failObserverToken` and invalidate `statusObserver` before replacement; the Rec-1 discipline (view-timer clock, no `AVPlayer` time observer) is preserved; the new `[weak avPlayer]` guards in the Tier-2 observers are an improvement.

**F-P2-2 (P2): stale remote-failure callbacks can clobber the *current* player.** The guards check `avPlayer != nil` — not `avPlayer === self.player` — and [handleRemotePlaybackFailure](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:1121) (lines 1121–1129) never checks that `bookmark` is still current. Sequence: play A (remote) → swipe/advance to B → B's player installed → A's item failure finally delivers while A's player is still retained by the outgoing container → handler runs `for: A`, either nil-ing B's fresh player or *starting A's local file* over B. The pre-existing code had the same shape but could only nil; the new local-fallback widens the blast radius to playing the wrong video. Remediation: bail unless `avPlayer === self.player` (capture `item` too) and `bookmark.reelID == currentBookmark?.reelID`.

Related P3s: F-P3-5 (status-observer `.failed` and `AVPlayerItemFailedToPlayToEndTime` both fire for one failure → double `handleRemotePlaybackFailure`; add a single-flight guard); F-P3-4 (the brief claims "proper error logging" but `error:` is silently ignored and `bookmark` unused in the `else` branch — Tier-3 drops are unobservable; add `os_log` with reelID + error).

The Tier-1.5 `statusObserver` (lines 1086–1095) still captures `self` strongly without `[weak avPlayer]` — inconsistent with the new Tier-2 branch but safe in practice (invalidated synchronously on every transition); align for uniformity when touching F-P2-2.

### 3.3 On-demand cache (`cacheVideoOnDemand`): works, but bypasses two vault policies

Mechanics that are right: `moveItem` never overwrites, so concurrent doubles cannot clobber (loser throws, caught, non-fatal); non-2xx responses clean up the temp file; completion flips `localStatus` to `.cached` and, if the player already Tier-3'd, starts the fresh local file — a genuine UX win for the reported symptom.

**F-P2-3 (P2, Invariant 5b regression): no cellular gate.** The download runs regardless of `Reachability.shared.isExpensive`, while §6 Invariant 5(b) gates video transfers on `!isExpensive`. Worse, it runs *concurrent with the stream*, doubling cellular bytes for the same content. Remediation: skip background caching when `isExpensive` (streaming continues; Wi-Fi plays will cache).

P3 cache items:

- **F-P3-1:** pre-checks use `FileManager.fileExists` while playability requires non-empty (`resolvedLocalFileURL`/`fileExistsAndNonEmpty`). A zero-byte/interrupted `dest` permanently blocks all future caching for that reel. Check `resolvedLocalFileURL(...) != nil` (or delete zero-byte dests) instead.
- **F-P3-2:** check-then-move TOCTOU — the loser of a concurrent double throws from `moveItem` with no temp cleanup (leak until `tmp/` purge). `defer`-guard the temp removal.
- **F-P3-3:** the detached task is untracked, uncancellable, and never deduplicated: it survives overlay dismissal (write-back touches a stale `self` copy), and every replay spawns another full concurrent download. Store a per-`reelID` handle, cancel in `teardownPlayer`, skip when in flight.
- **F-P3-10:** no `enforceCachedVideosCap` trigger and no size accounting after caching, so a binge session grows `Bookmarks/` unboundedly between bookmark-adds (cap enforcement only runs on add). In mitigation, on-demand rows carry `videoAssetName != nil`, so the upload-first eviction order treats them as safely evictable and re-streamable — the steady state is benign; only session growth is unbounded. Also note the overlay player is outside `AVPlayerPool.boundReelIDs`, so a concurrent enforcement pass could unlink a file the overlay is playing (pre-existing gap, slightly widened). Trigger cap enforcement post-cache.
- Minor: `URLSession.shared.download` inherits the shared session's generous resource timeout (a stalled download lingers; the file's own `makeEphemeralSession` 15 s pattern is the in-house precedent); no MIME/content sniffing before caching a 200 body (low risk — GitHub errors are non-200).

### 3.4 AUDIT-001 (phantom asset) holds: PASS

`syncWithGitHub` Step 4 still derives the pushed `video_asset_name` strictly from confirmed `local.videoAssetName` ([GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:795)); the cache path sets `localStatus` only, never `videoAssetName` — no new phantom-claim path. LRU upload-first order is unaffected (and correct for on-demand rows per §3.3).

---

## 4. Edge Cases & Robustness Analysis

### 4.1 F-P1-2 (P1): cache-key drift deterministically breaks the AUDIT-013 TTL test

Lookup key ([GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:153)): `"\(repo)/\(candidateTags.first ?? yearTag)/\(safeID)"` → e.g. `repo/v2026/s3_reel_01`.
Test-hook key ([GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:87) `setMemoryStreamCacheEntry`): `"\(repo)/\(year)/\(safeID)"` → `repo/2026/s3_reel_01`.

In `testAudit013ResolveReleaseStreamURLS3RedirectAndMemoryCacheTTL` part 3 ([DirectStreamTests.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Tests/InstagramDigestTests/DirectStreamTests.swift:1293)), the expired entry is written under the unread key while part 1's valid (+240 s) entry still sits under the read key — so `resolvedURL3` returns part 1's URL instead of `refreshedS3` and the assertion fails. Parts 1–2 still pass (verified by tracing the mock: 1 tag lookup + 1 redirect = 2 calls; cache hit on repeat). This is a certain CI red. Remediation: make the hook build the tag-format key (or, better, route both through one shared key builder).

### 4.2 F-P2-1 (P2): asset-miss inside a *found* release never tries remaining candidates or `/latest`

[GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:188) — the `guard let json = releaseJson` / asset-match / `return nil` flow only advances candidates on tag-404. If the first 200 tag lacks the asset (the exact legacy shape: bookmarked 2024/2025 → derived `v2024`, a `v2024` release *exists* from that year's syncs, but this asset was migrated under `v2026`), resolution returns nil without trying `v2026`-equivalent candidates or `/releases/latest`. The fallback only rescues the all-tags-404 case. Symmetrically, a wrong-but-resolving stored `releaseTag` shadows the correct release. Remediation: on asset-miss (embedded + paged), `continue` to the next candidate and then `/latest` instead of returning nil.

### 4.3 Release-tag / date handling: PASS with minor notes

- `releaseTag(for:)` year-≤-2000 → current-year guard kills the `v1` tag; existing boundary tests (`v2026`/`v2027`) are unaffected; no test depended on `distantPast` (verified by sweep).
- `parseDate` multi-format + `Date()` fallback is a strict improvement; GMT primary with local-TZ fallback-year is a negligible boundary inconsistency.
- `syncWithGitHub` backfill (`resolvedTag`) correctly heals legacy rows' `releaseTag` in SwiftData. Deliberately *not* setting `dtoMutated` for the backfill is arguably safer than persisting a possibly-wrong-year derived tag (see F-P2-1 shadowing) — keep as is, but the F-P2-1 `continue` fix is what makes both safe.
- `importBookmarks` does not backfill tags — consistent enough (playback derives candidates from `bookmarkedAt`), uniformity optional.

### 4.4 `sanitizeRepo`: PASS with integration gap (F-P3-7)

Stripping (`https://github.com/`, `github.com/`, `.git`, slashes) is correct for documented inputs; unhandled shapes (uppercase scheme, malformed paths) fail safe downstream (URL-init nil / API 404 → Tier 3). But `syncWithGitHub` ([GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:538)) and `syncIfNeeded` still use plain trimming with strict `owner/repo` validation, and `DigestState.thumbnailUrl` interpolates the raw value — so a pasted full-URL repo now *plays* (sanitized) but *sync throws* "Invalid repository format" and thumbnails 404. Sanitize at the save boundary (`SettingsSheetView.saveSettings`, `GitHubSyncModal.performSync`) and inside `syncWithGitHub`.

### 4.5 Minor edge items

- **F-P3-6:** hardcoded `2026` time-bombs — `year: Int = 2026` default ([GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:132)) and `parsedYear > 2000 ? parsedYear : 2026` ([BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:999)). Use a sentinel (e.g. `0` → dynamic current year) like the service's own `validYear`.
- **F-P3-9:** AUDIT-009 paged search (`fetchReleaseAssetDownloadURL`) matches only the sanitized asset name while the embedded search also tries the raw `reelID` name — pass both names through.
- **F-P3-11:** the retained 4-arg `resolveReleaseStreamURL` overload is redundant given the defaulted `releaseTag:` parameter (all call sites work without it). Harmless cruft; delete to remove any overload-resolution doubt — CI compilation of the pushed tip remains the arbiter either way.
- **F-P3-12:** spinner has no stall timeout (see §3.1).
- New test `testResolveReleaseStreamURLFallbackToLatestRelease` ([DirectStreamTests.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Tests/InstagramDigestTests/DirectStreamTests.swift:1331)): sound and hermetic (404s → `/latest` → 302 capture); `setUp`/`tearDown` hygiene (session reset, `clearMemoryStreamCache`) intact. It does not cover F-P2-1 (asset-miss-in-found-release) — extend it when fixing.

### 4.6 Invariant & prior-fix regression matrix

| Check | Result |
|---|---|
| Inv. 1 — Playback tiers 1 → 1.5 → 2 → 3 | HOLDS (Tier-2→Tier-1 rescue on concurrent-cache completion is order-consistent) |
| Inv. 2 — Resolver/Cobalt excision | HOLDS (no reintroduction) |
| Inv. 3 — 60 FPS feed swiping | HOLDS (untouched) |
| Inv. 4 — Honest completed states | HOLDS (untouched) |
| Inv. 5 — Silent sync & vault | PARTIAL — (b) cellular gate bypassed by cache (F-P2-3); (c) LRU cap unenforced post-cache (F-P3-10); coalescing/tombstones/push logic untouched |
| Inv. 6 — Strict concurrency / zero warnings | REGRESSED (static) — F-P1-1; gate itself advisory since `24aa87c` (pre-existing) |
| AUDIT-001 phantom asset | HOLDS (§3.4) |
| AUDIT-002 detached @Model reads | REGRESSED — same class reintroduced (F-P1-1); original `.task(id:)` site untouched |
| AUDIT-003/005/006/007 (UI states/ids) | HOLD (untouched) |
| AUDIT-008 trimmed PAT | HOLDS — all new requests use `trimmedPAT`; save-boundary trimming verified intact |
| AUDIT-009 pagination | HOLDS (F-P3-9 nit only) |
| AUDIT-010 thumbnails | HOLDS (untouched) |
| AUDIT-011 background copy | HOLDS (untouched; is the template for the F-P1-1 fix) |
| AUDIT-012 warnings gate | Pre-existing relaxation, not this diff — but it blinds CI to F-P1-1 |
| AUDIT-013 mandated tests | REGRESSED — TTL test part 3 breaks (F-P1-2); new latest-fallback test is sound |
| AUDIT-014 migration fields | HOLDS (untouched; client-side `distantPast` tail now closed) |

---

## 5. Required Remediations (if any) or Sign-off

### Must-fix before merge (P1)

**F-P1-1 — Restructure `cacheVideoOnDemand` to Sendable-only detached work.**
In [BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:1131), hoist to an outer `@MainActor` task and pass only `Sendable` snapshots (`reelID: String`, `dest: URL`, `remoteURL: URL`) into `Task.detached`; do the `ModelContext` fetch/save and any `playMedia` call in the outer task after `.value` returns (the [BookmarkController.add](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/BookmarkController.swift:136) AUDIT-011 shape). Do not reference `self` or `modelContext` inside the detached closure. This simultaneously fixes the `[BookmarkItem]` array crossing.

**F-P1-2 — Unify the stream-cache key format.**
In [GitHubBookmarkSyncService.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:87), make `setMemoryStreamCacheEntry` build the same tag-format key the lookup uses (`"\(sanitizedRepo)/v\(year)/\(safeID)"` at minimum; preferred: extract one `static func streamCacheKey(repo:tag:reelID:)` used by both the hook and line 153). Re-run the full `DirectStreamTests` suite.

### Should-fix before merge (P2)

- **F-P2-1** — In `resolveReleaseStreamURL`, treat asset-miss (embedded + paged) as `continue` to the next candidate tag and then `/releases/latest`, not `return nil`. Extend the new fallback test with an asset-miss-in-found-release case.
- **F-P2-2** — In the Tier-2 observers and `handleRemotePlaybackFailure`, bail unless the failing `avPlayer === self.player` (and `bookmark.reelID == currentBookmark?.reelID`) so stale failures can neither nil nor replace the current item's player.
- **F-P2-3** — Skip `cacheVideoOnDemand`'s download when `Reachability.shared.isExpensive` (streaming continues).

### Hardening (P3, may follow)

F-P3-1 zero-byte dest shadowing → check `resolvedLocalFileURL(...) != nil`; F-P3-2 temp leak on move race → `defer` cleanup; F-P3-3 untracked/duplicate downloads → per-`reelID` handle + cancel in `teardownPlayer` + in-flight set; F-P3-4 silent failures → `os_log` reelID + error in `handleRemotePlaybackFailure`; F-P3-5 double failure delivery → single-flight guard; F-P3-6 hardcoded `2026` → dynamic-year sentinel; F-P3-7 repo sanitization gaps → sanitize in `syncWithGitHub` + save boundaries; F-P3-8 swallowed cancellation → `Task.checkCancellation()` in candidate loop; F-P3-9 paged search → match both asset names; F-P3-10 cap/ledger bypass → trigger `enforceCachedVideosCap` post-cache; F-P3-11 redundant overload → delete; F-P3-12 spinner stall → stall timeout with Tier-3 escape.

### Sign-off (withheld pending gates)

This audit withholds final sign-off until: (1) F-P1-1 and F-P1-2 are fixed; (2) `build-ipa.yml` unit + UAT suites are green on the pushed tip (also the only available proof that the `#available(iOS 17.0, *)`-gated MIME key compiles under the pinned Xcode 16.4 SDK — not verifiable from this Linux host); (3) on-device proof that at least one legacy (`release_tag`-less, extension-less-asset) bookmark that previously showed 1–2 s of buffering then Tier 3 now reaches Tier-2 playback. P2s are strongly recommended in the same pass; P3s may be scheduled. Re-audit scope after remediation: the F-P1-1 hunk, the cache-key helper + TTL test, and the CI/device evidence — no full re-audit required.
