muse: workspace root: /home/kedarnath-reddy-vallaboina/Instagram_digest_ios (cwd default)
muse: Agent delegation: auto unavailable: workspace is untrusted.
muse: retrying meta model stream in 1000ms (attempt 2/10)
# Pre-Implementation Review: iOS Decisions & Implementation Plan

Reviewed `docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md` against [player.js](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js), [sw.js](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/sw.js), and the MindSpace reference (`AudioSessionManager`, `PlaybackEngine`, `VideoPlayerView`, `LibraryPathResolver`, `project.yml`). Findings below, in the required format. No locked product decision is challenged; every item is a runtime hardening addition.

---

## Area 1: 3-Slot `AVPlayerPool`

### [Pool]: Rapid fling leaves late-firing observers and ghost audio

* **Risk / Failure Mode:** Flinging past 15 reels in 2 seconds causes overlapping `AVPlayerItem.status` KVO callbacks from superseded items, `Task`-based asset loads completing out of order, and a preloaded neighbor briefly unmuted — the classic symptom is 200ms of the wrong reel's audio ("audio bleed") plus occasional main-thread hitches during `replaceCurrentItem`.
* **Root Cause:** `AVPlayerItem` status observation and `AVURLAsset.load(...)` are asynchronous. A naive pool that calls `replaceCurrentItem` per scroll event has no notion of "this completion belongs to a card that is already 10 positions away." The PWA solved the identical problem with a `navGen` generation counter ([player.js:1021,1310,1426](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)) and single-flight ready-waiters; the plan's "instantly cancelled and detached" names no mechanism.
* **Proposed Hardening:** Port both proven patterns:
  1. A `poolGeneration: UInt64` counter incremented on every `setCurrent(index)`. Every async asset-load `Task` captures its generation and checks `Task.isCancelled` + generation match before touching a slot. Cancel out-of-window tasks explicitly (`task.cancel()`), don't just ignore them.
  2. Copy MindSpace's `observedItem` identity guard: in the status-observer closure, `guard self.observedItem === observed else { return }` ([PlaybackEngine.swift:524-526](/home/kedarnath-reddy-vallaboina/MindSpace/Sources/MindSpace/AudioEngine/PlaybackEngine.swift)). Invalidate the old `NSKeyValueObservation` *before* `replaceCurrentItem(nil)`.
  3. Neighbor slots must never call `play()` — preload via construction + `pause()` (or `preroll`), with `player.isMuted = true` **and** `player.volume = 0` on slot ±1. Unmute only the slot being promoted to current, after its item reports `.readyToPlay`.

### [Pool]: Periodic time observers crash or leak on slot reuse

* **Risk / Failure Mode:** `EXC_BAD_ACCESS` / "Cannot remove a time observer" crashes when a slot is recycled, or leaked observers firing progress UI for reels long gone (progress bar jumping between values).
* **Root Cause:** `addPeriodicTimeObserver` returns an opaque token bound to one `AVPlayer` instance. `[weak self]` (all the plan specifies) prevents a retain cycle but does not detach the observer. Calling `removeTimeObserver` with a token from a *different* player instance traps.
* **Proposed Hardening:** Store `timeObserverToken` **per slot**, and enforce the MindSpace teardown order in `recycle(slot:)`: `player.pause()` → `removeTimeObserver(token)` → `looper = nil` → `replaceCurrentItem(nil)` → `playerLayer.player = nil` (see [PlaybackEngine.swift:582-588](/home/kedarnath-reddy-vallaboina/MindSpace/Sources/MindSpace/AudioEngine/PlaybackEngine.swift)). One shared token for the pool is a bug; make it per-slot state.

### [Pool]: `AVPlayerLooper` lifecycle retains dead items and hitches on short reels

