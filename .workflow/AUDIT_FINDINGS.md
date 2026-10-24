# Adversarial Audit Findings — arch/direct-meta-stream

Merged from 4 lane reports (python_pipeline, swift_engine, swift_ui, ci_cd).
53 raw findings → **43 merged** (9 merges, 10 duplicates absorbed).
Severity: **6 P0 / 18 P1 / 19 P2**. Source P3s normalized to P2 per merge contract.
Machine ledger: `.workflow/AUDIT_LEDGER.json` (all status PENDING).

## P0 — must fix before merge

### AUDIT-001 — Unplayable reels ship in direct manifest; deploy gates neutered
- File: `main.py:2243` (python_pipeline)
- Issue: Direct branch maps every ranked reel to uploaded_url_map including '' for reels lacking a Meta URL, sets dropped=[] and never filters, so zero-playable batches still save/build/deploy dead-card manifests; the deploy gate needs only 1 good URL.
- Remediation: Validate then filter in the direct branch exactly like the legacy dropped-filter (is_direct_stream_url check, playable/dead split, warn with dead ids, ranked_reels=playable, dropped=dead); leave the :2363/:2385 gates untouched.

### AUDIT-002 — Direct-stream tests purge the real app container
- File: `Tests/InstagramDigestTests/DirectStreamTests.swift:346` (swift_engine)
- Issue: Rollover-purge tests call MediaCacheManager.shared against the real app container, permanently wiping a device's offline cache when the suite runs; routing test also writes into the real container.
- Remediation: Never touch .shared in tests — instantiate with a temp-rooted path resolver (add a baseURL override for tests); or gate to simulator-only temp containers.

### AUDIT-003 — Empty-batch Start wipes old week and reports success
- File: `Sources/InstagramDigest/Views/Modals/DownloadAllSheet.swift:172` (swift_ui)
- Issue: With unwatchedOnly on and all reels watched, Start is still enabled; startDownloadAll([]) runs rollover purge (wipes prior week, resume data, ledger) then reports .completed, and the banner falsely claims full offline readiness.
- Remediation: Disable Start/Retry when effectiveReels is empty with an explanatory state; belt-and-braces early-return in startDownloadAll on empty batch before rollover.

### AUDIT-004 — Cobalt resolver broken live: single hardcoded instance, no auth/fallback
- File: `Sources/InstagramDigest/Engine/BookmarkResolver.swift:44` (ci_cd; merges ci_cd + swift_engine + config findings)
- Issue: Live POST returns 400 error.api.auth.jwt.missing — the public instance requires JWT but no Authorization header is sent, so every older-bookmark resolution fails. Single hardcoded endpoint, no fallback, no user override; Python COBALT_RESOLVER_API_URL is dead config.
- Remediation: User decides self-host vs API key; then add Authorization header + endpoint override from Settings/UserDefaults as the single source of truth, document one fallback, add a request-shape contract test plus an opt-in live smoke test.

### AUDIT-005 — Legacy Swift test fails under hardcoded directMode (CI red)
- File: `Tests/InstagramDigestTests/AuditFixesTests.swift:172` (ci_cd)
- Issue: testPendingRemoteBookmarksDrainOnRetry fails on this branch (directMode=true stub returns true, XCTAssertFalse fails); CI runs the whole bundle so build-ipa.yml goes red.
- Remediation: Gate the legacy test on !directMode (or inject a legacy-mode service), mirroring the Python conftest pin.

### AUDIT-006 — Empty video_url ships; iOS silently drops reels (count mismatch)
- File: `site_builder.py:99` (ci_cd)
- Issue: Writer emits video_url='' for non-Meta hosts; ReelItem init throws so DigestManifest lossy-drops those reels while count stays N; the >=1-URL gate lets partially-emptied manifests ship.
- Remediation: Drop empty-URL items in the writer and set count=len(items), or fail loudly below a fill threshold; add Python no-empty-URL and Swift lossy-skip/count tests.

## P1 — should fix before merge

### AUDIT-007 — r2_url field contaminated with Meta CDN URLs in persisted digest
- File: `main.py:2351` (python_pipeline)
- Issue: The persist block runs in both modes, writing Meta CDN URLs into r2_url; ranker persists full dicts, so digest JSON permanently stores Meta URLs as r2_url, corrupting later legacy-mode resume/expand/reconcile.
- Remediation: Gate by mode — in direct mode set video_url only and pop r2_url; add a test asserting no r2_url key after direct-mode save.

