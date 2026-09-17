# Iteration 2 Review: Hardened Revision 2 (`docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md`)

**Reviewer:** Muse Spark (max effort) — Review Iteration 2
**Date:** 2026-09-17
**Scope:** The 4 mandated areas below. Iteration 2 review complete. Full report saved to [MUSE_FEEDBACK_ITERATION_2.md](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/docs/MUSE_FEEDBACK_ITERATION_2.md). Grounded this session: `HOLD_ZONE`/`HOLD_MS`/concurrency-3/500ms blackout confirmed in `player.js`; digest payload confirmed to have **no per-reel `sizeBytes`** today.

**Verdict: REVISION 3 REQUIRED — not yet ready for implementation.** All eight Revision 2 mechanics are sound and survive unchanged. What blocks coding is specificity, not direction.

## Mandate 1 — Storage cap & LRU

**[R1.1] Purge vs LRU race + contradictory pin set**
- *Risk:* Weekly purge pins "active bookmarks" while LRU deletes "oldest bookmarked MP4" — literally contradictory; concurrent execution double-deletes or pins a just-evicted file.
- *Root cause:* Two mutators, no stated serialization; pin set defined over rows, not file-state.
- *Hardening:* All deletes live on the single `MediaCacheManager` actor; redefine `LivePinSet = {rows with localStatus == .cached AND file exists} ∪ pool.activeReelIDs`; LRU never evicts an actively bound reel; purge tolerates missing files.

**[R1.2] Re-cache policy undefined (your Q)**
- *Risk:* Re-caching streamed bookmarks causes cascading eviction churn; never re-caching silently shrinks the offline library.
- *Root cause:* No admission policy for the stream-fallback path.
- *Hardening:* Lock one line — **fallback streams are ephemeral** (remote URL, zero disk writes, no LRU touch); re-entry only via explicit per-row "Keep offline" through normal LRU admission.

**[R1.3] Size accounting unstated**
- *Risk:* Per-insert directory enumeration janks; crash between delete and accounting breaks the 1.5 GB promise; gauge numerator undefined.
- *Root cause:* No ledger design; "local file status" undescribed.
- *Hardening:* `BookmarkItem` gets `localStatus` enum + `sizeBytes` + `lastAccessedAt`; actor holds cached `totalBytes` reconciled once at launch; gauge = cached bookmark MP4 bytes only (excludes `.part` + week cache).

**[R1.4] "Free Local Storage" races downloads/eviction**
- *Risk:* One-tap purge deletes a `.part` mid-promotion or flips a just-cached row.
- *Root cause:* UI mass-delete not sequenced with actor mutators.
- *Hardening:* `freeBookmarkStorage()` is an actor func: suspend queue → delete `.cached` MP4s only (never `.part`) → flip rows to `.evicted` → resume; button disabled while in flight.

## Mandate 2 — Gestures

**[R2.1] Center-Lower long-press vs scroll (your Q)**
- *Risk:* Hold-then-scroll double-fires (bookmark + scroll).
- *Root cause:* Failure directions and the collection view's recognizer unnamed.
- *Hardening:* Pre-fire needs no code (`allowableMovement: 10` already cancels — state this); post-fire, overlay pan **and** `collectionView.panGestureRecognizer` must both require long-press to fail; `suppressNextTap` consumed by exactly one handler (tap).

**[R2.2] Slope gate unenforceable with stock `UIPanGestureRecognizer`**
- *Risk:* Diagonal drags fire both Seek HUD and pager scroll.
- *Root cause:* Delegates can't fail a continuous gesture mid-flight; `state` is settable only from inside the recognizer.
- *Hardening:* Add `SeekPanGestureRecognizer : UIPanGestureRecognizer` subclass failing fast on vertical-dominance; `shouldRecognizeSimultaneouslyWith → false`; seek commits on `.ended` only (`.zero` tolerance local, small tolerance remote).

