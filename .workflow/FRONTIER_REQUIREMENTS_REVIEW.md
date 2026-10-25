# Frontier Requirements Review — Final Refactor & Repository Lockdown (§6)

**Role:** Chief Architect review
**Date:** 2026-10-25
**Scope:** `.workflow/REQUIREMENTS.md` §6 (Final Refactor & Permanent Repository Lockdown), read against §§1–5 and the current Swift sources
**Scale anchor:** Single-person personal use (Kedarnath Reddy). One user, one iPhone, one workstation, one weekly batch of 250 reels. Zero multi-tenant overhead. No enterprise abstractions, microservices, auth frameworks, or scaling complexity. Recommendations below are deliberately the smallest correct single-user solution.
**Note:** This file previously held the §§1–4 review (2026-10-24); that content remains in git history. This revision covers the §6 lockdown.

---

## Part A — Intent Analysis

### A1. Functional intent of §6 (what it is really asking for)

Section 6 converts the app from "resolver-dependent streaming" to "owned-media playback":

1. **Bookmarks become self-sufficient.** Playback order Local Cache → GitHub Release Assets → Open on Instagram means every bookmark the user cares about is either on-device or in a repo the user owns. The Cobalt resolver — an unauthenticated third-party dependency that produces captcha/bot-check failures — is cut out entirely.
2. **Completed states become honest.** Both the Download All sheet and Settings must reflect on-disk reality (all 250 cached) instead of only reflecting the in-memory coordinator state.
3. **Feed scroll returns to 60 fps.** The `FeedCell` poster path is the confirmed stutter source: `FeedCell.configure` calls synchronous `UIImage(contentsOfFile:)` + JPEG decode on the main thread during cell dequeue (`Sources/InstagramDigest/Views/Feed/FeedPagerView.swift:605`). Reverting to pure `AVPlayerLayer` slot attachment removes it.
4. **Lockdown.** Dead code goes, Swift 6 strict concurrency is clean, CI builds a signed `.ipa`, repo freezes.

This is a sound end-state for personal use. The critique below is not about direction — it is about five places where §6 underspecifies behavior that will otherwise be implemented inconsistently or regress existing behavior.

### A2. Core transformations (§6 as a change list)

| # | Transformation | Source impact (verified in tree) |
|---|---|---|
| T1 | Delete `BookmarkResolver.swift` + all Cobalt calls, bot-check/captcha UI | `Engine/BookmarkResolver.swift` (delete); `BookmarksSheet.loadVideo/playViaResolver` failover branches; `ResolverResult` enum; `cobalt_api_url/key/token` UserDefaults keys; any tests referencing resolver |
| T2 | New bookmark playback chain: local → GitHub Release stream → Open on Instagram | `BookmarkPlayerOverlay.loadVideo` rewrite; **new** Release asset resolve + stream path (does not exist today — see A4); stored `videoUrl` branch fate undecided (Q2) |
| T3 | Upload newly cached bookmark videos to Releases on sync | **New** code in `GitHubBookmarkSyncService` (today it syncs only `bookmarks.json` + thumbnails; `video_asset_name` is always `nil`; no `releases` string exists under `Sources/`) |
| T4 | Completed states in `DownloadAllSheet` + `SettingsSheetView` | `DownloadAllSheet` idle branch (all-cached detection); `SettingsSheetView` (no download status exists today — new UI + disk scan) |
| T5 | `FeedCell` revert to pure `AVPlayerLayer` | `FeedCell.configure`/`prepareForReuse` in `FeedPagerView.swift`; `posterImageView` + `imageTask` removal |
| T6 | Delete `OfflineCacheBanner.swift` | Banner file + all call sites; **replacement CTA undecided** (Q5) — §2 still requires a prominent 1-tap Download action |
| T7 | Swift 6 hardening, zero warnings, CI `.ipa` | Concurrency audit; CI workflow + single-user signing plan (Q5) |

### A3. Data persistence map (what lives where after §6)