* **Risk / Failure Mode:** `AVPlayerLooper` holds its template item strongly. If the looper outlives a slot recycle, the old item (and its decoder buffers) leak and can ghost-play. Separately, short ~5s reels show a visible last-frame micro-freeze on each loop iteration.
* **Root Cause:** `AVPlayerLooper` works only with `AVQueuePlayer` and clones the template item internally; it must be destroyed before the item it wraps. The loop-point hitch is a decoder flush, worst on MP4s whose `moov` atom is at the file end (non-faststart encodes require a seek-back + reparse).
* **Proposed Hardening:**
  1. Slot teardown sets `looper = nil` *before* `replaceCurrentItem(nil)`. Construct a fresh `AVQueuePlayer` + `AVPlayerLooper` per bind; never re-template a live looper.
  2. Keep `AVPlayerLooper` (it pre-rolls the loop copy, so it hitches *less* than manual `seek(to: .zero)` on `AVPlayerItemDidPlayToEndTime`). Set `automaticallyWaitsToMinimizeStalling = false` for local files, `true` for remote streams (MindSpace uses `false`; you need both branches).
  3. Backend contract: extractor/R2 uploads must be faststart-encoded (`-movflags +faststart`). Add this as a pipeline acceptance check — no client code fully fixes a tail-`moov` file.
  4. Rate changes (2x boost) go to the `AVQueuePlayer.rate`, never to an item copy, and set `item.audioTimePitchAlgorithm = .timeDomain` at bind time.

### [Pool]: Audio-session interruption path is unspecified — calls/Siri break playback

* **Risk / Failure Mode:** Incoming call, Siri, alarm, or AirPods disconnect leaves the reel in a zombie state: UI shows playing, no audio, and resume either crashes `AVAudioSession.setActive` or plays silently. Spotify/podcasts also fail to resume after your app ducks them.
* **Root Cause:** The plan declares `.playback` + `moviePlayback` + `audio` background mode but specifies zero interruption handling. iOS deactivates your session on interruption; resuming without re-activation + state machine produces silence or `AVAudioSessionErrorCode`.
* **Proposed Hardening:** Port [AudioSessionManager.swift](/home/kedarnath-reddy-vallaboina/MindSpace/Sources/MindSpace/AudioEngine/AudioSessionManager.swift) nearly verbatim: `interruptionNotification` (began → pause + record `wasPlayingBeforeInterruption`; ended + `.shouldResume` → play), `routeChangeNotification` (`.oldDeviceUnavailable` → pause, never blare on speaker), `mediaServicesWereResetNotification` (rebuild current slot at saved position), `silenceSecondaryAudioHintNotification`. Activate the session lazily on first `play()`, not at launch; deactivating with `.notifyOthersOnDeactivation` on final pause. Use MindSpace's category options (`.allowBluetooth`, `.allowBluetoothA2DP`, `.allowAirPlay`) — the plan's bare `.playback` risks silent Bluetooth output — and confirm `mode: .moviePlayback` vs MindSpace's `.default` deliberately (`.moviePlayback` is fine for reels; just don't omit the options). Note: there is no PWA "tap-to-unmute / mute pill" equivalent in native (no autoplay-muted policy) — do not port the mute pill.

### [Pool]: Background-audio promise will not hold inside LiveContainer

* **Risk / Failure Mode:** App locks or backgrounds → audio stops despite `UIBackgroundModes: audio`, or video layer goes black and never recovers on foreground.
* **Root Cause:** The guest `Info.plist`'s background modes are advisory; the LiveContainer host process owns the real lifecycle. Lock-screen video detach behavior also differs from a stock app.
* **Proposed Hardening:** Contract change: reels **pause on `didEnterBackground`** (foreground-only playback). Adopt the [VideoPlayerView.swift:66-87](/home/kedarnath-reddy-vallaboina/MindSpace/Sources/MindSpace/AudioEngine/VideoPlayerView.swift) pattern — detach `playerLayer.player = nil` on `didEnterBackground`, reattach the stored player on `willEnterForeground` — so foreground/background transitions never stall or black-screen the layer.

---

## Area 2: Disk Caching & Background Download

### [Cache]: "File exists ⇒ playable" admits corrupt partial downloads