**[R2.3] Haptic latency (your Q)**
- *Risk:* First bookmark haptics late/silent (cold Taptic Engine).
- *Root cause:* Lifecycle implies construction at fire time.
- *Hardening:* One long-lived generator per overlay; `prepare()` when the hold timer arms + after each `impactOccurred()`; pre-warm on feed appear; all callbacks `@MainActor`.

## Mandate 3 — Download vs playback

**[R3.1] Concurrency reachability ambiguous**
- *Risk:* "Dedicated sheet" reads modal (no contention possible) but the mandate assumes concurrency.
- *Hardening:* Lock one line — sheet is dismissible; queue continues while foregrounded, suspends on background.

**[R3.2] Flash contention (your Q)**
- *Risk:* 3 `.part` writes + player range-reads → local rebuffering misread by the watchdog as dead reels.
- *Root cause:* Shared NAND bandwidth, not file locks — `.part` isolation means no two parties touch one inode, so no locking is needed.
- *Hardening:* `.utility` QoS serial delegate queue, no per-chunk fsync; adaptive concurrency (3 idle → 1 while playing); local-stall watchdog suspends downloads ~10s before counting a strike; thermal degradation at `.serious`/`.critical`.

**[R3.3] No device preflight**
- *Hardening:* `URLVolumeAvailableCapacityForKey` check (manifest total or count×20 MB + 1 GB headroom), explicit background delegate queue, persisted `resumeData`.

## Mandate 4 — Sanity check

**[R4.1] REGRESSION — seven accepted Iteration 1 behaviors missing from Rev 2 text:** audio interruption/route/reset state machine, `automaticallyWaitsToMinimizeStalling` dual branch, faststart contract, watched-marking 3 triggers + mindful-modal rules, grid/share pipeline, mute-pill non-port, memory-warning collapse. Rev 3 must re-add each as one locked line (paste-ready lines in the report).

**[R4.2]** File map's "Audio background mode" contradicts pause-on-background — remove `UIBackgroundModes: audio`, keep `.playback` category.

**[R4.3]** Collection wrapper won't compile as drawn — lock the delegate inventory (DataSource + Delegate + Prefetching coordinator, `CellRegistration`, `prepareForReuse` detach, `scrollViewDidEndDecelerating` settle, `AVQueuePlayer` for all slots since `AVPlayerLooper` requires it).

**[R4.4]** `.part` size validation depends on nonexistent `sizeBytes` — backend adds it in `site_builder.py`; client skips (never fails) when absent; strikes in-memory per launch.

**[R4.5]** Lock SwiftData (background context, debounced save, fetch-or-insert dedupe), explicit container URL with CloudKit off, per-file protection attributes, and XcodeGen/CI pins (`SWIFT_STRICT_CONCURRENCY: complete`, `CODE_SIGNING_ALLOWED=NO`, unsigned zip-Payload IPA).

## Phase 1 tips (after Rev 3)
1. Build order: resolver + cache actor → pool → pager → gestures.
2. First test: 15-rapid-fling pool test (one audible slot, zero leaked observers).
3. Keep a `docs/PLAN_DELTA.md` — one dated line per deviation, so any Iteration 3 reviews code against contract.
r continuous gestures — a recognizer's `state` can only be failed from *inside* the recognizer. A stock pan cannot be failed mid-gesture from a delegate callback.
* **Proposed Hardening:** Add a small `SeekPanGestureRecognizer : UIPanGestureRecognizer` subclass that overrides `touchesMoved` to set `state = .failed` when the gesture proves vertical-dominant (`|dy|*1.4 >= |dx|` before `|dx|` crosses 18pt). Delegate `shouldRecognizeSimultaneouslyWith` returns `false` for the overlay-vs-collection pair. Also restate the dropped Iteration 1 seek rule: `.changed` updates the HUD preview only; the seek **commits on `.ended`** (`.zero` tolerances for local files, small tolerance for remote to avoid rebuffer spin).