- **On-device weekly cache:** `Application Support/MediaCache/{week_id}/{reel_id}.mp4` + `{reel_id}.thumb.jpg`. Wiped on next-week rollover (`executeRolloverPurge`). Keyed by `(week_id, reel_id)`.
- **On-device bookmark vault:** `MediaCache/Bookmarks/{reel_id}.mp4` + `{reel_id}.thumb.jpg`. Survives rollover. Bound by the §5 LRU cap (25/50/100/Unlimited); eviction deletes `.mp4`, keeps SwiftData row + thumbnail.
- **SwiftData:** `BookmarkItem` rows (metadata + `videoUrlString`/thumbnail/canonical URLs), `WatchedEvent`, `AppState.currentWeekID`.
- **GitHub repo (`owner/repo`):** `bookmarks.json` (metadata) + `thumbnails/{id}_portrait.jpg` (committed blobs).
- **GitHub Releases (per §5/§6):** `{id}.mp4` assets under `v{year}` tags. **Not yet implemented** — the entire Tier-2 playback leg depends on it.
- **UserDefaults / AppStorage:** GitHub repo + PAT, LRU cap, quota/mode prefs, Cobalt keys (to be removed), sort/quota prefs.

### A4. Pre-existing scope gap the review must surface (not a question — a fact)

§6 Tier 2 ("GitHub Release Streaming") and §5 ("upload newly cached bookmark videos to GitHub Releases on sync") describe a feature with **zero current implementation**: no Release API calls, no asset upload, no asset-URL resolution, and `BookmarkSyncDTO.video_asset_name` is constructed as `nil` at every call site. So §6 is not a pure refactor — it contains one greenfield feature (Release upload + stream resolve) that Tier 2 playback depends on. The lockdown plan must sequence T3 before T2, and Q1 below pins the missing contract. Do not delete the resolver (T1) before Tier 2 works end-to-end, or bookmarks will have a window with no online leg at all.

### A5. Edge-case map (condensed; details live in the questions)

- **Bookmark playback:** never-synced bookmark (not on GitHub yet) + fresh `videoUrl` in SwiftData; evicted-by-LRU bookmark offline; PAT missing/expired (401); asset missing from Release (deleted/failed upload); private-repo streaming auth; `v{year}` tag ambiguity across calendar years.
- **Upload on sync:** large batch over cellular; partial upload failure; retry idempotency (asset already exists → replace vs skip); tag auto-creation race; sync triggered on every sheet open (must be cheap/no-op when clean).
- **Completed states:** fresh-launch `.idle` with 250/250 on disk; watched-filter interplay; partial batches; hardcoded "250" vs dynamic count; 250× `stat` cost on sheet open.
- **Feed revert:** fast flings beyond ±1 pre-buffered slots; uncached (streaming) cells with no poster; first-paint black flash; rotation/safe-area re-layout.
- **Deletion blast radius:** Cobalt UserDefaults keys; stored-URL failover observers; banner call sites and the orphaned 1-tap CTA; tests referencing deleted types.

---

## Part B — Probing Questions (5)

---

### Q1. What is the exact GitHub Release video contract (naming, tag ownership, upload trigger, streaming auth, failure fallthrough)?

**Ambiguity / failure modes.** §5–§6 name the shape (`{id}.mp4` under `v{year}`) but leave the whole contract open, and no implementation exists to fall back on:

- **Tag ownership:** is `v{year}` the bookmark's `bookmarked_at` year or the current calendar year? A Dec-2025 bookmark synced in Jan-2026 must resolve deterministically years later.
- **Upload trigger & cost:** "on sync" — but sync runs automatically on every BookmarksSheet open (per §5). An LRU-50 vault is ~375 MB; re-uploading or even re-checking 50 assets per sheet open is slow and burns cellular data.
- **Streaming auth:** private-repo Release assets need an authenticated request (`Accept: application/octet-stream` → S3 redirect). So Tier-2 playback needs the PAT at play time. What happens with no PAT, an expired PAT (401), or a public repo (unauthenticated `browser_download_url`)?
- **Failure fallthrough:** asset missing (never uploaded, deleted, failed upload), tag missing, API 5xx/rate-limit — each must land on Tier 3 ("Open on Instagram") cleanly, and offline must not spin.
- **New-device restore:** §5 promises thumbnails + metadata restore. Do videos also download, or stay stream-on-demand? (Downloading all on restore re-creates the storage problem the LRU cap solves.)
- **PAT scope:** Release asset upload needs `contents:write`. The current PAT instructions may only cover contents read/write for `bookmarks.json` — same scope, but worth stating.

**Recommended approach (single-user minimal):**