### AUDIT-008 — Direct deploy gate checks wrong keys with single-URL threshold
- File: `site_builder.py:619` (python_pipeline)
- Issue: Gate passes on ANY single http URL across video_url/r2_url/video_cdn_url; blanked video_url manifests still pass via stale video_cdn_url, as does 1-live/249-dead. It does not measure what the app consumes.
- Remediation: In direct mode check only video_url with is_direct_stream_url, count hits, require a quorum (max(1, 60% of items)); log good/total. Legacy path unchanged.

### AUDIT-009 — Serve-check exception path returns deploy success
- File: `site_builder.py:784` (python_pipeline)
- Issue: The outer except around serve verification warns and falls through to writing the deployed-week marker and returning True — a verification failure is reported as success and later runs treat an unserved week as live.
- Remediation: Fail closed — log at error and return False in the outer except; keep inner per-poll retry; add a test asserting False + no marker when urlopen raises.

### AUDIT-010 — Static marker path bypasses DATA_DIR isolation; tests pollute live data/
- File: `site_builder.py:816` (python_pipeline; merges python_pipeline + ci_cd findings)
- Issue: Marker written to import-time-bound DIRECT_DEPLOYED_WEEK_FILE ignores DATA_DIR redirects, so tests write into live data/; the tripwire does not cover that filename.
- Remediation: Add config.direct_deployed_week_file() (DATA_DIR-derived), replace the call site, monkeypatch it in fixtures, extend the tripwire, assert tmp placement.

### AUDIT-011 — Direct-mode expand reuses expired/stale CDN URLs for existing items
- File: `main.py:2837` (python_pipeline)
- Issue: Expand seeds existing-item URLs from stored fields (week-old, near-certainly 403 after 48–72h expiry) and returns stored video_cdn_url verbatim; legacy R2 URLs get blanked, turning pre-existing items into dead cards.
- Remediation: Seed only is_direct_stream_url-validated URLs with a staleness warning, run the AUDIT-001 playable filter and refuse deploy below MIN_DEPLOY_ITEMS, document that expand cannot refresh URLs (recommend full sync).

### AUDIT-012 — Python direct-mode test gaps: no legacy-parity, weak bypass coverage
- File: `tests/test_direct_stream_mode.py:97` (python_pipeline; merges python_pipeline P1 + ci_cd P2)
- Issue: No test_legacy_parity; the bypass test mocks an unused path and asserts only return codes without exercising Phase 5D/s3 gating/expand bypass/deploy routing; missing build-only/deploy-only, expand-direct, URL-edge, and prod-bytes tests.
- Remediation: Add legacy-parity (assert R2 stubs called when DIRECT False), harden bypass with AssertionError stubs on all R2 call sites across the real helpers, add the missing cases.

### AUDIT-013 — Force-unwrap crash on hostile bookmark reelID
- File: `Sources/InstagramDigest/Models/DigestState.swift:91` (swift_engine)
- Issue: Non-optional canonicalUrl force-unwraps a reelID-interpolated URL; URL-invalid characters from a malformed manifest crash on access. ReelItem.canonicalUrl is safely Optional — inconsistent.
- Remediation: Sanitize reelID before interpolation, or make it Optional like ReelItem and update the two call sites.

### AUDIT-014 — Expired-URL 403/410 has no manifest-refresh recovery
- File: `Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift:409` (swift_engine; merges swift_engine + ci_cd findings)
- Issue: Plan 3.2's pause + debounced refresh is absent; any non-2xx below 500 is terminal, so an expired manifest yields ~250 individual failures and Retry replays dead URLs.
- Remediation: On first 403/410 suspend once, refetch the direct manifest, rebuild pending videoUrls by id, resume; fail fast with 'Links expired' if refresh fails; add a stubbed-403 test.

### AUDIT-015 — Partial batch success misreported as total failure
- File: `Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift:21` (swift_engine)
- Issue: Shipped states lack ready/failed counts and any single failure maps to .failed, so 249/250 success reports total failure and the banner shows 'Download paused'.
- Remediation: Adopt the plan states or add .completedWithFailures(ready:failed:) with targeted retry of failed items only.