### [R2.3] First-gesture haptic will be late or silent (mandate Q)

* **Identified Risk / Edge Case:** The bookmark long-press fires at 0.5s but the haptic arrives late or not at all on the first gestures of a session.
* **Root Cause:** `UIImpactFeedbackGenerator` cold-starts the Taptic Engine on first use; the plan's phrasing implies construction at fire time, which misses the moment.
* **Proposed Hardening:** One long-lived generator per overlay (`lazy var bookmarkHaptic = UIImpactFeedbackGenerator(style: .medium)`); call `prepare()` when the hold timer arms (touch-began in-zone) and again after every `impactOccurred()` to pre-warm the next gesture; one `prepare()` on feed appear. All recognizer callbacks dispatch to `@MainActor`. Lock styles: `.medium` for bookmark, `.light` for seek-commit tick (or explicitly no haptic for seek — one line either way).

---

## Mandate 3 — Background Download vs UI Playback Concurrency

### [R3.1] Reachability of the concurrency is ambiguous (modal sheet vs dismissible queue)

* **Identified Risk / Edge Case:** §1.6 describes a "dedicated sheet running foreground `URLSession`" (reads modal — user isn't watching, contention impossible) while this mandate assumes concurrent watch-during-download. Implementers will guess differently.
* **Root Cause:** Sheet modality and queue-continuation policy are unstated.
* **Proposed Hardening:** Lock one line: **the Download All sheet is dismissible; the queue continues while the app is foregrounded and suspends on backgrounding** (no background completion — the Iteration 1 LiveContainer contract stands). R3.2 controls therefore apply.

### [R3.2] Flash bandwidth contention between `.part` writes and local playback reads (mandate Q)

* **Identified Risk / Edge Case:** Three concurrent `.part` writes plus `AVPlayer` range-reads/seeks on local MP4s contend for NAND bandwidth; symptom is local-file rebuffering that the 8s watchdog misreads as a dead reel and wrongly skips.
* **Root Cause:** Shared flash bandwidth — *not* file locking. (Name this explicitly: `.part` isolation means downloads and playback never touch the same inode, so no file lock is needed or wanted; the only shared resource is throughput.)
* **Proposed Hardening:**
  1. QoS split: download `URLSession` on a background serial `delegateQueue`, QoS `.utility`; no per-chunk fsync — single atomic `moveItem` promotion on completion.
  2. Adaptive concurrency in `DownloadAllCoordinator`: 3 while the sheet is visible and the pool is idle; **1 while the pool is actively playing** (one observed `isPlaying` flag, one `int` switch).
  3. Watchdog precedence: on a **local**-file stall with active downloads, first suspend downloads for ~10s and retry before counting a strike — download-induced stalls must never burn a dead-reel strike.
  4. Thermal degradation: observe `ProcessInfo.thermalState`; at `.serious`/`.critical` drop to concurrency 1 and suspend ±1 prefetch.

### [R3.3] No device-side storage preflight; delegate queue unspecified

* **Identified Risk / Edge Case:** "Download All" on a near-full 128 GB phone fails 60% through, leaving ~150 orphaned `.part` files; download delegate callbacks landing on the main queue jank scrolling.
* **Root Cause:** The backend `<5 GB` guard protects R2, not the phone; `URLSession` delegate-queue choice is unstated.
* **Proposed Hardening:** Preflight via `URLVolumeAvailableCapacityForKey`: required = manifest total if known else `count × 20 MB median`, plus **1 GB headroom**; refuse with an alert when short. Construct the session with an explicit background serial `delegateQueue` (never main, never nil-for-main). Persist per-file `resumeData` so kill/resume continues rather than restarts.

---

## Mandate 4 — Final Sanity Check (compile on Xcode 16/XcodeGen, run on iOS 17/18)

### [R4.1] REGRESSION: Seven Iteration 1 hardenings were dropped from the Revision 2 text

* **Identified Risk / Edge Case:** A coder implementing from Revision 2 alone will not build: (a) audio interruption/route-change/media-reset handling, (b) `automaticallyWaitsToMinimizeStalling` dual branch, (c) faststart backend contract, (d) watched-marking 3 triggers + mindful-modal rules, (e) grid thumbnail pipeline + share payload, (f) mute-pill non-port, (g) memory-warning pool collapse. All were accepted in Iteration 1; none appear in Revision 2.
* **Root Cause:** Revision 2 carried the mechanics (generation counter, teardown order, zones, models) but not the behaviors.
* **Proposed Hardening:** Rev 3 re-adds each as one locked line — paste-ready:
  1. `AudioSessionCoordinator` ports interruption (began → pause + `wasPlaying` record; ended + `.shouldResume` → play), route-change (`.oldDeviceUnavailable` → pause), media-services-reset (rebuild current slot at saved position), `silenceSecondaryAudioHint`; category `.playback` + `[.allowBluetooth, .allowBluetoothA2DP, .allowAirPlay]`, mode `.moviePlayback`; lazy `setActive` on first `play()`, `.notifyOthersOnDeactivation` on final pause.
  2. `automaticallyWaitsToMinimizeStalling = false` for local files, `true` for remote streams; 2x rate goes to the `AVQueuePlayer.rate`, never an item copy.
  3. Backend contract: extractor/R2 uploads faststart-encoded (`-movflags +faststart`) — pipeline acceptance check.
  4. Watched triggers: forward scroll-away marks departed card immediately; 80% progress milestone (evaluated on throttled 0.5s observer ticks); scrub-commit past 35%; jump-to-N marks all predecessors. Mindful modal: fires once on crossing 50/day, per-day snooze keyed by local date, "Take a Break" pauses video.
  5. Grid: `URLCache`-backed async thumbnails + `NSCache` decoded-image cache + collection prefetch, decode off-main. Share: remote video URL + handle text (local file only when offline).
  6. No mute pill: native has no autoplay-muted policy; do not port the PWA pill.
  7. `didReceiveMemoryWarning` collapses the pool to the current slot only.

### [R4.2] `Info.plist` audio background mode contradicts the pause-on-background contract

* **Identified Risk / Edge Case:** File map promises "Audio background mode" while §1.4 mandates pause + layer-detach on `didEnterBackground` (foreground-only playback). Both cannot be the contract; the mode is advisory-ignored inside LiveContainer anyway and invites confusion.
* **Root Cause:** Leftover pre-Iteration-1 spec in the file map.
* **Proposed Hardening:** Lock: **remove `UIBackgroundModes: audio`** (foreground-only contract); keep `AVAudioSession` category `.playback` for silent-switch override. Keep the `didEnterBackground → playerLayer.player = nil` / `willEnterForeground → reattach` pattern exactly as specified.

### [R4.3] `UICollectionView` wrapper is under-specified — will not compile/run as drawn

* **Identified Risk / Edge Case:** "Wrapped in `UIViewControllerRepresentable`, driven by `willDisplay`/`didEndDisplaying`" omits the load-bearing details: data source, cell reuse/layer-detach, settle detection, prefetch, memory path.
* **Root Cause:** Container decision recorded without its delegate inventory.
* **Proposed Hardening:** Lock the inventory: Coordinator implements `UICollectionViewDataSource` + `Delegate` + `UICollectionViewDataSourcePrefetching`; full-screen cells via `CellRegistration`, `isPagingEnabled`, prefetch on; settle via `scrollViewDidEndDecelerating` (+ small debounce, the native analog of the PWA 80ms settle); `prepareForReuse` detaches any player layer; non-±1 cells show poster/thumbnail only; `AVQueuePlayer` (not `AVPlayer` — `AVPlayerLooper` requires a queue player) for all three slots, fresh queue+looper per bind, `looper = nil` before `replaceCurrentItem(nil)` per the stated teardown order.

### [R4.4] `.part` size validation depends on a manifest field that does not exist

* **Identified Risk / Edge Case:** "Validating that byte count matches expected manifest size" cannot run — verified this session: the digest payload carries no per-reel `sizeBytes`.
* **Root Cause:** Backend dependency unstated and unimplemented.
* **Proposed Hardening:** Two-sided lock. Backend (one line in `site_builder.py`, which already touches each file; `local_server.py` proves the `stat()` call): add `sizeBytes` per reel. Client ladder degrades gracefully: `size > 0` → `sizeBytes` match **if present, skipped if absent (never a failure)** → `AVURLAsset.load(.isPlayable, .duration)` probe before first bind. Playback-failure path: on `.failed`, evict file, try remote stream once, then the two-strike rule (strikes in-memory per reelID per launch, matching the PWA's card-scoped strikes).

### [R4.5] SwiftData + Swift 6 + LiveContainer + XcodeGen specifics unstated

* **Identified Risk / Edge Case:** First-compile failures (`SWIFT_STRICT_CONCURRENCY: complete` vs non-`Sendable` contexts), container relocation across installs, unreadable files after reboot-before-unlock, backup bloat, signing failures in CI.
* **Root Cause:** Platform-adapter details live only by reference ("mirroring MindSpace"), not as lockable lines.
* **Proposed Hardening:** Lock each line:
  1. SwiftData: reads on main `ModelContext`, watched/bookmark writes on a background context, `save()` debounced (scroll-settle / pause / background) — never per-frame; compound uniqueness via fetch-or-insert (SwiftData has no unique constraints — state the manual dedupe).
  2. `ModelContainer` at an explicit `Application Support` URL, CloudKit disabled (zero entitlements); never persist absolute paths — persist `weekID + reelID`, recompute URLs via the `Sendable` resolver (guest containers relocate).
  3. `completeUntilFirstUserAuthentication` + `isExcludedFromBackup` applied to the directory **and** to each promoted file (protection does not reliably inherit on `moveItem` — set attributes on the result).
  4. XcodeGen: iOS 17.0, Swift 6, `SWIFT_STRICT_CONCURRENCY: complete`, portrait-only, `CODE_SIGNING_ALLOWED=NO`; CI pins the `xcodegen` version, uses a `macos-15`/Xcode-16 runner, `xcodegen generate` → `xcodebuild` (unsigned) → zip `Payload/*.app` to IPA for LiveContainer import. All `AVPlayerItem`/`AVURLAsset` creation and consumption on `@MainActor`; `DownloadAllCoordinator` a plain `NSObject` `URLSessionDelegate` forwarding into the actor with `await`.

---

## Verdict

**REVISION 3 REQUIRED — not yet ready for implementation.** Revision 2's eight incorporated mechanics (generation counter, teardown order, `MediaCache/` relocation, latched boost, UIKit stack, collection pager, 4-model split, LRU cap + `.part` validation) are all sound and survive this review unchanged. What blocks coding is specificity, not direction: the R4.1 regression (seven accepted Iteration 1 behaviors missing from the text) plus the six one-line locks — LivePinSet definition (R1.1), ephemeral-stream default (R1.2), custom seek-pan subclass (R2.2), sheet-modality line (R3.1), audio-mode removal (R4.2), `sizeBytes` backend field (R4.4). None requires re-architecture; each is a sentence in Rev 3.

## Phase 1 tips (after Rev 3 lands)

1. Build order: `LibraryPathResolver` + `MediaCacheManager` actor first (every other component's tests lean on them), then pool, then pager, then gestures.
2. First test target: pool fling test (15 rapid `setCurrent` calls, assert single audible slot + zero leaked observers) — it exercises the generation counter, teardown order, and mute discipline in one shot.
3. Keep a `docs/PLAN_DELTA.md` from day one: every deviation from Rev 3 gets one dated line, so Iteration 3 (if any) reviews the code against the contract instead of re-reading the code.