1. **Deterministic placement:** asset name `{sanitized_reel_id}.mp4`; tag `v{YYYY}` derived from the bookmark's `bookmarked_at` year, stored explicitly in the DTO (`video_asset_name` + a `release_tag` field — populate both; stop passing `nil`). Resolution never guesses the year.
2. **Upload trigger:** during GitHub sync, upload only locally cached bookmark videos whose `(release_tag, video_asset_name)` pair is absent remotely. Check presence via one `GET /repos/{repo}/releases/tags/{tag}` + its `assets[]` list per touched tag (not one call per video). Skip check entirely when the local vault hash/ledger is unchanged since last successful sync (persist `last_release_sync_fingerprint`), so routine sheet opens are a cheap no-op.
3. **Upload mechanics:** auto-create the `v{year}` release if missing; upload via the `upload_url` template; if the asset already exists, `DELETE` then re-upload (idempotent retry). Failures are per-asset, recorded, and surfaced as "N videos pending upload — retry" without failing metadata sync.
4. **Cellular guard:** uploads run only on Wi-Fi by default with a one-tap "Upload now" override. Single-user, no settings framework needed — one `Bool` in AppStorage.
5. **Streaming (Tier 2):** at play time, `GET /repos/{repo}/releases/tags/{tag}` (authenticated with stored PAT) → find asset by name → `GET {asset.url}` with `Accept: application/octet-stream`, follow redirect to S3, hand final URL to `AVPlayer`. Cache the resolved S3 URL in memory only (short-lived; never persist). Any failure (401/404/5xx/rate-limit/offline) falls through to Tier 3 with no error dialog.
6. **New-device restore:** metadata + thumbnails only (current §5 contract). Videos stream on demand via Tier 2. No bulk video download.

**Alternatives:**

- **Public repo + `browser_download_url`:** removes playback-time auth, but publishes personal bookmarks publicly. Rejected for privacy; keep the repo private.
- **Upload eagerly at bookmark time:** freshest Tier 2, but burns data unexpectedly and complicates the add path. The sync-time batch is the better single-user tradeoff.
- **Skip presence check, always attempt upload:** simpler code, but every sync re-uploads hundreds of MB. Unacceptable on cellular and slow on Wi-Fi.
- **Persist resolved S3 URLs in SwiftData:** saves one API call, but the URLs expire and create stale-playback bugs. Memory-only cache is correct.

---

### Q2. Does the stored Meta CDN `videoUrl` fast path survive, and if so where does it sit in the hierarchy?

**Ambiguity / failure modes.** Current `loadVideo` is a 3-leg chain: local file → stored `videoUrl` (if online) → Cobalt resolver (`BookmarksSheet.swift:975-990`). §6 defines a *different* 3-leg chain: local → GitHub Release → Open on Instagram. The stored-`videoUrl` leg is never mentioned. Deleting it silently is a real regression: a reel bookmarked 5 minutes ago has a fresh, playable Meta CDN URL in SwiftData but is not yet uploaded to any Release (upload happens on sync). Under a strict §6 reading, that bookmark shows "Open on Instagram" despite a playable URL sitting in the database. Conversely, keeping it naively reintroduces expired-URL hangs (403s after ~48h) and the `AVPlayerItemFailedToPlayToEndTime` → resolver failover observers that §6 wants gone.

**Recommended approach:** keep the stored URL as an explicit **Tier 1.5 fast path with silent fallthrough**, scoped to freshness:

1. Order becomes: (1) local file → (1.5) stored `videoUrl` **only if** the bookmark's `weekID == currentActiveWeekID` **and** online → (2) GitHub Release → (3) Open on Instagram.
2. The active-week gate is the expiry heuristic: active-week URLs are overwhelmingly fresh; older-week stored URLs are overwhelmingly dead, so skip them without attempting (no 403 wait, no spinner).
3. On Tier-1.5 playback failure (status `.failed` / `FailedToPlayToEndTime`), fall through to Tier 2 automatically — reusing the existing observer shape but retargeted from resolver to Release resolve. One attempt, no retries, no dialogs.
4. Document Tier 1.5 in the hierarchy comment as intentional, so a later cleanup pass does not "simplify" it away as dead code.

**Alternatives:**

- **Strict §6 3-tier (delete stored-URL branch):** simplest code and literally what §6 says, but regresses fresh-bookmark playback and forces a GitHub API round-trip (or an "Open on Instagram" dead-end) for content that would have played instantly. Not recommended.
- **Keep stored URL for all weeks with failure fallthrough:** preserves maximum playability but guarantees a slow 403/timeout spinner for every old bookmark before Tier 2 engages. The active-week gate avoids this at negligible cost.
- **Proactive expiry via HEAD request before play:** avoids the spinner but adds a blocking network round-trip to every playback. Worse than just attempting playback with fallthrough.

