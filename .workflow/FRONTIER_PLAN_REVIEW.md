# Frontier Plan Review: Final Refactor & Repository Lockdown (§6)

**Reviewer:** Principal Software Engineer (pre-execution review)
**Date:** 2026-10-25
**Inputs:** `.workflow/REQUIREMENTS.md` (§6 + §5 addendum), `.workflow/IMPLEMENTATION_PLAN.md`
**Anchor:** Single-person personal use (Kedarnath Reddy). Zero multi-tenant overhead. No enterprise abstractions suggested below.
**Method:** Every finding verified against current source in `Sources/`, `Tests/`, `config.py`, `.env.example`, `project.yml`, `build-ipa.yml`.

---

## 1. Verdict

**APPROVE WITH CHANGES — do not execute as written.** The plan covers all §6 functional requirements with no over-engineering, and its phase decomposition is sound. However, it has **three compile-breaking omissions**, **one data-loss window**, and **several API-contract gaps** that will fail CI or corrupt state if executed verbatim. All are small, personal-scale fixes. The revised ordering in §4 and the must-fix list in §2 should be folded into the plan before execution.

---

## 2. Must-Fix Gaps (block execution)

### F1. [COMPILE BREAK] Banner usage block has no removal step
- Plan Phase 1 deletes `OfflineCacheBanner.swift` but never removes its **usage** in [InstagramDigestApp.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/InstagramDigestApp.swift:349) (lines 349–359: `OfflineCacheBanner(reels:...)` inside `FeedMainView`). `InstagramDigestApp.swift` is listed in Step 1.2 files, but its only bullet is the UserDefaults cleanup. Deleting the file without removing this block breaks the build.
- **Fix:** Add Step 1.3: delete the `if DigestDataService.directMode, let m = manifest` banner block in `FeedMainView`. Per §5 the banner is removed with no replacement — but confirm the home-screen "Download Week" entry point survives elsewhere (Settings row + `TriggerDownloadAllNotification` exist; if no home-screen affordance remains, §2's P1 bulk-cache trigger regresses — verify, don't redesign).

### F2. [COMPILE + UAT BREAK] Grid badge `resolver` branch and XCUITest expectation unaddressed
- [BookmarksSheet.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:271) lines 271–275 emit `BookmarkBadge_{i}_resolver` (globe) for non-active-week uncached bookmarks. The plan never updates this badge logic.
- [InstagramDigestUITests.swift](/home/kedarnath-reddy-vallaboina/Instagram_digest/Tests/InstagramDigestUITests/InstagramDigestUITests.swift:353) lines 353–357 explicitly assert the 3-state badge set including `BookmarkBadge_0_resolver`. CI UAT will fail unless both change together.
- **Fix:** Redefine the badge mapping in the same step as the Tier rewrite (see F8: drive it from persisted `videoAssetName`): cached → checkmark; active-week uncached → download; GitHub-backed → cloud; web-only → safari/globe. Update the XCUITest assertion in the same commit.

### F3. [WRONG SYMBOL] `BookmarkController.toggleBookmark` does not exist
- Plan Step 5.1 says "On bookmark add / remove in `BookmarkController.toggleBookmark`". There is no such method. Verified: `BookmarkController` exposes `add(reel:weekID:context:)` and `remove(reelID:context:)`; the toggle lives at `InstagramDigestApp.toggleBookmarkCurrentReel` ([line 1010](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/InstagramDigestApp.swift:1010)), which calls `add`/`remove`.
- **Fix:** Hook silent sync inside `BookmarkController.add`/`remove` (single-owner choke point, correct choice) — just correct the symbol name in the plan.