### AUDIT-016 — Unsave plays next bookmark after player unmount
- File: `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:875` (swift_ui)
- Issue: handleUnsave tears down and unmounts the player, then loadVideo for the next bookmark can create a player, activate audio, and fire a resolve on a detached view; teardown races the new player.
- Remediation: Return early after onClose/onDeleteBookmark without loadVideo; let the parent re-present if desired.

### AUDIT-017 — Synchronous FS scans in view bodies (banner + grid)
- File: `Sources/InstagramDigest/Views/Feed/OfflineCacheBanner.swift:55` (swift_ui; merges swift_ui P1 + swift_engine P3)
- Issue: Banner body stats up to 250 mp4s on the main thread on every state publish; grid cells stat per cell per render. Main-thread I/O jank during progress updates.
- Remediation: Compute counts off-body in .task/onChange with @State cache, or publish a cached count from the coordinator; never stat in body.

### AUDIT-018 — Grid badge / playback presence mismatch on zero-byte files
- File: `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:144` (swift_ui)
- Issue: Badge ORs a size-checked predicate with bare fileExists, reviving zero-byte files as present while loadVideo rejects them — badge says ready, playback falls to the resolver. Also double-stats every cell.
- Remediation: Use resolvedLocalFileURL(...) != nil (or isLocalFileAvailable alone) as the single badge predicate.

### AUDIT-019 — @State mutated off main actor after purge/delete
- File: `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:298` (swift_ui)
- Issue: performFreeStorage and deleteBookmark spawn unstructured Task {} from a non-@MainActor View and mutate @State off the main actor.
- Remediation: Use Task { @MainActor in ... } for both, or move ledger refresh into .task/onChange.

### AUDIT-020 — Player offline error mislabels current-week reels
- File: `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:802` (swift_ui)
- Issue: Offline-matrix violation — uncached current-week bookmarks show 'Online connection required for older bookmarks' with no 'Download required' path though REQUIREMENTS mandates it; one icon for all failure kinds.
- Remediation: Branch .offline on weekID == currentActiveWeekID (current → 'Download required' + open sheet; older → 'Online required'); per-case icons.

### AUDIT-021 — CI commits unsigned IPA binaries to git (repo bloat)
- File: `.github/workflows/build-ipa.yml:127` (ci_cd)
- Issue: Every push force-adds multi-MB unsigned IPAs into dist/ipa/, bloating the repo; the artifact cannot install without manual re-sign.
- Remediation: Upload IPA as workflow artifact only; stop committing binaries (no retention service — single user).

### AUDIT-022 — Empty direct manifest returned, not rejected
- File: `Sources/InstagramDigest/Engine/DigestDataService.swift:64` (ci_cd; merges ci_cd P1 + swift_engine P2)
- Issue: Plan 2.2's atomic rejection is missing — an empty manifest is not cached but still returned, propagating an empty week into the feed instead of falling back to manifest_cache_direct.json.
- Remediation: In direct mode throw/skip to cache fallback on empty items; add a DirectStreamTests case.

### AUDIT-023 — Direct mode can still serve production R2 manifest via bundle fallback
- File: `Sources/InstagramDigest/Engine/DigestDataService.swift:32` (ci_cd; merges ci_cd P1 + swift_engine P3)
- Issue: No channel enforcement and bundle fallback to production data.json (R2 URLs) stays active in direct mode, violating 'never fall back to R2'. Latent today (no bundled data.json) but one build away from live.
- Remediation: In direct mode skip bundle fallback unless channel==direct (or restrict to -ui-testing); optionally assert channel on decode.

### AUDIT-024 — Direct channel never prunes stale gh-pages thumbnails
- File: `site_builder.py:741` (ci_cd; merges ci_cd P1 + python_pipeline P2)
- Issue: Additive thumbnail overlay never deletes; each weekly deploy strands ~250 jpgs (~25–50 MB/week) plus stale addressable thumbs for purged weeks.
- Remediation: Prune destination thumbs not referenced by the incoming manifest (excluding .tmp-*) before prod-sha re-verification; log count; add a test.

## P2 — fix opportunistically / follow-up

### AUDIT-025 — Direct manifest advertises thumbnails that may not exist
- File: `site_builder.py:107` (python_pipeline; merges python_pipeline + ci_cd findings)
- Issue: Thumbnail/poster URLs stamped unconditionally; missing ffmpeg or failed renders yield gh-pages 404s. Legacy path falls back to apple-touch-icon.png.
- Remediation: Stamp jpg URLs only when the file exists (resolve against output_path.parent/'thumbnails'), else legacy-style fallback; add a test.