---

### Q3. What exactly counts as "downloaded" for the completed states, and when is it evaluated?

**Ambiguity / failure modes.** `DownloadAllCoordinator.state` is in-memory singleton state: a fresh launch is `.idle` even with 250/250 on disk, and `.completed` does not survive restart. The spec sentences assume omniscience the code does not have:

- **DownloadAllSheet:** the `.idle` branch currently shows "N reels ready to download" + Start Download. The all-cached case only surfaces indirectly (empty `effectiveReels` via the watched filter → disabled button, not the specified "Done" UI). Nobody scans the disk on sheet open today.
- **Watched-filter interplay:** with "unwatched only" on (the default), is completion "all 250 on disk" or "all unwatched on disk"? If the user watched 200 and cached 50, is that "All reels downloaded"? The honest answers differ.
- **What counts as cached:** week-dir file non-empty? Bookmark-dir copy counts? Size validation against `size_bytes`? A truncated 3 KB file passes a non-empty check and then black-screens the player.
- **Settings:** `SettingsSheetView` has no download UI at all today — the "All Reels Downloaded (250/250) + green check" row is new. Hardcoding "250" breaks on short weeks; and 250 `stat` calls on every Settings open must not hitch the UI.
- **Partial batches:** `completedWithFailures` from a previous launch is invisible after restart — is a 247/250 week "completed"?

**Recommended approach:**

1. **Single source of truth:** add one `func cache census(reels:weekID:) -> (cached: Int, total: Int)` helper (on `LibraryPathResolver` or the coordinator) using the existing `isLocalFileAvailable` (non-empty check; week dir or bookmark copy both count). No size validation — truncated-file quarantine stays a playback-time concern, out of §6 scope.
2. **Evaluate on-appear, off-main:** both sheets run the census in a background task on appear (250 `stat` calls ≈ milliseconds, but keep it off the main thread regardless) and cache the result for the sheet lifetime. The coordinator need not change state.
3. **Completion rule (both sheets):** completed iff `cached == total` over the **full** reel list, ignoring the watched filter. Rationale: "All reels downloaded" must mean all reels; the watched toggle is a download *scope*, not a completion definition. When the filter is on and everything-unwatched is cached but watched items are not, show the completed UI with a sub-caption ("50/50 unwatched cached") — honest without a second rule.
4. **Dynamic counts, never "250":** display `"All Reels Downloaded (\(cached)/\(total))"`. The spec's "250/250" is an example value, not a literal.
5. **Partial weeks:** 247/250 shows the download UI with "247 of 250 cached — Retry 3", never the completed UI. `completedWithFailures` need not persist across launches because the census recomputes reality.
6. **DownloadAllSheet completed UI:** hide Start Download, show "All reels downloaded and ready offline!" + single prominent Done (per spec). Settings row: same census, green check, tapping it opens the Download sheet (harmless, useful).

**Alternatives:**

- **Persist coordinator state across launches:** makes `.completed` durable but adds a state store that can disagree with disk reality (user deletes files via Files app, rollover purges). Census-on-appear derives truth from disk and cannot desync. Rejected.
- **Count only week-dir files (exclude bookmark copies):** stricter, but a bookmarked-then-rolled-over reel *is* playable from the vault; excluding it under-reports readiness. Rejected.
- **Byte-size validation in the census:** catches truncation but needs `size_bytes` for every reel (not always present) and slows the scan. Defer to playback-time quarantine. Rejected for §6.

---

### Q4. After the pure-`AVPlayerLayer` revert, what covers the states the poster thumbnail used to cover?

**Ambiguity / failure modes.** The revert is the right performance call — sync decode on the main thread during dequeue is unambiguous jank — but the poster was also doing three quiet jobs that §6 does not reassign:

1. **First paint / attach gap:** between cell dequeue and `AVPlayerLayer` attach + first frame, the cell is black. With ±1 pre-buffered slots this is usually invisible, but fast flings land 2+ pages away on cold slots.
2. **Uncached (streaming) cells:** reels not yet downloaded buffer from Meta CDN URLs; previously the poster showed during buffering, now it is black until first frame.
3. **Poster removal scope:** `FeedCell` also loads *remote* thumbnail URLs asynchronously (off-main, non-janking). A blanket "zero-thumbnail" revert deletes that path too — fine, but confirm it is intentional rather than collateral.

Note the current prefetch hook (`collectionView(_:prefetchItemsAt:)`) is an empty no-op, so no prefetch behavior depends on the poster.