* **Risk / Failure Mode:** Kill the app or lose Wi-Fi mid-download and reel #42's file is a 40% prefix. Next launch, `AVPlayer(url:)` reports `.failed` ("unplayable stream") on a file the cache claims is good. "Download All" resume also wrongly counts it as done.
* **Root Cause:** No integrity gate between network bytes and the playback path. `FileManager.fileExists` says nothing about completeness. (The PWA never had this bug class because the Cache API `put` is atomic on complete `Response`.)
* **Proposed Hardening:**
  1. Download to `{reel_id}.mp4.part`; on completion validate, then atomic-promote via `FileManager.replaceItemAt` / `moveItem`. Delete all `*.part` on launch.
  2. Validation ladder: `size > 0` → expected byte count match (add `sizeBytes` to `data.json`; MindSpace's catalog carries `sizeBytes` + `sha256` per asset — adopt at least the size) → `AVURLAsset.load(.isPlayable, .duration)` probe before first bind.
  3. Playback failure path: on `AVPlayerItem.status == .failed`, evict the file, fall back to remote stream once, and apply the PWA's two-strike `skipDeadCard` rule ([player.js:1097-1134](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)): first strike skips-but-keeps (transient), second strike hides for the session. Never leave a black card.
  4. Persist the completed-download set (SwiftData or plist) so post-kill resume re-verifies rather than re-downloading 3 GB.

### [Cache]: Storage location needs one correction — `Documents/` is the wrong directory

* **Risk / Failure Mode:** ~3 GB of MP4s appears in the Files app / iTunes sharing, gets offered to iCloud backup (quota burn, backup failures), and risks being unreadable after reboot-before-unlock.
* **Root Cause:** `Documents/` is user-visible and backup-eligible by default. `Library/Caches/` is auto-purged under storage pressure (breaks the offline promise). The correct semantic is `Library/Application Support/MediaCache/`.
* **Proposed Hardening:** Store media in `Library/Application Support/MediaCache/{week_id}/`. Apply MindSpace's `applyHardeningAndProtection()` verbatim: `isExcludedFromBackup = true` + `FileProtectionType.completeUntilFirstUserAuthentication` ([LibraryPathResolver.swift:225-251](/home/kedarnath-reddy-vallaboina/MindSpace/Sources/MindSpace/AudioEngine/../Services/LibraryPathResolver.swift)). Never persist absolute file paths in SwiftData — persist `week_id` + `reel_id` and recompute URLs each launch through a `Sendable` resolver struct (LiveContainer guest containers relocate across installs; cached absolute URLs go stale).

### [Cache]: Weekly purge can yank files out from under a playing `AVPlayer`

* **Risk / Failure Mode:** `week_id` rolls over while reel #7 is on screen; purge deletes its file; current playback fails on the next range re-read (seek or loop point), or the *next* reel fails to bind.
* **Root Cause:** APFS unlink-while-open keeps the held fd alive, but `AVPlayer` re-opens by URL for seeks/buffering — post-unlink reads fail. The plan orders purge unconditionally on new-`week_id` detection with no live-handle guard.
* **Proposed Hardening:** `MediaCacheManager.purge(except:)` takes a live-pin set from `AVPlayerPool.activeReelIDs` plus all bookmarked reel IDs (see Area 5). Rule: purge every `week_id != current`, skipping pinned files; tombstone skipped files for deletion on slot release. Perform the purge at launch *before* pool bind when possible, and make it idempotent (re-run safe after a crash between the SwiftData week-write and the delete). Order of operations on rollover: write new `week_id` → save → purge old. Crash anywhere in that sequence self-heals on next launch.

### [Cache]: True background `URLSession` downloads won't wake a LiveContainer guest

* **Risk / Failure Mode:** "Download All" with a background-session identifier stalls when the app backgrounds and never completes — completion handlers rooted in the host app delegate never reach the guest.
* **Root Cause:** Background-session wakeup is delivered to the process owner's `application(_:handleEventsForBackgroundURLSession:)` — in LiveContainer, that owner is the host, not your guest binary.
* **Proposed Hardening:** Specify "Download All" as a **foreground** `URLSession` (delegate-based for pause/resume via `resumeData`), concurrency 3 (the PWA's proven `DOWNLOAD_CONCURRENCY`), with `UIApplication.shared.isIdleTimerDisabled = true` for the duration — the direct analog of the PWA's WakeLock in `startFullDownload` ([player.js:1976-2004](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)). Persist per-file progress so kill/resume continues. Do not promise background completion in v1.

---

## Area 3: Gestures & Touch Arbitration

### [Gestures]: Composed SwiftUI gestures cannot reproduce the PWA's arbitration — use one UIKit recognizer stack

* **Risk / Failure Mode:** Diagonal scrolls open the Seek HUD; lifting a long-press pauses the video; fling-scrolls bookmark reels. Each is a daily-use papercut that reads as "the app is broken."
* **Root Cause:** The PWA's correctness lives in thresholds SwiftUI gesture composition cannot express: 10px cancel radius, 500ms hold timer armed **only when the touch begins in a zone**, horizontal-gate `|dx|>18 && |dx|>|dy|*1.4`, 320ms tap debounce, `suppressNextClick`, 350ms post-swipe cooldown, 500ms post-scroll tap blackout ([player.js:860-947,1517-1588,1910-1918](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)). Stacking `.onTapGesture` + `.onLongPressGesture` + `DragGesture(minimumDistance: 0)` gives all three gestures the same touch with no failure-requirement graph.
* **Proposed Hardening:** Build `FeedGestureOverlay` as a single `UIViewRepresentable` hosting `UITapGestureRecognizer` + `UILongPressGestureRecognizer(minimumPressDuration: 0.5, allowableMovement: 10)` + `UIPanGestureRecognizer`, with a `UIGestureRecognizerDelegate` that:
  1. Hit-tests the normalized zone (`x>0.65w` upper/lower, center-lower box) at touch-**began** and arms the hold timer only in-zone; any >10pt movement cancels (ports `armHoldTimer`/`cancelHold` exactly).
  2. Lets the pan claim the touch only after the PWA slope gate passes; otherwise it fails and the vertical pager owns the touch (`gestureRecognizer(_:shouldRecognizeSimultaneouslyWith:)` → `false`, plus `shouldRequireFailureOf`/`shouldBeRequiredToFailBy` pairing with the scroll recognizer).
  3. Consumes a `suppressNextTap` flag set by any fired long-press, so release never toggles play/pause (ports `suppressNextClick`, including the PWA's lesson that the flag must be consumed by exactly one handler).
  4. Ignores taps within 500ms of scroll end and 350ms after a swipe (ports `lastScrollTime` / `isTouchSwiping`).
  5. Seek commits **only on `.ended`** via `seek(to:toleranceBefore:toleranceAfter:completionHandler:)` (`.zero` tolerances for local files, small tolerance for remote to avoid rebuffer spin); `.changed` updates the HUD preview only — per-move seeks thrash the decoder.

### [Gestures]: The paging container is unnamed — and the default choice drops frames

* **Risk / Failure Mode:** A 300-card SwiftUI `ScrollView` with `scrollTargetBehavior(.paging)` instantiates hundreds of `AVPlayerLayer`-backed views; scrolling stutters and memory climbs as layers attach outside the ±1 window.
* **Root Cause:** SwiftUI laziness doesn't give cell reuse or prefetch callbacks; the pool needs precise will-display/did-end-display signals to bind/unbind the 3 slots.
* **Proposed Hardening:** Make the feed root a `UICollectionView` (full-screen cells, `isPagingEnabled`, prefetch on) wrapped in `UIViewControllerRepresentable`, driven by `willDisplay`/`didEndDisplaying` + `scrollViewDidEndDecelerating` settle — the native equivalent of the PWA's IntersectionObserver + 80ms scroll-settle dual drive ([player.js:1863-1923](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)). `ReelCardView` outside ±1 shows only the poster/thumbnail; the player layer attaches **only** when the pool binds that index. Add a `didReceiveMemoryWarning` path that collapses to the current slot (the analog of the PWA sliding window evicting `src` outside −2/+3, [player.js:1237-1292](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)).

---

## Area 4: Swift 6 Concurrency

### [Concurrency]: Actor-hop the cold path, not the scroll path

* **Risk / Failure Mode:** Under `-strict-concurrency=complete`, either compile failures (non-`Sendable` `AVPlayerItem`/`AVURLAsset` crossing into the `MediaCacheManager` actor) or per-frame `await` suspensions on the scroll path as the pool queries cache state through the actor during a fling.
* **Root Cause:** File-existence checks and URL resolution don't need isolation; downloads, part-file bookkeeping, and purges do. Funneling everything through one actor serializes the hot path and drags non-`Sendable` AVFoundation types across isolation domains.
* **Proposed Hardening:** Split exactly along the MindSpace line: a synchronous `Sendable` resolver struct for the hot path (`resolveURL(for:)`, `isFileAvailable` — cf. [LibraryPathResolver.swift:79-94](/home/kedarnath-reddy-vallaboina/MindSpace/Sources/MindSpace/AudioEngine/../Services/LibraryPathResolver.swift)), called synchronously from the `@MainActor` pool with **zero `await`**; the `MediaCacheManager` **actor** owns only downloads, part-file promotion, and purge. Create and consume all `AVPlayerItem`/`AVURLAsset` instances on `@MainActor`. Make `DownloadAllCoordinator` a plain `NSObject` `URLSessionDelegate` (delegates aren't actor-isolated) that forwards into the actor with `await`. Wrap every `NotificationCenter`/KVO/time-observer closure in `Task { @MainActor ... }` / `MainActor.assumeIsolated` per the [PlaybackEngine.swift:503-518,524-543](/home/kedarnath-reddy-vallaboina/MindSpace/Sources/MindSpace/AudioEngine/PlaybackEngine.swift) pattern.

---

## Area 5: SwiftData Schema & Weekly Lifecycle

### [Schema]: A single `DigestState` model will jank scrolling and risks write loss

* **Risk / Failure Mode:** Marking every watched reel rewrites one giant `@Model` object (300-ID array + counters + bookmarks); saves land on the scroll critical path → dropped frames. Concurrent writes from playback callbacks vs. UI risk "context has pending changes" crashes, and one-field migrations churn the whole store.
* **Root Cause:** Coarse-grained mutable aggregate updated at scroll frequency. The PWA's `localStorage` equivalent is a single JSON string rewrite per mark ([player.js:256-279](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)) — tolerable in JS, not in SwiftData on the main actor at 120Hz.
* **Proposed Hardening:** Decompose into four models following [SwiftDataModels.swift](/home/kedarnath-reddy-vallaboina/MindSpace/Sources/MindSpace/Models/SwiftDataModels.swift) (`CompletionEvent` / `PlaybackResume` / `FavoriteItem` / `UserSettings`):
  * `WatchedEvent` (append-only: `reelID`, `weekID`, compound-unique, timestamp),
  * `BookmarkItem` (see below),
  * `DailyProgress` (`dateString` PK using local-calendar day, viewed IDs or count, `snoozed`),
  * `AppState` (`currentWeekID`, `lastActiveID`).
  Watched/bookmark writes go through a background `ModelContext` and `save()` is debounced (scroll-settle / pause / background), never per-frame. The plan's "archive previous week's watched IDs" then becomes a non-operation — events are week-scoped by attribute and queries filter by week. Rollover is crash-safe by construction: insert new `AppState` → save → purge cache; a crash between save and purge just re-purges on next launch.

### [Schema]: Bookmarks must snapshot media or the purge eats them

* **Risk / Failure Mode:** User bookmarks 20 reels; week rolls over; purge deletes the files; every bookmarked card shows a spinner → error. "Bookmarks preserved" is true for the *rows* and false for playback.
* **Root Cause:** The plan preserves bookmark *data* but purges the *media* those bookmarks point at, with no pin-set or snapshot.
* **Proposed Hardening:** `BookmarkItem` stores a denormalized snapshot (`reelID`, `weekID`, title/creator/category/rank, thumbnail URL, **video URL at bookmark time**) and the purge consults bookmarked IDs as pins (Area 2). Playback resolution order: pinned local file → snapshot-URL stream → graceful "unavailable" UI. Verify R2 public URLs are permanent; if they are ever signed/expiring, bookmarking must force-pin (download) the local file. Surface pinned-bytes in the Bookmarks sheet so unbookmarking visibly frees storage.

---

## Additional blind spots (outside the five areas, same severity)

### [Parity]: The 2x boost and default-speed specs contradict the shipped PWA

* **Risk / Failure Mode:** Users who learned the PWA get a different-feeling app: iOS spec says "2x while held, revert on release," but the PWA **latches** 2x per reel (500ms hold → stuck at 2x until a right-zone tap exits or the reel changes, [player.js:360-395,1552-1556](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)). The plan also drops the PWA's 1.25x default speed and header speed cycler entirely ([player.js:11-29,341-358](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)).
* **Root Cause:** Spec drift from the reference implementation.
* **Proposed Hardening:** Lock one line in the plan before coding: either match the PWA (latched boost + 1.25x default + cycler) or deliberately diverge with rationale. Recommendation: match the PWA latch — it's field-tested against accidental triggers — and keep the cycler. Either way, set `preservesPitch` equivalent (`audioTimePitchAlgorithm = .timeDomain`) as the plan already states.

### [Parity]: Watched-marking semantics are unported — counters and rings depend on them

* **Risk / Failure Mode:** Daily `🎯 n/50` badge, category rings, and resume position silently diverge from PWA behavior because the trigger rules were never specified.
* **Root Cause:** The PWA has three precise rules — forward scroll-away marks the departed card immediately ([player.js:1029-1033,1901-1904](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)), 80% progress milestone ([player.js:1505-1508](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)), scrub-commit past 35% ([player.js:934-937](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)) — plus jump-to-N marking all predecessors. The plan names none.
* **Proposed Hardening:** Port all three triggers; evaluate the 80% rule on throttled 0.5s periodic-observer ticks, not every frame. Port the mindful-modal rules with them: trigger once on crossing 50/day, per-day snooze keyed by local date, "Take a Break" pauses video ([player.js:113-153](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)).

### [Resilience]: No stall watchdog or dead-reel path — one bad file wedges the feed

* **Risk / Failure Mode:** A corrupt R2 object or stalled connection leaves the user on a black card with a spinner forever; no advance, no retry, no signal.
* **Root Cause:** The PWA has a 2s-tick watchdog that skips a card stalled >8s ([player.js:1925-1936](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/templates/partials/player.js)) plus media-error listeners and offline/online resurrection. The native plan specifies no error path at all.
* **Proposed Hardening:** Per-current-slot watchdog: if `rate > 0` but `currentTime` hasn't advanced in 8s and the item isn't `.readyToPlay`-buffering, apply the two-strike rule and auto-advance. Observe `AVPlayerItemFailedToPlayToEndTime` → same path. Keep strikes in-memory (fresh chance each launch), matching the PWA's `dataset`-scoped strikes.

### [Grid/Share]: Thumbnail pipeline and share payload are unspecified

* **Risk / Failure Mode:** 300-cell grid janks on image decode on the main thread; share sheet tries to attach a 50MB MP4 and hangs.
* **Root Cause:** No image-caching or share-content decision. (The PWA warms thumbnails in-window with a 6-file LRU and pre-resolves one share file at activation for Web Share transient-activation rules — native has no transient-activation constraint, so that complexity drops away.)
* **Proposed Hardening:** Grid: `URLCache`-backed async thumbnails + `NSCache` decoded-image cache + collection prefetch; decode off-main. Share: share the **remote video URL + handle text**, not the file — unless offline, in which case share the local file. One sentence in the plan locks this.

---

## Explicitly validated as robust

* **3-slot pool shape.** Correct invariant for VideoToolbox pressure, and it tightens (rather than contradicts) the PWA's proven −2/+3 sliding window. With the generation-counter and per-slot teardown additions above, it is the right design.
* **Zero frameworks / zero banned entitlements / XcodeGen.** Exactly right for LiveContainer; MindSpace's `project.yml` (iOS 17, `SWIFT_STRICT_CONCURRENCY: complete`, portrait, `audio` mode) is a safe template to clone.
* **Actor + `@MainActor` split.** Right shape — the only change is moving the hot read path into a `Sendable` struct so the actor owns mutations only.
* **Gesture zone geometry** (`x > 0.65w`, lower-third split). Faithful port of `HOLD_ZONE`/`isLower` — keep the numbers, just implement them in UIKit recognizers rather than SwiftUI gestures.
* **MindSpace observer-lifecycle patterns** (`observedItem` guard, `MainActor.assumeIsolated` in callbacks, background layer detach, `LibraryPathResolver` hardening). Directly reusable; several hardening items above are "copy this file."
* **Local-only SwiftData with week-scoped queries.** The right sovereignty call; the decomposition above makes it performant and crash-safe without changing the contract.

## Suggested pre-coding checklist (highest leverage first)

1. Lock the UIKit gesture-overlay + `UICollectionView` pager decision (biggest frame-drop and mis-tap risk).
2. Lock boost semantics (latch vs hold) and default speed (1.25x?).
3. Add `sizeBytes` to `data.json` and `+faststart` to the encode pipeline.
4. Split SwiftData into four models; define bookmark snapshot + purge pin-set.
5. Move media to `Application Support`, foreground-only Download All, pause-on-background.