### AUDIT-026 — extra_manifest can clobber reserved keys; budget_capped dropped
- File: `site_builder.py:125` (python_pipeline)
- Issue: payload.update(extra_manifest) can overwrite schema keys, silently corrupting the contract; separately build_site's direct call drops budget_capped while legacy keeps it.
- Remediation: Allowlist-merge refusing reserved keys with a warning; thread budget_capped through the direct call.

### AUDIT-027 — Blanked video_urls are silent; no dropped-reel logging
- File: `site_builder.py:99` (python_pipeline)
- Issue: Invalid-URL reels are blanked with no per-reel log; systemic causes (expired scrape, new Meta host) ship as dead manifests with zero diagnostic trail.
- Remediation: Collect blanked ids and log a warning with count/total/sample; keep blanking behavior, add observability only.

### AUDIT-028 — Direct reconcile returns success while orphaning legacy outbox
- File: `main.py:566` (python_pipeline)
- Issue: Direct reconcile returns 0 before reading the outbox; a non-empty legacy outbox's parked reels are permanently orphaned with success reported. The :584-586 guard is unreachable dead code.
- Remediation: Check for a stranded outbox first — warn with counts and return 1; delete the duplicate guard.

### AUDIT-029 — No gh-pages bootstrap path when branch is absent
- File: `site_builder.py:710` (python_pipeline)
- Issue: Clone of a nonexistent gh-pages branch returns False with no recovery except a manual legacy deploy.
- Remediation: On branch-not-found stderr, init/push a fresh gh-pages branch (skipping the prod invariant only here, with a warning); other clone errors still return False.