**Recommended approach (pure revert, per spec, with two bounded safeguards):**

1. **Delete the whole poster path** (`posterImageView`, `imageTask`, both file and remote branches in `configure`). Do not keep the "harmless" async remote branch — half a poster system still pays layout/compositing cost and invites re-adding sync loads later. The spec's "zero-thumbnail architecture" is explicit; implement it literally.
2. **Accept brief black on cold attach.** For a single-user offline-first app where the normal state is "full week cached + ±1 slots hot," cold-attach black is rare and transient. No placeholder, no shimmer, no spinner — those are their own jank and complexity.
3. **Bounded safeguard 1:** ensure `attachAppropriateSlot` + `reattachPlayerToVisibleCells` run synchronously in `cellForItemAt`/scroll-settle (they already do) so attach latency, not decode latency, is the only gap.
4. **Bounded safeguard 2:** keep `prepareForReuse` detaching the layer immediately (already done) to prevent stale frames flashing on reuse — the one visual bug a pure-layer cell can produce.
5. **Acceptance:** swipe-fling test on-device (cached week): no dropped-frame hitch attributable to the cell, no stale-frame flash. Uncached-week buffering shows black-before-first-frame; that is accepted, documented behavior, not a bug.

**Alternatives:**

- **Async poster (background decode + fade-in):** preserves polish during buffering, but reintroduces image lifecycle (cancellation, fade timing, memory) into the hottest scroll path for a state (uncached browsing) the offline-first design de-emphasizes. Rejected — complexity against the 60 fps invariant.
- **In-memory pre-decoded thumbnail cache:** smooth but holds ~250 decoded images (~100+ MB) or needs its own LRU. Absurd for the marginal benefit. Rejected.
- **Extend pre-buffering beyond ±1 (e.g. ±2 slots):** covers flings but doubles `AVPlayer` memory and CDN pressure. The ±1 pool is already tuned; do not touch it in a lockdown refactor. Rejected.

---

### Q5. What is the full deletion blast radius, what replaces the banner's 1-tap CTA, and what gates the lockdown (Swift 6 + `.ipa`)?

**Ambiguity / failure modes.** Three lockdown items are stated as one-liners but each hides a decision:

**(a) Resolver deletion residue.** `BookmarkResolver.swift` is referenced by `BookmarksSheet` (resolve call, six-way `ResolverResult` switch, stored-URL failover observers), by UserDefaults keys (`cobalt_api_url`, `cobalt_api_key`/`cobalt_api_token`), and possibly by tests and `GitHubSyncModal`/settings strings. "Delete the file" without the residue list leaves dead keys, dead branches, and failing tests. The offline badge copy ("Online required for older bookmarks") also dies with the resolver — Tier 3 needs its replacement copy for the offline case (tapping "Open on Instagram" with no network currently does nothing observable).

**(b) Banner deletion orphans the 1-tap CTA.** `OfflineCacheBanner` is the §2-mandated "auto-prompt / 1-tap prominent Download Week for Offline action on the home screen." §5 says "remove feed banner" (presentation hardening) and §6 says "delete `OfflineCacheBanner.swift`" — but nothing names the replacement CTA. Deleting it with no replacement silently drops a confirmed §2 requirement and strands the Download flow behind Settings.

**(c) Lockdown gates are unverifiable as written.** "100% Swift 6 strict concurrency + zero warnings" needs a named build invocation (is `SWIFT_STRICT_CONCURRENCY=complete` set? warnings-as-errors in CI?). "Package `.ipa` via GitHub Actions macOS CI" needs a single-user signing story (personal team, ad-hoc/sideload, provisioning profile storage) — without it, CI produces an unsigned artifact the iPhone cannot install.

**Recommended approach:**

