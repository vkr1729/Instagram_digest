# Audit Findings: arch/direct-meta-stream (§6 Final Refactor)

Lead-auditor adversarial review of the workspace on branch `arch/direct-meta-stream`
against `.workflow/REQUIREMENTS.md` (§1-§6) and `.workflow/IMPLEMENTATION_PLAN.md`
(Phases A-F). Read-only audit: no source files modified. Machine ledger:
`.workflow/AUDIT_LEDGER.json` (14 entries, schema-validated).

Scale respected: single-user personal use (offline MRT iPhone playback). No
enterprise findings; every remediation is sized to this codebase.

Result: **0 P0 · 4 P1 · 10 P2.** No deterministic crash, unrecoverable
corruption, or wholly broken invariant. The P1s are one backup-loss risk, one
latent crash/concurrency violation, one false offline-ready state, and one
workspace-vs-tip divergence that invalidates CI artifacts.

## P1 — fix before repo freeze

### AUDIT-001 — Phantom video_asset_name suppresses future upload, poisons LRU (swift_engine)
`Sources/InstagramDigest/Engine/GitHubBookmarkSyncService.swift:538`.
Step 4 claims `video_asset_name` for any bookmark with a local file even when
upload was skipped (cellular) or failed. The phantom persists to
`bookmarks.json`, makes `remoteHasAsset` true forever (no self-healing), gets
imported into the local row, and then prioritizes that file for LRU eviction —
after which the video exists nowhere. One cellular sync can poison a batch.
Fix: push `video_asset_name` only from confirmed `local.videoAssetName`.

### AUDIT-002 — @Model objects read on background thread in Task.detached (swift_ui)
`Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift:405`. `@Query`
`BookmarkItem` rows are read inside `Task.detached`: cross-thread
ModelContext access and a Swift 6 strict-concurrency violation. Snapshot
Sendable `(weekID, reelID)` tuples on the MainActor first.

### AUDIT-003 — False Completed state under watched-filter (swift_ui)
`Sources/InstagramDigest/Views/Modals/DownloadAllSheet.swift:103`.
`isAllDownloaded || pendingReels.isEmpty` shows "All reels downloaded and
ready offline!" when the unwatched-only filter merely yields zero pending
(e.g. watched 250, downloaded 0). Gate completed UI strictly on census
`cached == total`; add a distinct filter-empty state.

### AUDIT-004 — §6 changes uncommitted; branch tip still contains excised files (ci_cd)
Workspace holds ~20 uncommitted modifications plus unstaged deletions of
`BookmarkResolver.swift` and `OfflineCacheBanner.swift`; both still exist at
the branch tip. CI builds the tip, so every IPA/test result reflects
pre-excision code. Commit, push, and re-run CI before declaring the freeze.

## P2 — fix or explicitly accept

| ID | Area | Finding |
|----|------|---------|
| AUDIT-005 | swift_ui | DownloadAllSheet census sync on main + per-body pendingReels stat storm (`DownloadAllSheet.swift:402`) |
| AUDIT-006 | swift_ui | SettingsSheetView census on MainActor via inherited `.task` (`SettingsSheetView.swift:191`) |
| AUDIT-007 | swift_ui | Stale `_resolver` badge identifier on Tier-3 web badge (`BookmarksSheet.swift:279`) |
| AUDIT-008 | swift_engine | Tier-2 asset request uses untrimmed PAT; whitespace-padded tokens 401 to Tier 3 (`GitHubBookmarkSyncService.swift:125`) |
| AUDIT-009 | swift_engine | Asset listing capped: embedded first page on resolve, per_page=100 on upload; follow Link pagination (`GitHubBookmarkSyncService.swift:115`) |
| AUDIT-010 | swift_engine | Optimistic relative thumbnail_url + silent detached-PUT failure breaks restore fallback (`GitHubBookmarkSyncService.swift:544`) |
| AUDIT-011 | swift_engine | Bookmark add copies full video on @MainActor (`BookmarkController.swift:106`) |
| AUDIT-012 | ci_cd | CI has no zero-warnings gate; strict-concurrency violations compile silently (`.github/workflows/build-ipa.yml:99`) |
| AUDIT-013 | ci_cd | Missing mandated tests: coalescing, cellular gate, evict-priority, 422, template strip, MockURLProtocol 302 (`DirectStreamTests.swift:889`) |
| AUDIT-014 | python_pipeline | Migration omits video_asset_name/release_tag, passes empty bookmarked_at (breaks Tier-2 year to v1) (`scripts/migrate_r2_bookmarks_to_gh.py:161`) |

## Invariants — verification outcome

1. **Playback priority Tier 1 → 1.5 → 2 → 3: HOLDS** (with AUDIT-001/008/009
   weakening Tier 2 in specific cases). Tier 1 local vault, Tier 1.5 stored
   Meta URL with single-shot observer fallthrough, Tier 2 ephemeral-session
   302 capture with 4-min memory cache, Tier 3 clean "Open on Instagram" /
   offline-unavailable states all verified in `BookmarksSheet.swift:934-1099`.
2. **Excision of resolver/banner/Cobalt: HOLDS in workspace, FAILS at tip.**
   Both files deleted, zero resolver references, zero `UIImage(contentsOfFile:)`,
   cobalt strings limited to the mandated purge block + test scrubs. But see
   AUDIT-004: deletions are uncommitted.
3. **FeedCell 60 FPS: HOLDS.** Pure `PlayerContainerView`/`AVPlayerLayer`
   attachment, detach-on-reuse, black backgrounds, no disk reads in
   `configure`/`prepareForReuse`. Grid/bookmark thumbnails load via AsyncImage.
4. **Honest Completed states: PARTIAL.** Settings row correctly gated on
   census; DownloadAllSheet completed block is polluted by the filter-empty
   case (AUDIT-003), and both census evaluations run on-main (AUDIT-005/006).
5. **Silent sync & local vault: HOLDS** (with AUDIT-001 poisoning input).
   isSyncing/needsResync coalescing with deferred follow-up, triggers on
   add/remove/foreground/sheet-open, cellular upload gate, count-based LRU
   (default 50, 0 = unlimited) evicting uploaded-first while preserving
   thumbnails, tombstones cleared only after confirmed push.

## Verified clean (corroborated, not just claimed)

- Python pipeline: `DIRECT_STREAM_MODE` bypasses R2/healthcheck/notifier
  coherently; `direct_pages_manifest_url` contract pair matches Swift;
  `write_direct_manifest` strips R2 keys, enforces Meta-CDN-only URLs and the
  60% deploy floor; shortfall/reconcile/deploy-refusal gates intact.
- `pytest tests/test_direct_stream_mode.py`: **29 passed** (re-run this session).
- Swift models: `video_url`/`size_bytes`/canonical decoding incl. direct-mode
  R2 rejection; UTC `releaseTag` boundary; DTO round-trip with asset fields.
- Rollover purge protects LivePinSet + incoming batch; `.part` promotion
  bounds; tombstone zombie protection on both sync paths.

## Notes (not findings)

- Plan names the LRU cap `MediaCacheManager.pruneBookmarksToCap`; the code
  implements identical behavior as `BookmarkController.enforceCachedVideosCap`.
  Naming drift only — behavior matches the invariant.
- Exit criterion "rg -i cobalt returns 0 hits" is unsatisfiable as written:
  the plan itself mandates a purge block naming the cobalt keys. The
  implementation is correct; the criterion needs a carve-out.
- Pre-upload R2 purge invariant untouched; per AGENTS.md §5 it was not
  flagged and must not be "fixed".