### AUDIT-030 — Rollover purge leaves stale .cached bookmark rows
- File: `Sources/InstagramDigest/Engine/MediaCacheManager.swift:204` (swift_engine)
- Issue: Purge deletes Bookmarks/*.mp4 and zeroes the ledger but never flips rows to .evicted; rows lie until next reconcile and freeBookmarkStorage iterates phantom .cached rows.
- Remediation: Flip affected rows (.evicted/sizeBytes=0) in the same transaction, or call reconcileBookmarkStorageLedger() at purge end.

### AUDIT-031 — Re-bookmark silently keeps stale weekID on duplicate
- File: `Sources/InstagramDigest/Engine/BookmarkController.swift:60` (swift_engine)
- Issue: Blind insert on @Attribute(.unique) reelID throws on duplicates, rolls back, and keeps the old weekID — file checks look in the purged old dir and miss the new copy.
- Remediation: Fetch by reelID first; update weekID/canonicalUrl/sizeBytes/metadata in place when a row exists.

### AUDIT-032 — Missing one-time legacy Bookmarks/ migration
- File: `Sources/InstagramDigest/Engine/MediaCacheManager.swift:183` (swift_engine)
- Issue: Plan 4.2's one-time purge of legacy isolated Bookmarks/*.mp4 is missing; up to 1.5 GB lingers until the first Download Week tap, silently consuming budget.
- Remediation: On first direct-mode launch (UserDefaults one-shot flag), delete legacy copies, zero the ledger, reconcile.

### AUDIT-033 — Preflight-after-purge can strand user with zero weeks
- File: `Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift:114` (swift_engine)
- Issue: Rollover purge runs before preflight; if preflight then fails the user has zero offline weeks — old cache destroyed, new batch refused.
- Remediation: Run a pre-purge preflight counting reclaimable bytes; abort before wiping when post-purge capacity still cannot fit; keep post-purge preflight as final gate.

### AUDIT-034 — Rollover purge bypasses LivePinSet protection
- File: `Sources/InstagramDigest/Engine/MediaCacheManager.swift:190` (swift_engine)
- Issue: Purge bulk-deletes without consulting LivePinSet and does no pause/cancel itself — any future caller skipping the preamble unlinks files under the player.
- Remediation: Move pause + download suspension into the purge (or assert quiescence at entry); document the caller contract.

### AUDIT-035 — Challenge HTML on HTTP 200 misclassified as generic failure
- File: `Sources/InstagramDigest/Engine/BookmarkResolver.swift:92` (swift_engine; source P3 → P2)
- Issue: A 200 with an HTML (Turnstile/challenge) body fails JSON parsing and returns generic .failed instead of .authOrChallengeRequired.
- Remediation: Check Content-Type for text/html (or sniff '<html') before JSON parse; return .authOrChallengeRequired.

### AUDIT-036 — Resolver retry ignores task cancellation
- File: `Sources/InstagramDigest/Engine/BookmarkResolver.swift:68` (swift_engine; source P3 → P2)
- Issue: resolveWithRetry sleeps then retries without checking Task.isCancelled, firing network requests after swipe-away.
- Remediation: Check Task.isCancelled after each sleep and before retry; return .failed('cancelled') or rethrow CancellationError.

### AUDIT-037 — Banner overclaims completion, mislabels failed/paused
- File: `Sources/InstagramDigest/Views/Feed/OfflineCacheBanner.swift:46` (swift_ui)
- Issue: .completed prints manifest count for a possibly unwatched-only subset; partial failures hidden; 'Download paused' shown for terminal errors; .paused grouped with .idle renders the CTA.
- Remediation: Show coordinator completed/total + failed count, distinct 'failed' vs 'paused' copy, and a dedicated .paused branch.

### AUDIT-038 — Stale R2 storage gauge shown in direct mode
- File: `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:46` (swift_ui)
- Issue: The x/1.5GB gauge, pending label, and Free Storage button are legacy-R2 concepts showing permanently-zero values in direct mode while the real ~3GB feed cache is unrepresented.
- Remediation: In directMode hide/replace the gauge with feed-week cache size, or hide the button.

### AUDIT-039 — Silent black screen when no playable URL exists
- File: `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:783` (swift_ui)
- Issue: If bookmark.videoUrl is nil, loadVideo silently returns — black screen with HUD, no feedback or retry.
- Remediation: Set resolutionError with a retry affordance when neither local file nor remote URL exists (both modes).

### AUDIT-040 — Direct-mode badges untestable, week fallback lossy
- File: `Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:163` (swift_ui)
- Issue: Direct-mode badges lack accessibilityIdentifiers (legacy ones have them), breaking UI-test coverage of the Phase 5.2 contract; empty activeWeekID collapses download-required into the globe branch.
- Remediation: Add BookmarkBadge_<index>_<state> identifiers; treat empty activeWeekID as unknown.

### AUDIT-041 — Unpinned deps: pip floors, xcodegen, Xcode toolchain
- File: `requirements.txt:1` (ci_cd)
- Issue: >= floors in requirements, unpinned xcodegen/Xcode on macos-15, actions on @v5 majors — drift surface for non-reproducible builds.
- Remediation: Pin xcodegen + explicit Xcode in CI; consider freezing requirements. No lockfile service (single user).

### AUDIT-042 — No documented re-sign/install runbook for unsigned IPA
- File: `.github/workflows/build-ipa.yml:105` (ci_cd)
- Issue: The unsigned pipeline's re-sign/install path exists only as workflow echo lines; no in-repo runbook.
- Remediation: Add a short docs note with exact re-sign + install steps. No enterprise signing infra.

### AUDIT-043 — Swift direct-stream test gaps + tautological routing test
- File: `Tests/InstagramDigestTests/DirectStreamTests.swift:96` (ci_cd; rollover pollution split out to AUDIT-002)
- Issue: No end-to-end manifest decoding test; routing test asserts a hardcoded constant; resolver request shape/retry/5xx/timeout/non-HTTPS cases unasserted.
- Remediation: Golden-sample decode test, request-shape + retry-count assertions, retry/5xx/timeout/non-HTTPS cases; see AUDIT-002 for isolation.

## Carried gaps (unresolved, from lane reports)

- Meta CDN host allowlist completeness (yt-dlp emitted hosts vs fbcdn.net/cdninstagram.com) — needs a real extractor sample; no network runs per AGENTS.md.
- Cobalt self-host vs API-key decision is a user call; rate limits and full POST contract beyond the confirmed JWT gate unverified.
- On-device behavior of AVPlayer slots whose file is unlinked mid-playback — code-reviewed only.
- Full-batch UX under real 48h-expiry pressure — no device/simulator run performed (read-only audits).
- XCUITest owner-key prompt tolerance of direct-mode bypass — toggleBookmark UI path not fully traced.
