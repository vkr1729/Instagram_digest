# Instagram Digest iOS — Architectural Decisions & Implementation Plan (Hardened Revision 3)

> **Target Platform:** iOS 17.0+ (iPhone)  
> **Deployment Target:** LiveContainer (Unsigned IPA via SideStore/AltStore)  
> **CI/CD Build System:** GitHub Actions (macOS runners on `vkreddy1729-ops/Instagram_digest`)  
> **Reference Pattern:** `/home/kedarnath-reddy-vallaboina/MindSpace` (XcodeGen, zero-leak AVFoundation, local persistence)  
> **Source Repository:** `/home/kedarnath-reddy-vallaboina/Instagram_digest_ios` (Branch: `feat/ios-app`)

---

## 1. Locked Architectural Decisions (The Contract)

### 1.1 Ingestion & Single Source of Truth
* **Pure Consumer Client:** Zero Python dependencies on device. Consumes remote `data.json` on GitHub Pages / Cloudflare R2.
* **Media Streams:** High-definition MP4 streams fetched directly from Cloudflare R2 public bucket URLs.
* **Faststart MP4 Contract:** Video MP4s in Cloudflare R2 must be faststart-encoded (`-movflags +faststart`) so the `moov` atom is at the beginning of the file, allowing instant streaming and pre-buffering.
* **`sizeBytes` Manifest Field:** Backend (`site_builder.py`) injects per-reel `sizeBytes` into `data.json`. Client verifies byte length during `.part` promotion if present; if absent, validation is skipped gracefully (never treated as a failure).