1. **Deletion manifest (single commit, reviewable):** delete `BookmarkResolver.swift`, `OfflineCacheBanner.swift`; remove the `ResolverResult` enum, all `playViaResolver` paths, stored-URL failover observers (retargeted per Q2, not orphaned), `cobalt_*` UserDefaults keys (with a one-time `UserDefaults.removeObject` migration so stale keys do not linger), resolver copy in tests/modals; delete `OfflineCacheBanner` call sites. Enumerate the manifest in the commit message. Grep-gate: `rg -i cobalt` and `rg OfflineCacheBanner` return zero hits under `Sources/` and `Tests/`.
2. **CTA replacement (smallest correct):** move the 1-tap action into the existing feed header as a compact download button with progress ring (the header already hosts pills/buttons; reuse its layout). Behavior: tap → open `DownloadAllSheet`. Auto-prompt ("new week available" alert with Download action) stays as the §2 auto-prompt. This preserves the confirmed requirement with no new component.
3. **Offline Tier-3 copy:** when offline and Tier 1+2 are unavailable, the bookmark player shows "You're offline — this bookmark isn't downloaded" + the Open-on-Instagram button disabled-but-visible (tapping it offline is a no-op with the same honest label, never a spinner). No scary dialogs, per spec.
4. **Swift 6 gate:** enable `SWIFT_STRICT_CONCURRENCY=complete` (or Xcode 16 "Complete" mode) in the package/project settings, fix all resulting diagnostics (expect `DownloadAllCoordinator`'s `@unchecked Sendable`, `AVPlayerPool` shared-state access, and detached-task captures to need attention), and add a CI step that fails on any warning (`-warnings-as-errors` for the app target). "Zero warnings" is then machine-checked, not eyeballed.
5. **`.ipa` gate:** CI builds with the personal Apple ID team (manual signing, ad-hoc profile for the one iPhone; profile + cert stored as GitHub Actions secrets). Document the exact `xcodebuild -exportArchive` invocation and the sideload step (AltStore/sideloadly/Xcode Devices) in one short `docs/RELEASE.md`. First green CI `.ipa` that installs and launches on the user's phone is the lockdown exit criterion — not just "CI passed."
6. **Freeze definition:** after the exit criterion, `main` accepts only break-fix (crash/data-loss) commits; everything else is a new dated proposal. Say this in one paragraph at the top of the changelog so "locked" has an operational meaning.

**Alternatives:**

- **Leave stale `cobalt_*` keys / dead branches "for history":** zero benefit (git has history), real cost (confusion, grep noise, accidental reuse). Rejected.
- **Replace the banner with a new floating action button:** new component, new layout edge cases, new tests — for a CTA the header can already host. Rejected as lockdown-scope creep.
- **CI unsigned `.xcarchive` only, sign locally:** simpler CI, but the "final compiled `.ipa`" deliverable is then unbuilt and untested by the pipeline. For a one-phone sideload the ad-hoc CI path is only marginally harder and actually verifies the artifact. Rejected.
- **Warnings-as-errors across all targets including tests:** tempting, but third-party/test-target warnings can block the lockdown on trivia. Scope strictness to the app target; tests stay warning-clean by convention.

---

## Part C — Lockdown Acceptance Gates (suggested)

Derived from the recommendations above; each is checkable by one person in one pass:

1. **Tier order:** bookmark plays from local vault offline (airplane mode) → evicted bookmark streams via Release on Wi-Fi → unknown/failed bookmark shows Open on Instagram with no dialog; offline+evicted shows the honest offline label.
2. **Upload:** fresh bookmark + GitHub sync → `{id}.mp4` appears under the correct `v{year}`; second sync uploads nothing (presence-check no-op); failed upload retries cleanly (delete-then-upload, no duplicate assets).
3. **Fresh-URL fast path:** active-week bookmark, week file deleted, online → plays via stored URL; same setup in airplane mode → Tier 2/3 path, no hang.
4. **Completed states:** fresh install-launch with full week on disk → Download sheet shows completed UI + Done only; Settings shows `All Reels Downloaded (N/N)` + green check; counts are dynamic, census runs off-main.
5. **Feed:** on-device fling through cached week — no dequeue hitch (Instruments optional; eyeball-acceptable for personal use), no stale-frame flash on reuse.
6. **Deletions:** `rg -i cobalt` and `rg OfflineCacheBanner` clean; header CTA opens Download sheet; auto-prompt fires on new week.
7. **Concurrency + warnings:** Complete-mode build, zero warnings, CI enforces it.
8. **Artifact:** CI `.ipa` installs and launches on the user's iPhone; `docs/RELEASE.md` records the exact resign/sideload steps.
9. **Freeze:** changelog paragraph declares break-fix-only status.

---

## Explicit Non-Goals (per the personal-use anchor)

No provider abstraction for resolvers (the resolver is being deleted, not interfaced). No multi-account/PAT-vaulting beyond one AppStorage string. No upload resumability framework (per-asset retry suffices for ~10 MB files). No analytics/telemetry to "verify" the 60 fps invariant beyond on-device feel. No server-side component for Release management — the phone + `gh` API calls are the whole system.
