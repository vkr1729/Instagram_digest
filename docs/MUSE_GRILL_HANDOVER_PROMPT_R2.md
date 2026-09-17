# Handover Prompt: Muse Spark 1.3 Contributor (Max Effort) — Review Iteration 2

> **Model Required:** `meta/muse-spark-1.3-contributor` (Reasoning Effort: `max`)  
> **Source Document:** `docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md` (Hardened Revision 2)  
> **Context:** All initial findings from Iteration 1 have been incorporated into the plan:
> - `poolGeneration: UInt64` counter for fling cancellation.
> - Strict per-slot teardown sequence (`pause -> removeTimeObserver -> looper=nil -> replaceCurrentItem(nil)`).
> - Relocated media to `Library/Application Support/MediaCache/{week_id}/` with background exclusion and hardware protection.
> - Latched 2.0x boost + 1.25x default speed.
> - UIKit gesture recognizer stack in `FeedGestureOverlay` via `UIViewRepresentable` with slope gating and `suppressNextTap`.
> - Root pager transitioned to `UICollectionView` (`isPagingEnabled = true`) driven by `willDisplay`/`didEndDisplaying`.
> - Decomposed SwiftData into 4 models (`WatchedEvent`, `BookmarkItem`, `DailyProgress`, `AppState`).
> - Space-Safe Bookmark Engine: 1.5 GB LRU disk eviction with seamless fallback to streaming from Cloudflare R2 on demand, plus storage gauge and one-tap manual purge.
> - Partial download `.part` validation and stall watchdog (>8s two-strike rule).

---

## 🎯 Review Mandate for Iteration 2

Review **Hardened Revision 2** (`docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md`) with max reasoning effort. 

The goal is to verify that these new additions are airtight and check for any remaining edge cases before we begin coding:

### 1. The 1.5 GB Bookmark Storage Cap & LRU Eviction
* Does the interaction between the weekly cache purge and the 1.5 GB bookmark LRU eviction introduce any race conditions?
* If an evicted bookmark's local file was purged, and the user taps it to stream from R2, how should the app handle re-caching? (Does it stream as a temporary stream or re-enter the cache?)

### 2. Gesture Stack Edge Cases
* In the UIKit `UIGestureRecognizer` stack inside `FeedGestureOverlay`:
  * Is there any ambiguity between the 0.5s long-press in the Center-Lower zone (bookmark) vs dragging vertically to dismiss/scroll?
  * How should the haptic generator (`UIImpactFeedbackGenerator`) lifecycle be managed to avoid latency on the first gesture?

### 3. Concurrency Between Background Download & UI Playback
* When "Download All" is writing `.part` files in the background, and the user is concurrently watching reels from disk in the foreground, how do we prevent file I/O contention on the device flash storage?

### 4. Final Sanity Check
* Are there any remaining gaps, missing delegate callbacks, or subtle bugs that would prevent this from compiling cleanly on Xcode 16 via XcodeGen or running smoothly on iOS 17/18?

Output your findings in the standard structured format:
* **Identified Risk / Edge Case**
* **Root Cause**
* **Proposed Hardening**

If the plan is completely locked and ready for implementation, explicitly state: **"ARCHITECTURAL CONTRACT VERIFIED: READY FOR IMPLEMENTATION"** along with any final tips for Phase 1.
