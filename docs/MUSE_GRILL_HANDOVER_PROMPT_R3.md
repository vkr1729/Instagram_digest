# Handover Prompt: Muse Spark 1.3 Contributor (Max Effort) — Review Iteration 3

> **Model Required:** `meta/muse-spark-1.3-contributor` (Reasoning Effort: `max`)  
> **Source Document:** `docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md` (Hardened Revision 3)  
> **Context:** All Revision 3 requirements from Iteration 2 have been incorporated verbatim into the plan:
> 1. **LivePinSet Definition (R1.1):** All deletions serialized in `MediaCacheManager` actor; $\text{LivePinSet} = \{\text{rows with localStatus == .cached AND file exists}\} \cup \text{pool.activeReelIDs}$; LRU never evicts actively bound reel; purge tolerates missing files.
> 2. **Ephemeral Stream Fallback (R1.2):** Stream fallback is strictly ephemeral (remote URL, zero disk writes, no LRU touch); re-entry to disk only via explicit per-row "Keep Offline".
> 3. **Storage Accounting Ledger (R1.3 & R1.4):** In-memory ledger reconciled once on launch; gauge displays cached bookmark MP4 bytes only; `freeBookmarkStorage()` suspends queue, deletes `.cached` MP4s, flips rows to `.evicted`, resumes queue.
> 4. **Long-Press vs. Scroll (R2.1):** `allowableMovement: 10` cancels long-press if dragging; post-fire, overlay pan and collection view pan both require long-press to fail; `suppressNextTap` consumed by tap handler.
> 5. **SeekPanGestureRecognizer Subclass (R2.2):** Custom subclass failing fast on vertical dominance ($|\Delta y| \times 1.4 \ge |\Delta x|$ before 18pt); seek commits on `.ended` only.
> 6. **Pre-Warmed Haptics (R2.3):** Single persistent `UIImpactFeedbackGenerator(style: .medium)` pre-warmed on touch began and feed appear; `@MainActor` callbacks.
> 7. **Dismissible Download Sheet (R3.1):** Sheet is dismissible; queue continues while foregrounded and suspends on background.
> 8. **Flash Contention & Adaptive Concurrency (R3.2):** Concurrency 3 while idle/sheet visible; concurrency 1 while playing; watchdog suspends downloads for 10s on local stall before counting a strike; thermal degradation at `.serious`/`.critical`.
> 9. **Device Storage Preflight (R3.3):** Preflight via `URLVolumeAvailableCapacityForKey` (manifest total/20MB median + 1 GB headroom); persists `resumeData`.
> 10. **Restored Iteration 1 Behaviors (R4.1):**
>     - `AudioSessionCoordinator` interruption state machine, route change pause, media services reset, `.playback` category.
>     - `automaticallyWaitsToMinimizeStalling` dual branch (`false` local, `true` remote).
>     - Faststart contract (`-movflags +faststart`).
>     - Watched marking (3 triggers: scroll past, 80% progress, scrub past 35%, jump-to-N marks predecessors) + Mindful Modal at 50/day.
>     - Grid: `URLCache` + `NSCache` image pipeline; Share: remote URL + text default.
>     - No mute pill.
>     - Memory warning collapses pool to current slot.
> 11. **Info.plist Cleanup (R4.2):** Removed `UIBackgroundModes: audio` (foreground-only contract).
> 12. **UICollectionView Coordinator Spec (R4.3):** Full delegate inventory (`CellRegistration`, `prepareForReuse` layer detach, `scrollViewDidEndDecelerating` + debounce, `AVQueuePlayer` for all 3 slots).
> 13. **sizeBytes Manifest Field (R4.4):** Optional manifest field; validation skipped gracefully if absent; 2-strike in-memory ladder.
> 14. **SwiftData & Platform Invariants (R4.5):** Main context reads, background context writes, debounced save, fetch-or-insert dedupe, explicit Application Support URL, CloudKit off, per-file protection attributes, XcodeGen complete concurrency.

---

## 🎯 Review Mandate for Iteration 3

Review **Hardened Revision 3** (`docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md`) with max reasoning effort. 

Verify that:
1. Every single item from Iteration 2 has been faithfully and completely addressed without introducing new conflicts.
2. The architectural contract is unambiguous and ready for code generation.

If the plan is completely locked and ready for implementation, explicitly state: **"ARCHITECTURAL CONTRACT VERIFIED: READY FOR IMPLEMENTATION"** along with any final tips for Phase 1.