### 1.2 Local Persistence & Storage Boundaries
* **100% Local-First:** All state lives locally via SwiftData with CloudKit explicitly disabled (zero entitlements).
* **Model Topology:** Decomposed into 4 lightweight models (mirroring MindSpace's `SwiftDataModels.swift`):
  1. `WatchedEvent` (append-only: `reelID: String`, `weekID: String`, `timestamp: Date`).
  2. `BookmarkItem` (denormalized snapshot: `reelID: String`, `weekID: String`, `creatorHandle: String`, `caption: String`, `rank: Int`, `videoUrl: URL`, `bookmarkedAt: Date`, `localStatus: BookmarkLocalStatus` [`.cached` | `.evicted`], `sizeBytes: Int64`, `lastAccessedAt: Date`).
  3. `DailyProgress` (`dateString: String` PK using local calendar `YYYY-MM-DD`, `viewedCount: Int`, `snoozeUntil: Date?`).
  4. `AppState` (`currentWeekID: String`, `lastActiveReelID: String?`).
* **SwiftData Concurrency & Deduplication:**
  * Reads occur on the main `ModelContext`.
  * Writes (watched events, bookmarks) occur on a background `ModelContext`.
  * Context `save()` is debounced to scroll-settle, playback pause, or app backgrounding (never on high-frequency per-frame ticks).
  * Compound uniqueness is enforced via fetch-or-insert deduplication (SwiftData lacks native unique constraints).
* **Storage Location & Protection:**
  * Media files stored in `Library/Application Support/MediaCache/{week_id}/` (never `Documents/` to avoid Files app clutter and iCloud backup bloat).
  * URL recomputation: Never persist absolute file paths (guest containers relocate across reinstalls). Persist `weekID` and `reelID`; resolve paths at runtime via a `Sendable` `LibraryPathResolver`.
  * Hardware Protection & Backup Exclusion: Apply `URLResourceKey.isExcludedFromBackupKey = true` and `FileProtectionType.completeUntilFirstUserAuthentication` to the directory **and** explicitly to each promoted `.mp4` file (since `moveItem` does not reliably inherit file protection attributes).
* **Weekly Rollover & LivePinSet Invariant:**
  * All file deletions, weekly purges, and LRU evictions are strictly serialized inside the `MediaCacheManager` actor.
  * When a new `week_id` arrives:
    1. Writes new `currentWeekID` to `AppState`.
    2. Purges old week's cache directory, strictly protecting the **LivePinSet**:
       $$\text{LivePinSet} = \{\text{reelID} \mid \text{BookmarkItem.localStatus} == \text{.cached} \land \text{file exists}\} \cup \text{AVPlayerPool.activeReelIDs}$$
    3. Purge tolerates missing files gracefully; never deletes any file currently bound to the active video pool.
    4. Historical `WatchedEvent` rows remain preserved in SwiftData; queries are week-scoped.

### 1.3 Space-Safe Bookmark Engine (128 GB iPhone Protection)
* **1.5 GB Bookmark Storage Cap (LRU Eviction):**
  * Local offline storage for bookmarked MP4s is capped at **1.5 GB** (~75–100 reels).
  * If a newly saved bookmark causes total bookmark storage to exceed 1.5 GB, `MediaCacheManager` deletes the oldest bookmarked MP4 from disk based on `BookmarkItem.lastAccessedAt`.
  * When evicted, `BookmarkItem.localStatus` is updated to `.evicted`, but the database row remains 100% intact in SwiftData.
  * An actively playing or bound reel is never evicted by LRU.
* **Ephemeral Stream Fallback (Zero Churn):**
  * Tapping an evicted bookmark gracefully streams on-demand from its permanent Cloudflare R2 URL.
  * **Fallback streams are strictly ephemeral:** they do not write to disk, do not trigger LRU eviction churn, and do not update `localStatus`.
  * Re-entry to local disk storage is only permitted via an explicit per-row "Keep Offline" user action, which passes through standard LRU admission checks.
* **Storage Accounting & Ledger:**
  * `MediaCacheManager` maintains an in-memory `totalBookmarkBytes: Int64` ledger, reconciled once on app launch.
  * The Bookmarks sheet storage gauge (`💾 Bookmarks: 420 MB / 1.5 GB`) reflects strictly cached bookmark MP4 bytes (excluding temporary `.part` files and the current week's cache).
* **"Free Local Storage" Purge:**
  * One-tap `freeBookmarkStorage()` function on `MediaCacheManager`:
    1. Temporarily suspends the download queue.
    2. Deletes only completed `.cached` bookmark MP4s (never touches `.part` files or active feed files).
    3. Updates all bookmark rows to `.evicted`.
    4. Resets `totalBookmarkBytes = 0`.
    5. Resumes download queue.
  * The UI button is disabled while the purge is in-flight.

### 1.4 AVPlayer Lifecycle & Hardware Decoder Invariant
* **Strict 3-Slot Pool (`Current - 1, Current, Current + 1`):**
  * Built using `AVQueuePlayer` for all three slots (required by `AVPlayerLooper`).
  * **Slot 0 (`Current - 1`):** Paused on previous reel at current timestamp, ready for instant back-scroll.
  * **Slot 1 (`Current`):** Active playback with `AVPlayerLooper`, unmuted.
  * **Slot 2 (`Current + 1`):** Preloaded item pre-buffered to frame 0, `isMuted = true` and `volume = 0`.
  * `automaticallyWaitsToMinimizeStalling`: Set to `false` for local files (instant frame-accurate playback) and `true` for remote streams (smooth buffering).
* **Ghost Audio & Fling Elimination (`poolGeneration`):**
  * Monotonic `poolGeneration: UInt64` counter increments on every `setCurrent(index)`.
  * In-flight async asset loading `Task`s verify their generation before touching a slot; superseded tasks are immediately cancelled (`task.cancel()`).
  * In `AVPlayerItem.status` observer, checks `guard self.observedItem === observed else { return }`.
* **Zero-Leak Teardown Order:**
  * `timeObserverToken` is strictly per-slot state.
  * Recycling order: `player.pause()` → `removeTimeObserver(token)` → `looper = nil` → `replaceCurrentItem(nil)` → `playerLayer.player = nil`.
* **Audio Interruption & Route Management (`AudioSessionCoordinator`):**
  * Category: `.playback` with options `[.allowBluetooth, .allowBluetoothA2DP, .allowAirPlay]`, mode `.moviePlayback`.
  * Lazy activation: calls `setActive(true)` on first `play()`, and `.notifyOthersOnDeactivation` on final pause.
  * Audio interruptions (`AVAudioSession.interruptionNotification`):
    * `.began`: Pause playback and record `wasPlaying = true`.
    * `.ended`: If `interruptionOptions.contains(.shouldResume)` and `wasPlaying`, resume playback.
  * Route changes (`AVAudioSession.routeChangeNotification`):
    * On `.oldDeviceUnavailable` (e.g. headphones unplugged / Bluetooth disconnected), immediately pause playback.
  * Media services reset (`AVAudioSession.mediaServicesWereResetNotification`): Rebuild the current slot player and restore to saved position.
  * Secondary audio hint: Observe `silenceSecondaryAudioHintNotification`.
* **LiveContainer Backgrounding & Memory Safeguards:**
  * Foreground-only contract: No `UIBackgroundModes: audio` in `Info.plist`.
  * On `didEnterBackground`, pause playback and detach `playerLayer.player = nil`.
  * On `willEnterForeground`, reattach player to the layer (prevents permanent black screen in LiveContainer).
  * On `didReceiveMemoryWarning`, immediately collapse the pool to the current slot only (evict slots -1 and +1).

### 1.5 Playback Speed, Watched Rules & Gestures
* **Playback Speed:**
  * Default speed: **1.25x** with header speed cycler for 1.0x, 1.25x, 1.5x, 2.0x.
  * Speed changes apply directly to `AVQueuePlayer.rate` (never item copies).
  * 2.0x Latched Boost: Holding Upper-Right zone (`x > 0.65w, y <= 0.65h`) for 0.5s **latches** the reel at 2.0x speed for its duration. Tapping anywhere resets back to default. `audioTimePitchAlgorithm = .timeDomain` prevents pitch distortion.
* **Watched Marking (3 Triggers):**
  1. Forward scroll-away: Departing card marked watched immediately upon scrolling past.
  2. 80% progress milestone: Evaluated on throttled 0.5s time observer ticks.
  3. Scrub past 35%: Seeking and committing beyond 35% marks the reel as watched.
  4. Jump-to-N: Jumping to reel N via the Grid marks all predecessor reels (1 through N-1) as watched.
* **Mindful Daily Modal:**
  * Triggers once upon crossing 50 watched reels in a local day (`DailyProgress.viewedCount >= 50`).
  * Dismissible with per-day snooze keyed by local calendar date (`YYYY-MM-DD`).
  * Tapping "Take a Break" pauses video playback.
* **UIKit Gesture Stack (`FeedGestureOverlay`):**
  * Single `UIViewRepresentable` hosting:
    * `UITapGestureRecognizer`: Single tap toggles play/pause; resets latched 2.0x speed.
    * `UILongPressGestureRecognizer(minimumPressDuration: 0.5, allowableMovement: 10)`.
    * Custom `SeekPanGestureRecognizer : UIPanGestureRecognizer`.
  * **Custom `SeekPanGestureRecognizer` Subclass:**
    * Overrides `touchesMoved`: fails immediately (`state = .failed`) when vertical dominance is detected ($|\Delta y| \times 1.4 \ge |\Delta x|$ before $|\Delta x|$ crosses 18pt).
    * `shouldRecognizeSimultaneouslyWith` returns `false` between overlay pan and collection view pan.
    * Pan `.changed` updates the horizontal Seek HUD preview only; seek **commits strictly on `.ended`** (`.zero` tolerance for local files, small tolerance for remote streams).
  * **Long-Press & Scroll Disambiguation:**
    * Pre-fire: `allowableMovement: 10` automatically cancels long-press if user scrolls.
    * Post-fire: Overlay pan and `collectionView.panGestureRecognizer` both require the long-press to fail before scrolling.
    * `suppressNextTap` flag set by any fired long-press to prevent lifting from toggling pause.
    * Ignores taps within 500ms of scroll end and 350ms of swipe.
  * **Spatial Zones:**
    * Upper-Right (`x > 0.65w, y <= 0.65h`): Latched 2.0x speed boost.
    * Lower-Right (`x > 0.65w, y > 0.65h`): System Share Sheet (`UIActivityViewController`). Shares remote video URL + creator handle text (local file shared only when fully offline).
    * Center-Lower (`0.35w <= x <= 0.65w, y > 0.65h`): Bookmark toggle with haptics and animated pop.
  * **Haptic Generator Lifecycle:**
    * Persistent `UIImpactFeedbackGenerator(style: .medium)` kept alive per overlay.
    * Pre-warmed via `prepare()` when hold timer arms (touch began in bookmark zone) and after each `impactOccurred()`.
    * Pre-warmed once on feed view appear. All callbacks dispatched to `@MainActor`.
* **No Mute Pill:** Native iOS does not enforce browser autoplay-muted constraints; the PWA mute pill is omitted.

### 1.6 Download All vs. Playback Concurrency
* **Dismissible Sheet & Queue Lifecycle:**
  * The "Download All" sheet is dismissible; downloading continues in the background while the app is foregrounded, and cleanly suspends when the app enters background.
  * Uses `UIApplication.shared.isIdleTimerDisabled = true` while downloading to prevent screen lock.
* **Flash I/O & Flash Bandwidth Contention:**
  * Dedicated `URLSession` on a background serial `delegateQueue` with QoS `.utility`.
  * Downloads write to `{reel_id}.mp4.part`. No per-chunk fsync; single atomic `moveItem` promotion on validation.
  * **Adaptive Concurrency:**
    * Concurrency = **3** while sheet is visible and video pool is idle.
    * Concurrency = **1** while video pool is actively playing a reel.
  * **Local-Stall Watchdog Precedence:**
    * On a local-file stall with active downloads, the watchdog suspends downloads for 10s and retries playback before counting a strike. Download bandwidth contention must never register a dead-reel strike.
    * Playback failure ladder: On `.failed`, evict file, attempt remote stream once, then apply two-strike rule (strikes tracked in-memory per launch).
  * **Thermal Throttling:**
    * Observes `ProcessInfo.thermalStateDidChangeNotification`.
    * At `.serious` or `.critical`, throttles download concurrency to 1 and suspends $\pm 1$ prefetching.
* **Device Storage Preflight:**
  * Preflights free space via `URLVolumeAvailableCapacityForKey`:
    $$\text{Required} = \text{ManifestTotalBytes (or } N \times 20\text{ MB)} + 1.0\text{ GB Headroom}$$
  * If insufficient, download is refused with an alert.
  * Persists `resumeData` per download so interrupted sessions resume without starting over.

### 1.7 Feed Container & UI Performance
* **Root Collection Pager:**
  * `UICollectionView` full-screen paging container (`isPagingEnabled = true`) wrapped in `UIViewControllerRepresentable`.
  * Coordinator implements `UICollectionViewDataSource`, `UICollectionViewDelegate`, and `UICollectionViewDataSourcePrefetching`.
  * Cells registered via modern `UICollectionView.CellRegistration`.
  * Settle detection: driven by `scrollViewDidEndDecelerating` (+ 80ms settle debounce).
  * `prepareForReuse` immediately detaches any `AVPlayerLayer`.
  * Non-adjacent cells display cached thumbnail image only; player layers attach strictly to active pool slots.
* **Grid View Thumbnail Pipeline:**
  * `URLCache`-backed async image loading + `NSCache` decoded-image memory cache.
  * Image decompression performed off-main thread; collection view prefetching enabled.

---

## 2. Component File Map

```
InstagramDigest/
├── project.yml                          # XcodeGen spec (iOS 17.0+, LiveContainer-safe, Swift 6 strict)
├── Sources/InstagramDigest/
│   ├── Info.plist                       # Document support, no background audio mode
│   ├── InstagramDigestApp.swift         # App entry point, ModelContainer setup
│   ├── Models/
│   │   ├── ReelItem.swift               # Codable reel model (with optional sizeBytes)
│   │   ├── DigestManifest.swift         # Codable root payload
│   │   └── DigestState.swift            # 4 SwiftData models (Watched, Bookmark, Daily, AppState)
│   ├── Engine/
│   │   ├── LibraryPathResolver.swift    # Sendable dynamic container path resolver
│   │   ├── MediaCacheManager.swift      # Async actor, 1.5GB cap, LRU eviction, LivePinSet, .part validation
│   │   ├── AVPlayerPool.swift           # 3-slot AVQueuePlayer pool, poolGeneration counter, teardown order
│   │   ├── AudioSessionCoordinator.swift# Interruption, route change, reset, .playback category
│   │   ├── DownloadAllCoordinator.swift # Adaptive concurrency (3 idle / 1 playing), preflight, resumeData
│   │   └── DigestDataService.swift      # Remote data.json fetching
│   └── Views/
│       ├── Feed/
│       │   ├── FeedPagerView.swift      # UICollectionView paging wrapper, CellRegistration, settle debounce
│       │   └── ReelCardView.swift       # Layer attach/detach, metadata overlays
│       ├── Gestures/
│       │   ├── SeekPanGestureRecognizer.swift # Custom subclass failing on vertical dominance
│       │   └── FeedGestureOverlay.swift # UIKit recognizer stack, spatial zones, pre-warmed haptics
│       └── Modals/
│           ├── MindfulDailyModalView.swift # 50/day completion sheet with snooze
│           ├── DownloadAllSheet.swift   # Bulk download manager with storage preflight
│           ├── GridView.swift           # 300-reel jump grid with cached thumbnail pipeline
│           └── BookmarksSheet.swift     # Saved reels with 1.5GB storage gauge & Free Local Storage button
└── .github/workflows/
    └── build-ipa.yml                    # Automated unsigned IPA CI on macOS runner
```

---

## 3. CI/CD & Build Invariants (XcodeGen)

* **Deployment Target:** iOS 17.0.
* **Swift Strict Concurrency:** `SWIFT_STRICT_CONCURRENCY: complete` (Swift 6 ready).
* **Target Family:** iPhone only (`TARGETED_DEVICE_FAMILY: "1"`).
* **Signing:** `CODE_SIGNING_ALLOWED=NO`, `CODE_SIGN_IDENTITY=""`.
* **CI Runner:** `macos-15` runner with Xcode 16.
* **IPA Packaging:** `xcodegen generate` → `xcodebuild -sdk iphoneos` → assemble unsigned IPA via `Payload/` zip for direct LiveContainer import.