### F4. [SILENT DATA LOSS] Sync push condition drops asset-only updates
- Current `syncWithGitHub` pushes `bookmarks.json` only `if newlyAddedLocal > 0 || remoteDTOs.isEmpty` ([GitHubBookmarkSyncService.swift:348](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:348)). Plan Step 4.2 uploads video assets and says "persist `video_asset_name` and `release_tag` in remote `bookmarks.json`" — but for any bookmark that **already exists remotely** (pre-feature bookmarks, prior failed uploads), `newlyAddedLocal == 0`, so the video uploads while the DTO update is silently discarded. Tier 2 then has no record of what exists.
- **Fix:** Introduce a `dtoMutated` flag (set on new bookmark **or** newly uploaded asset **or** thumbnail backfill) and push when `dtoMutated || remoteDTOs.isEmpty`. Await video uploads (unlike the fire-and-forget `Task.detached` thumbnail path at line 294) **before** encoding the push payload.

### F5. [STATE CORRUPTION] In-flight guard must live in the service, not call sites
- Plan Step 5.1 proposes trigger-point guards, but after Phase 5 there are **four** concurrent sync entry points: existing `BookmarksSheet.task` auto-sync ([lines 358–369](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:358) — already fires on every sheet appear), `GitHubSyncModal.performSync` (has only a local `isSyncing` flag), and the new add/remove + foreground triggers. Two overlapping `syncWithGitHub` runs both read the same `bookmarks.json` SHA; the second PUT returns 409/422 and its uploads become orphans.
- **Fix:** Centralize in `GitHubBookmarkSyncService` (it is `@MainActor`, but `await` suspension points still interleave): add `private var isSyncing` + `private var needsResync` (coalesce, don't drop — a bookmark added mid-sync must trigger a follow-up pass). All four entry points call one `syncIfNeeded(...)` entry. Deduplicate the existing `BookmarksSheet.task` sync into the same path rather than adding a second on-appear trigger.

### F6. [DATA LOSS WINDOW] LRU eviction can delete not-yet-uploaded videos
- Plan Step 5.2 evicts oldest `.mp4`s beyond the cap. Nothing orders upload-before-evict: a video evicted before its first successful Release upload leaves only the (48h-expired) Meta URL — the durable copy is gone forever. With silent background sync this window is small but real (offline bookmarking streaks, failed uploads).
- **Fix (personal-scale, no new infra):** persist `videoAssetName`/`releaseTag` on `BookmarkItem` at sync time (additive SwiftData fields — lightweight migration, safe). In `enforceCachedVideosCap`, prefer evicting already-uploaded victims when sync is configured; skip not-yet-uploaded videos unless over hard cap pressure. This same persisted field gates Tier 2 attempts (F8) and drives badges (F2) — one field, three uses.

### F7. [MISSING PROP] Settings census has no data source
- Plan Step 3.3 says "run the cache census on appear" in `SettingsSheetView`, but that view has **no `reels` or `weekID` inputs** (verified: init takes only bindings + callbacks). There is nothing to census.
- **Fix:** Pass `reels` + `weekID` (and `generatedAt` if needed) into `SettingsSheetView` from `FeedMainView`, mirroring `DownloadAllSheet`'s init. Census runs off-main via `.task`, result cached in `@State`.

---

## 3. API-Contract Corrections (Phase 4 will fail at runtime without these)

All verified against current `GitHubBookmarkSyncService.swift` (385 lines, no release-asset code exists yet — this is greenfield inside the file, so get the contract right the first time):

1. **`upload_url` template must be stripped.** GitHub returns `upload_url` as `https://uploads.github.com/.../assets{?name,label}`. The plan's "POST `...?name={reel_id}.mp4`" must first cut at `{`. This is the #1 classic failure of this API.
2. **Treat 422 `already_exists` as success.** Both `POST /releases` (create tag) and asset upload return 422 if the tag/asset already exists. Presence-check-then-act has TOCTOU (double-tap manual sync, add-trigger + foreground-trigger racing before F5 lands). On 422: refetch and continue, never fail the batch.
3. **Paginate the assets list.** `GET .../assets` defaults to 30/page; a 50-video vault overflows page one. Request `?per_page=100` (sufficient for personal scale; no pagination loop needed) or first-page misses will cause duplicate-upload 422s (handled by #2, but avoid the noise).
4. **Pin one asset-naming rule.** The plan uses `{reel_id}.mp4` (Step 4.2) and `{sanitized_id}.mp4` (Steps 4.1/4.3) interchangeably. **Use sanitized always** (`LibraryPathResolver.sanitizeComponent`), matching the existing thumbnail convention (`{safeID}_portrait.jpg`). Un-sanitized IDs risk 422s and lookup misses.
5. **Redirect capture needs a dedicated session + delegate.** `URLSession.shared` follows redirects automatically, so the plan's "issues request ... without following redirects to extract the 302 `Location:`" is unimplementable as stated. Add a small ephemeral `URLSession` with a `URLSessionTaskDelegate` that cancels the redirect and captures `Location`. ~15 lines, no new dependency.
6. **Presigned S3 URLs are memory-only, short-lived.** GitHub's S3 redirect URLs expire in minutes. Plan says "cached in memory for playback duration" — correct; extend the invariant explicitly: **never persist to DTO/`bookmarks.json`/SwiftData**, tiny in-memory `[reelID: (url, fetchedAt)]` with ~4-min TTL is sufficient.
7. **Validate credentials before the release flow.** Against a private repo, a bad PAT returns **404, not 401** — the plan's "if 404, create release" will misread auth failure as missing-release and then fail confusingly. First call in the release path: `GET /repos/{repo}` (or the existing `bookmarks.json` GET, which already distinguishes errors) to surface "check PAT/repo" honestly.
8. **Document required PAT scope.** Personal classic PAT needs `repo` (or fine-grained Contents read+write). Add one line to the Settings "GitHub Bookmark Backup" footer and the modal; otherwise Tier 2 fails with opaque 404s on new-device setup.
9. **Upload via `uploadTask(fromFile:)`**, not a `Data`-based task — streams ~10 MB files from disk without memory spikes.
10. **Group bookmarks by year.** The plan's per-bookmark "determine tag → GET release → maybe create" issues N identical release lookups. Group by `releaseTag` first (almost always a single `v{year}`), one lookup per year. Trivial loop restructure, avoids rate-limit noise during a 50-video first sync.
11. **Derive year in UTC.** Use a `Calendar(identifier: .gregorian)` with `timeZone = UTC` in the new pure helper `releaseTag(for: Date) -> String` so a Dec 31/Jan 1 bookmark doesn't flip tags by timezone. Extract as a pure function — it becomes directly unit-testable (see §6).

---

## 4. Required Ordering Changes

### R1. Build Tier 2 (upload + stream) BEFORE deleting the resolver
The plan deletes `BookmarkResolver` in Phase 1 but builds its replacement in Phase 4 — the tree is in a no-fallback state for three phases, and Phase 1–3 cannot be functionally verified for old bookmarks. Reorder so **every phase leaves a working app**:

| Revised order | Content | Verifiable outcome |
|---|---|---|
| Phase A | Census + honest completed states (old Phase 3) | Independent, zero-risk, green UAT |
| Phase B | Release upload + Tier 2 streaming **alongside** resolver (old Phase 4) | Fallback chain Tier 1 → 1.5 → GitHub → resolver → web; manual "Sync Now" proves upload end-to-end |
| Phase C | Resolver purge + Tier 3 web fallback (old Phase 1 + Tier 3 wiring) | Resolver excised only after its replacement is proven; captcha dialogs gone |
| Phase D | FeedCell zero-thumbnail revert (old Phase 2) | 60 FPS, isolated UI change |
| Phase E | Silent background sync + vault + upload-before-evict (old Phase 5 + F6) | Automation lands last, on top of a proven manual path |
| Phase F | Swift 6 audit + tests + CI/IPA (old Phase 6) | Final gate only |

Rationale: silent sync (E) automates a path that manual sync (B) has already proven; the purge (C) removes a fallback only after Tier 2 demonstrably replaces it. No phase depends on a later phase.

### R2. Move test updates into their own phases — Phase 6 becomes audit-only
The plan defers all test changes to Step 6.2, leaving the tree red (failing compilation — `DirectStreamTests` references `BookmarkResolver` ~25 times) from Phase 1 through Phase 5. Each phase must update its own tests in the same commit (resolver-test removal with Phase C, census/tier/release-tag tests with Phases A/B). Phase 6 then only audits coverage and runs CI.

### R3. Rewire (don't delete) the Tier 1.5 failure observers
Current `playMedia(..., isStoredURL: true)` arms `failObserverToken` + `statusObserver` that re-enter `playViaResolver` ([lines 1080–1097](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:1080)). Step 1.2 deletes `playViaResolver` but never rewires these observers — and Step 4.3 doesn't mention them. **Fix:** rewire both observers to fall through to Tier 2 **with a single-shot guard** (a flag consumed on first fire), so a failing Tier 2 stream cannot loop back into Tier 2. Related: Step 1.2 deletes `isResolving`/`resolutionError` state wholesale, but Tier 2's async `resolveReleaseStreamURL` needs a loading indicator or taps produce seconds of dead UI. **Rename** `isResolving → isLoadingVideo` and keep the error view for Tier 3/offline states; delete only the resolver-specific cases (rate-limit/captcha/unsupported-format).

### R4. Upload source: use `resolvedLocalFileURL`, not Bookmarks-dir-only
Step 4.2 uploads only from `MediaCache/Bookmarks/{id}.mp4`. But Tier 1 playback resolves from **either** the week cache or the Bookmarks dir (`resolvedLocalFileURL` checks both, [LibraryPathResolver.swift:127](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Engine/LibraryPathResolver.swift:127)), and a re-downloaded week restores the week copy without restoring the evicted Bookmarks copy. Videos playable via Tier 1 would never back up. **Fix:** upload source = `resolvedLocalFileURL(for:weekID:reelID:)` (either location counts).

---

## 5. Smaller Corrections & Clarifications

1. **Wrong test filename.** Step 6.2 names `tests/test_direct_stream.py` — the file is `tests/test_direct_stream_mode.py` (confirmed in repo + `build-ipa.yml`). Python COBALT references exist **only** in `config.py` + `.env.example` (verified repo-wide); removal is safe, but the plan omits `.env.example:57` — per repo `AGENTS.md` precedence-audit rule, remove `COBALT_RESOLVER_API_URL` from `.env.example` in the same step as `config.py`.
2. **`ResolverTests.swift` must be KEPT.** Despite its name, all 28 lines test `LibraryPathResolver` sanitization/paths — zero references to `BookmarkResolver`. Step 6.2's "remove all tests referencing deleted `BookmarkResolver`" must target only the resolver `MARK` section of `DirectStreamTests.swift` (~lines 264–750+), keeping its `MockURLProtocol` (still used by `DigestDataService` tests).
3. **Swift tests cannot run "locally".** Step 6.2 says "run Python and Swift test suites locally" — Swift/`xcodebuild` runs only on macOS CI (this workstation is Linux). Split the step: `pytest` locally; Swift unit + XCUITest via `build-ipa.yml` on push.
4. **`project.yml` needs no edit — confirmed safe.** Both app and test targets use directory globs (`path: Sources/InstagramDigest`, `path: Tests/InstagramDigestTests`), and CI regenerates via `xcodegen` anyway. File deletion is zero-risk; note this in the plan to close the question.
5. **Tier 1.5 narrowing is intentional — acknowledge it.** Current code tries the stored Meta URL for **any** week's bookmark when online ([line 982](/home/kedarnath-reddy-vallaboina/Instagram_digest/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:982)); §6 restricts the fast path to the active week. Correct per spec (stale signed URLs are dead weight after 48h), but it is a behavior change worth one line in the plan so the implementer doesn't "preserve" the old branch.
6. **Census/coordinator interplay.** Census must drive UI **only in `.idle`** (a `.downloading`/`.paused`/`.failed` coordinator state always wins), and the sheet's pending set must derive from the same census over `effectiveReels` (respecting the default-on unwatched filter — full-250 census vs. filtered-batch pending will otherwise disagree, e.g. watched-but-uncached reels). Note `startDownloadAll` already re-filters pending internally (AUDIT-067) and runs rollover purge first — the census-filtered call stays consistent.
7. **Cellular guard for silent uploads.** Phase 5 as written will silently upload up to ~500 MB over cellular on foreground triggers. Personal-scale fix: gate **video-asset uploads** on non-expensive network (`NWPathMonitor.path.isExpensive == false`, consistent with existing `Reachability`); metadata/thumbnail sync always runs. One `if`, no new settings UI.
8. **Cobalt UserDefaults cleanup should be versioned.** Step 1.2's "one-time startup cleanup" needs a `hasPurgedCobaltKeys_v1` sentinel so it runs once, not every launch.
9. **Export/import round-trip.** `importBookmarks` ignores the DTO's `video_asset_name`/`release_tag` and `exportBookmarksJSON` emits `video_asset_name: nil` — after F6's persisted fields exist, round-trip them so Tier 2 works immediately post-import without requiring a re-sync.
10. **`syncWithGitHub` must never purge — assert it.** §6 states "syncing to GitHub never purges local cache"; current code complies. Add an explicit test/CI-grep guard (no `removeItem` in `GitHubBookmarkSyncService.swift`) so future edits can't regress it. Related reassurance: `executeRolloverPurge` already pins all bookmarked files via LivePinSet — the §6 vault survives rollover by existing design; the plan correctly leaves rollover untouched.

---

## 6. Missing Test Guardrails (add to plan)

| # | Test | Type | Covers |
|---|---|---|---|
| T1 | `cacheCensus` exact counts via injected `LibraryPathResolver(baseURL: tmp)` (empty dir, partial, full, zero-byte files excluded) | Swift unit | Phase A |
| T2 | `releaseTag(for:)` incl. Dec 31/Jan 1 UTC boundary | Swift unit | §3.11 |
| T3 | Playback-tier decision as a **pure function** `PlaybackTier.resolve(hasLocal:isActiveWeek:hasStoredURL:isOnline:isGitHubBacked:) -> Tier` — no AVPlayer in the test | Swift unit | Tier 1→1.5→2→3 incl. offline honesty |
| T4 | `upload_url` template strip + 422-already_exists-as-success (create + upload) with `MockURLProtocol` | Swift unit | §3.1–3.2 |
| T5 | In-flight coalescing: two overlapping `syncIfNeeded` → one network pass + one follow-up; single PUT wins, no 409 | Swift unit | F5 |
| T6 | Push-condition: asset-only upload sets `dtoMutated` → `bookmarks.json` PUT contains `video_asset_name`/`release_tag` | Swift unit | F4 |
| T7 | Eviction prefers uploaded victims; never evicts un-uploaded when sync configured (F6) | Swift unit | F6 |
| T8 | No-`removeItem` grep guard on `GitHubBookmarkSyncService.swift` | CI grep | §5.10 |
| T9 | XCUITest badge update: replace `BookmarkBadge_0_resolver` with new mapping (F2); Tier 3 "Open on Instagram" tap-through (`BookmarkPlayerOpenInstagramButton` id already exists) | XCUITest | F2, Tier 3 |
| T10 | Post-purge greps: `rg -i cobalt` over `Sources/ Tests/ Tests/InstagramDigestUITests/ config.py .env.example`; `rg 'playViaResolver\|BookmarkResolver\|OfflineCacheBanner\|resolutionError'` over `Sources/` → 0 hits | CI/exit criteria | Phase C |
| T11 | Feed perf: manual Instruments Time Profiler pass on device (60 FPS swipe) + `rg 'UIImage\(contentsOfFile' Sources/.../Feed/` → 0 hits | Manual + grep | Phase D |

Note on T3: extracting the tier decision as a pure function is the highest-value testability change in this review — it makes the §6 hierarchy (the core lockdown invariant) exhaustively testable without players, network, or keychains, and costs ~20 lines.

---

## 7. Coverage Matrix (§6 + approved additions → plan)

| Requirement | Plan coverage | Review note |
|---|---|---|
| Tier 1 local vault (latest 50 / configurable cap) | Phase 5.2 | OK, plus F6 upload-before-evict |
| Tier 1.5 active-week fast path w/ silent fallthrough | Phase 4.3 | OK, plus R3 observer rewire + single-shot guard |
| Tier 2 GitHub Release streaming | Phase 4.1–4.3 | OK subject to §3 contract fixes |
| Tier 3 web fallback + offline honesty | Phase 1.2 + 4.3 | Partial — badge/UAT gap (F2), keep loading state (R3) |
| Excise resolver + cobalt keys + captcha | Phase 1 | OK, plus F1 banner block, §5.1 `.env.example`, §5.8 versioned cleanup |
| Silent background sync (add/remove, foreground, sheet) | Phase 5.1 | OK subject to F3 symbol fix, F5 centralized guard, §5.7 cellular gate |
| Completed states (census-driven) | Phase 3 | OK subject to F7 Settings props, §5.6 coordinator precedence |
| 60 FPS FeedCell revert | Phase 2 | OK; add T11 verification |
| Dead-code cleanup + Swift 6 + zero warnings | Phases 1, 6.1 | OK; keep `ResolverTests.swift` (§5.2) |
| Tests + CI + `.ipa` + freeze | Phase 6.2–6.3 | OK subject to R2 (tests per-phase), §5.3 filename + macOS-only Swift tests |
| Approved: silent background sync | Phase 5.1 | Same notes as above |
| Approved: local 50-reel vault retention | Phase 5.2 | Same notes as above |

No over-engineering detected: no new services, frameworks, abstractions, or multi-user machinery. The suggested additions (pure helpers, a small redirect delegate, two model fields, one service flag pair) are all proportional to personal scale.

---

## 8. Suggested Exit-Criteria Amendments

Append to the plan's existing 7 criteria:

8. `rg -i cobalt Sources/ Tests/ Tests/InstagramDigestUITests/ config.py .env.example` → 0 hits; `rg 'playViaResolver|BookmarkResolver|OfflineCacheBanner' Sources/` → 0 hits.
9. XCUITest badge suite passes with the new Tier-2-aware badge mapping; no reference to `BookmarkBadge_*_resolver` remains.
10. Asset-only sync pushes updated `bookmarks.json` (T6); concurrent syncs coalesce without 409/422 (T5).
11. Eviction never deletes an un-uploaded video while sync is configured (T7); no `removeItem` in `GitHubBookmarkSyncService.swift` (T8).
12. Feed swipe shows zero main-thread `UIImage(contentsOfFile:)` (grep) and holds 60 FPS on-device (Instruments spot-check).
13. Video uploads occur only on non-expensive networks; metadata sync is unaffected.

---

## 9. Summary of Required Plan Edits

1. Add F1 (banner usage removal) and F2 (badge + UAT) steps — both break the build/CI as written.
2. Fix F3 (symbol), F7 (Settings props), §5.1 (`.env.example` + correct pytest filename), §5.2 (keep `ResolverTests.swift`), §5.3 (macOS-only Swift tests).
3. Adopt R1 phase reorder (census → Tier 2 → purge → FeedCell → silent sync → gate) and R2 per-phase tests.
4. Fold §3 GitHub API contract details into Phase 4 steps (template strip, 422 handling, pagination, redirect delegate, credential validation, PAT scope, `uploadTask(fromFile:)`, year grouping, UTC year).
5. Add F4 (`dtoMutated` push condition), F5 (centralized `syncIfNeeded` coalescing), F6 (upload-before-evict + persisted asset fields), R3 (observer rewire + retained loading state), R4 (`resolvedLocalFileURL` upload source), §5.7 (cellular gate).
6. Add §6 tests and §8 exit criteria.

After these edits, the plan is executable and the lockdown can proceed.
