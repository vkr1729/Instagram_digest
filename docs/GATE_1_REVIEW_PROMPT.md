# Gate 1 Re-Review: Core Foundations & Engine Audit (Remediation Iteration)

Review the remediated iOS Core Foundations & Engine against `docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md` and the 10 P0 items identified in the previous review:

Key remediations applied:
1. **Bookmark Isolation & Ledger Accounting:** Bookmarks now use isolated directory `MediaCache/Bookmarks/{reel_id}.mp4`. Reconcile only checks this directory and never auto-flips feed files to `.cached`. Pre-rejects admissions > 1.5 GB. Ledger decrements only on verified deletion.
2. **Purge Filename Parsing:** Strips `.part` and `.mp4` suffixes cleanly via `deletingPathExtension` and compares sanitized to sanitized against `LivePinSet`.
3. **Stall Watchdog & Serialized Deletion:** First local stall suspends downloads for 10s and retries local playback; only on second consecutive stall is eviction routed via actor `evictLocalFeedFile` and falls back to remote.
4. **KVO Status & Failure Observation:** Added `AVPlayerItem.status` KVO publisher and `AVPlayerItemFailedToPlayToEndTime` notification observer. Checked item equality and triggers failure ladder.
5. **Media Reset Restoration:** Added `rebuildCurrentSlot(restoringTo:)` capturing `currentTime` and restoring position after session re-initialization.
6. **Throttled 80% Milestone:** 0.5s ticks with `watchedLatchedReelIDs` once-per-reel latch.
7. **Bounded Download Retries:** Max 3 retries per reel; failures accounted for so batch completes without looping indefinitely.
8. **Watchdog Resume:** Checked `!Task.isCancelled` after `Task.sleep` and routed through `@MainActor` without premature resumption.
9. **Lossy JSON Decoding:** Implemented lossy element-by-element decoding in `DigestManifest`.
10. **Test Target Directory:** Created `Tests/InstagramDigestTests/` with comprehensive unit tests (`ModelTests.swift`, `ResolverTests.swift`, `EngineTests.swift`).
11. **P1/P2 Items:** Marked `Slot` `@MainActor`; tasks run in `@MainActor`; audio session deactivates on pause; immediate concurrency preemption (3 -> 1 on play); persistent `resumeData` in `MediaCache/ResumeData/`; cleaned up `Info.plist` (no arbitrary loads, foreground-only).

Files to review:
1. `project.yml`
2. `Sources/InstagramDigest/Info.plist`
3. `Sources/InstagramDigest/Models/ReelItem.swift`
4. `Sources/InstagramDigest/Models/DigestManifest.swift`
5. `Sources/InstagramDigest/Models/DigestState.swift`
6. `Sources/InstagramDigest/Engine/LibraryPathResolver.swift`
7. `Sources/InstagramDigest/Engine/MediaCacheManager.swift`
8. `Sources/InstagramDigest/Engine/AVPlayerPool.swift`
9. `Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift`
10. `Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift`
11. `Sources/InstagramDigest/Engine/DigestDataService.swift`
12. `Tests/InstagramDigestTests/`

Verify whether all P0 and P1 issues are resolved and the engine is approved.
If approved, state explicitly:
`## Verdict: GATE 1 ENGINE APPROVED`
