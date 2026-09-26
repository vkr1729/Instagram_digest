import Foundation
import AVFoundation
import Combine
import UIKit

/// Manages a strict 3-slot AVQueuePlayer pool (Current - 1, Current, Current + 1)
/// with zero-leak teardown, monotonic poolGeneration counter, DidPlayToEnd
/// auto-advance, KVO status observation, and LivePinSet synchronization.
@MainActor
public final class AVPlayerPool: ObservableObject {
    public static let shared = AVPlayerPool()
    public static let didEnterForegroundNotification = Notification.Name("AVPlayerPool.didEnterForegroundNotification")

    public struct SlotItem: Sendable {
        public let reel: ReelItem
        public let localURL: URL?
        public let remoteURL: URL
        public let isLocal: Bool
    }

    @MainActor
    public final class Slot {
        public let index: Int
        public let player: AVQueuePlayer
        public var looper: AVPlayerLooper?
        public var currentItem: AVPlayerItem?
        public var timeObserverToken: Any?
        public var statusCancellable: AnyCancellable?
        public var failedObserverToken: NSObjectProtocol?
        public var didPlayToEndObserverToken: NSObjectProtocol?
        public var slotItem: SlotItem?
        public weak var playerLayer: AVPlayerLayer?
        public var inFlightTask: Task<Void, Never>?

        public init(index: Int) {
            self.index = index
            self.player = AVQueuePlayer()
            self.player.actionAtItemEnd = .none
        }

        public func teardown(detachingLayer: Bool = false) {
            inFlightTask?.cancel()
            inFlightTask = nil
            player.pause()
            if let token = timeObserverToken {
                player.removeTimeObserver(token)
                timeObserverToken = nil
            }
            statusCancellable?.cancel()
            statusCancellable = nil
            if let token = failedObserverToken {
                NotificationCenter.default.removeObserver(token)
                failedObserverToken = nil
            }
            if let token = didPlayToEndObserverToken {
                NotificationCenter.default.removeObserver(token)
                didPlayToEndObserverToken = nil
            }
            looper?.disableLooping()
            looper = nil
            player.replaceCurrentItem(with: nil)
            if detachingLayer {
                playerLayer?.player = nil
                playerLayer = nil
            }
            currentItem = nil
            slotItem = nil
        }
    }

    // 3 slots: 0 -> prev (-1), 1 -> current (0), 2 -> next (+1)
    public private(set) var slotPrev = Slot(index: 0)
    public private(set) var slotCurrent = Slot(index: 1)
    public private(set) var slotNext = Slot(index: 2)

    private var slots: [Slot] {
        [slotPrev, slotCurrent, slotNext]
    }

    /// Monotonic generation counter to invalidate asynchronous tasks
    public private(set) var poolGeneration: UInt64 = 0

    /// Playback rates
    public static let availableRates: [Float] = [1.0, 1.25, 1.5, 2.0]
    @Published public var baseRate: Float = 1.25
    @Published public var isLatched2x: Bool = false
    @Published public var isPlaying: Bool = false
    @Published public var currentProgress: Double = 0.0 // 0.0 to 1.0
    @Published public var currentDuration: Double = 0.0
    @Published public var currentTime: Double = 0.0

    /// Watchdog and strike tracking
    private var reelStrikes: [String: Int] = [:]
    private var localStallCount: [String: Int] = [:]
    private var watchedLatchedReelIDs: Set<String> = []
    /// B2: user playback intent. Async slot-load completions must honor it:
    /// a pause/background/interruption that lands mid-load must not be
    /// overridden when the load finishes (ghost audio).
    private var wantsPlayback: Bool = true

    /// Active index in playlist
    public private(set) var currentIndex: Int = -1
    public private(set) var currentItems: [ReelItem] = []
    public var currentWeekID: String = ""

    /// Callback hooks for watched events and stall alerts
    public var onWatchedMilestone: (@MainActor (ReelItem) -> Void)?
    public var onStallWatchdogTriggered: (@MainActor () -> Void)?

    private var memoryPressureObserver: NSObjectProtocol?
    private var backgroundObserver: NSObjectProtocol?
    private var foregroundObserver: NSObjectProtocol?
    private var thermalObserver: NSObjectProtocol?

    public init() {
        setupLifecycleObservers()
    }

    deinit {
        if let o = memoryPressureObserver { NotificationCenter.default.removeObserver(o) }
        if let o = backgroundObserver { NotificationCenter.default.removeObserver(o) }
        if let o = foregroundObserver { NotificationCenter.default.removeObserver(o) }
        if let o = thermalObserver { NotificationCenter.default.removeObserver(o) }
    }

    /// End-of-item threshold: a currentTime within this many seconds of
    /// duration counts as "at end" and must restart from zero.
    /// AVPlayer ignores play()/rate at end; only a seek to .zero restarts it.
    /// Without this, swiping back to a fully-watched reel strands it on the
    /// last frame (field bug: auto-advanced reel stays completed on return).
    public nonisolated static let endRestartThreshold: Double = 0.3

    /// Pure helper: true when playback sits at/over the end and needs a restart.
    public nonisolated static func isAtEnd(currentTime: Double, duration: Double, threshold: Double = endRestartThreshold) -> Bool {
        guard currentTime.isFinite, duration.isFinite, duration > 0, threshold.isFinite else { return false }
        return currentTime >= duration - threshold
    }

    public var effectiveRate: Float {
        isLatched2x ? 2.0 : baseRate
    }

    public func cyclePlaybackRate() {
        if isLatched2x {
            isLatched2x = false
        }
        if let idx = Self.availableRates.firstIndex(of: baseRate) {
            let nextIdx = (idx + 1) % Self.availableRates.count
            baseRate = Self.availableRates[nextIdx]
        } else {
            baseRate = 1.25
        }
        applyPlaybackRate()
    }

    public func setLatched2x(_ latched: Bool) {
        isLatched2x = latched
        applyPlaybackRate()
    }

    private func applyPlaybackRate() {
        if isPlaying {
            slotCurrent.player.rate = effectiveRate
        }
    }

    /// Starts slotCurrent, seeking to zero first when its item sits at end.
    /// AVPlayer ignores play()/rate at end-of-item, so returning to a
    /// fully-watched reel must seek before setting rate, otherwise the reel
    /// stays frozen on its last frame with isPlaying incorrectly true.
    /// Partially-watched reels resume from their preserved position untouched.
    private func playCurrentSlotRestartingIfNeeded() {
        let slot = slotCurrent
        let player = slot.player
        if let item = player.currentItem {
            let duration = item.duration.seconds
            let current = player.currentTime().seconds
            if Self.isAtEnd(currentTime: current, duration: duration) {
                self.currentProgress = 0.0
                self.currentTime = 0.0
                self.isPlaying = true
                AudioSessionCoordinator.shared.activateSession()
                player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) { [weak self, weak slot, weak item] _ in
                    guard let self = self, let s = slot, let expected = item else { return }
                    guard self.slotCurrent === s, s.player.currentItem === expected else { return }
                    if self.isPlaying {
                        s.player.rate = self.effectiveRate
                    }
                }
                return
            }
        }
        isPlaying = true
        AudioSessionCoordinator.shared.activateSession()
        player.rate = effectiveRate
    }

    // MARK: - Navigation & Loading

    public func setReels(_ items: [ReelItem], weekID: String, startIndex: Int = 0) {
        self.currentItems = items
        self.currentWeekID = weekID
        self.watchedLatchedReelIDs.removeAll()
        self.currentIndex = -1
        guard !items.isEmpty else {
            // Empty playlist: park the pool in a defined state instead of
            // leaving currentIndex stale on a previous list.
            poolGeneration &+= 1
            cancelAllInFlightTasks()
            slotPrev.teardown()
            slotCurrent.teardown()
            slotNext.teardown()
            self.currentIndex = -1
            self.isLatched2x = false
            self.isPlaying = false
            return
        }
        // Clamp: an out-of-range startIndex must never leave currentIndex stale.
        setCurrentIndex(min(max(0, startIndex), items.count - 1))
    }

    /// Filters the playback pool to a specific category (e.g. "all", "entertainment", "finance", "ai_tech", "niche", "health", "food")
    /// - Returns: false when the category matched nothing, leaving the pool
    ///   untouched — B14: an empty category must not silently play the full
    ///   feed while the chip claims otherwise.
    @discardableResult
    public func filterByCategory(_ categoryKey: String, allReels: [ReelItem], weekID: String) -> Bool {
        let filtered: [ReelItem]
        if categoryKey == "all" {
            filtered = allReels
        } else if categoryKey == "entertainment" {
            filtered = allReels.filter {
                let cat = $0.category?.lowercased() ?? ""
                return cat == "entertainment" || cat == "culture"
            }
        } else if categoryKey == "ai_tech" {
            filtered = allReels.filter {
                let cat = $0.category?.lowercased() ?? ""
                return cat == "ai_tech" || cat == "tech" || cat == "technology"
            }
        } else if categoryKey == "niche" {
            filtered = allReels.filter {
                let cat = $0.category?.lowercased() ?? ""
                return cat == "niche" || cat == "explainer"
            }
        } else {
            filtered = allReels.filter {
                ($0.category?.lowercased() ?? "") == categoryKey.lowercased()
            }
        }

        guard !filtered.isEmpty else { return false }
        setReels(filtered, weekID: weekID, startIndex: 0)
        return true
    }

    public func setCurrentIndex(_ newIndex: Int) {
        guard !currentItems.isEmpty, newIndex >= 0, newIndex < currentItems.count else { return }
        if newIndex == currentIndex {
            if !isPlaying, slotCurrent.player.currentItem != nil { play() }
            return
        }
        wantsPlayback = true // navigation is an explicit "play this" gesture

        poolGeneration &+= 1
        let thisGeneration = poolGeneration
        let oldIndex = self.currentIndex

        self.currentIndex = newIndex
        self.isLatched2x = false

        // Determine target reel IDs for slots
        let prevItem = newIndex > 0 ? currentItems[newIndex - 1] : nil
        let currItem = currentItems[newIndex]
        let nextItem = newIndex + 1 < currentItems.count ? currentItems[newIndex + 1] : nil

        // Sync LivePinSet to MediaCacheManager
        var boundIDs = Set<String>()
        if let p = prevItem { boundIDs.insert(p.id) }
        boundIDs.insert(currItem.id)
        if let n = nextItem { boundIDs.insert(n.id) }

        Task {
            await MediaCacheManager.shared.setActiveVideoPoolReelIDs(boundIDs, generation: thisGeneration)
        }

        let isThermalThrottled = ProcessInfo.processInfo.thermalState == .serious || ProcessInfo.processInfo.thermalState == .critical

        // Check for contiguous forward step (+1) with already buffered slotNext
        if oldIndex >= 0 && newIndex == oldIndex + 1 && slotNext.slotItem?.reel.id == currItem.id, let promotedItem = slotNext.currentItem {
            // FORWARD ROTATION:
            // slotNext is promoted to slotCurrent (already has buffer & layer attached).
            // slotCurrent is demoted to slotPrev.
            // slotPrev is recycled to become the new slotNext.
            let oldPrev = slotPrev
            let oldCurrent = slotCurrent
            let promotedSlot = slotNext

            // 1. Demote old current to prev: pause, mute, remove observers
            if let token = oldCurrent.timeObserverToken {
                oldCurrent.player.removeTimeObserver(token)
                oldCurrent.timeObserverToken = nil
            }
            oldCurrent.statusCancellable?.cancel()
            oldCurrent.statusCancellable = nil
            if let token = oldCurrent.failedObserverToken {
                NotificationCenter.default.removeObserver(token)
                oldCurrent.failedObserverToken = nil
            }
            if let token = oldCurrent.didPlayToEndObserverToken {
                NotificationCenter.default.removeObserver(token)
                oldCurrent.didPlayToEndObserverToken = nil
            }
            oldCurrent.player.pause()
            oldCurrent.player.isMuted = true

            // 2. Promote slotNext to slotCurrent
            slotCurrent = promotedSlot
            slotPrev = oldCurrent
            slotNext = oldPrev

            // Ensure layer and playback state on promoted slot
            slotCurrent.playerLayer?.player = slotCurrent.player
            slotCurrent.playerLayer?.videoGravity = .resizeAspect
            slotCurrent.player.isMuted = false
            slotCurrent.player.volume = 1.0

            let isLocal = LibraryPathResolver.shared.isLocalFileAvailable(for: currentWeekID, reelID: currItem.id)
            observePlayerItemStatus(for: slotCurrent, item: promotedItem, reel: currItem, isLocal: isLocal, generation: thisGeneration)
            attachTimeObserver(to: slotCurrent, reel: currItem)
            playCurrentSlotRestartingIfNeeded()

            // 3. Recycle oldPrev into slotNext to preload nextItem
            if !isThermalThrottled, let next = nextItem {
                configureSlot(slotNext, with: next, generation: thisGeneration)
            } else {
                slotNext.teardown()
            }

        // Check for contiguous backward step (-1) with already buffered slotPrev
        } else if oldIndex >= 0 && newIndex == oldIndex - 1 && slotPrev.slotItem?.reel.id == currItem.id, let promotedItem = slotPrev.currentItem {
            // BACKWARD ROTATION:
            // slotPrev is promoted to slotCurrent.
            // slotCurrent is demoted to slotNext.
            // slotNext is recycled to become the new slotPrev.
            let oldNext = slotNext
            let oldCurrent = slotCurrent
            let promotedSlot = slotPrev

            // 1. Demote old current to next: pause, mute, remove observers
            if let token = oldCurrent.timeObserverToken {
                oldCurrent.player.removeTimeObserver(token)
                oldCurrent.timeObserverToken = nil
            }
            oldCurrent.statusCancellable?.cancel()
            oldCurrent.statusCancellable = nil
            if let token = oldCurrent.failedObserverToken {
                NotificationCenter.default.removeObserver(token)
                oldCurrent.failedObserverToken = nil
            }
            if let token = oldCurrent.didPlayToEndObserverToken {
                NotificationCenter.default.removeObserver(token)
                oldCurrent.didPlayToEndObserverToken = nil
            }
            oldCurrent.player.pause()
            oldCurrent.player.isMuted = true

            // 2. Promote slotPrev to slotCurrent
            slotCurrent = promotedSlot
            slotNext = oldCurrent
            slotPrev = oldNext

            // Ensure layer and playback state on promoted slot
            slotCurrent.playerLayer?.player = slotCurrent.player
            slotCurrent.playerLayer?.videoGravity = .resizeAspect
            slotCurrent.player.isMuted = false
            slotCurrent.player.volume = 1.0

            let isLocal = LibraryPathResolver.shared.isLocalFileAvailable(for: currentWeekID, reelID: currItem.id)
            observePlayerItemStatus(for: slotCurrent, item: promotedItem, reel: currItem, isLocal: isLocal, generation: thisGeneration)
            attachTimeObserver(to: slotCurrent, reel: currItem)
            playCurrentSlotRestartingIfNeeded()

            // 3. Recycle oldNext into slotPrev to preload prevItem
            if !isThermalThrottled, let prev = prevItem {
                configureSlot(slotPrev, with: prev, generation: thisGeneration)
            } else {
                slotPrev.teardown()
            }

        } else {
            // NON-CONTIGUOUS JUMP or unbuffered slot: full reconfiguration
            cancelAllInFlightTasks()
            configureCurrentSlot(with: currItem, generation: thisGeneration)

            if !isThermalThrottled {
                if let prev = prevItem {
                    configureSlot(slotPrev, with: prev, generation: thisGeneration)
                } else {
                    slotPrev.teardown()
                }

                if let next = nextItem {
                    configureSlot(slotNext, with: next, generation: thisGeneration)
                } else {
                    slotNext.teardown()
                }
            } else {
                slotPrev.teardown()
                slotNext.teardown()
            }
        }
    }

    // MARK: - Slot Configuration

    private func configureCurrentSlot(with item: ReelItem, generation: UInt64, restoringTo targetTime: Double? = nil, forceRebuild: Bool = false) {
        let slot = slotCurrent

        // If slot already holds this item and has currentItem, resume unless rebuilding
        if !forceRebuild, targetTime == nil, slot.slotItem?.reel.id == item.id, slot.player.currentItem != nil {
            // Reconnect the layer: it may have been detached by cell reuse or backgrounding.
            slot.playerLayer?.player = slot.player
            slot.playerLayer?.videoGravity = .resizeAspect
            // The status publisher emits the current value on subscribe, but
            // this early-return path attaches no new observer: an already-
            // failed item would otherwise sit on a black frame forever.
            if slot.player.currentItem?.status == .failed {
                self.handlePlaybackError(for: item, isLocal: slot.slotItem?.isLocal ?? false)
                return
            }
            playCurrentSlotRestartingIfNeeded()
            return
        }

        slot.teardown()

        let isLocal = LibraryPathResolver.shared.isLocalFileAvailable(for: currentWeekID, reelID: item.id)
        let resolvedLocal = LibraryPathResolver.shared.resolvedLocalFileURL(for: currentWeekID, reelID: item.id)
        let mediaURL = resolvedLocal ?? item.videoUrl

        let slotItem = SlotItem(reel: item, localURL: resolvedLocal, remoteURL: item.videoUrl, isLocal: isLocal)
        slot.slotItem = slotItem

        let assetOptions: [String: Any]? = isLocal ? nil : [AVURLAssetPreferPreciseDurationAndTimingKey: false]
        let asset = AVURLAsset(url: mediaURL, options: assetOptions)
        slot.inFlightTask?.cancel()
        slot.inFlightTask = Task { @MainActor [weak self, weak slot] in
            guard let self = self, let slot = slot else { return }
            do {
                if isLocal {
                    _ = try await asset.load(.isPlayable, .duration)
                }
                guard self.poolGeneration == generation else { return }

                let playerItem = AVPlayerItem(asset: asset)
                playerItem.audioTimePitchAlgorithm = .timeDomain
                if !isLocal {
                    playerItem.preferredForwardBufferDuration = 8.0
                }

                // Dual-branch stalling configuration
                slot.player.automaticallyWaitsToMinimizeStalling = !isLocal

                slot.currentItem = playerItem
                slot.player.replaceCurrentItem(with: playerItem)
                slot.playerLayer?.player = slot.player
                slot.playerLayer?.videoGravity = .resizeAspect

                // Unmute current slot
                slot.player.isMuted = false
                slot.player.volume = 1.0

                // Setup KVO status and failure notifications
                self.observePlayerItemStatus(for: slot, item: playerItem, reel: item, isLocal: isLocal, generation: generation)

                // Attach throttled 0.5s time observer
                self.attachTimeObserver(to: slot, reel: item)

                // Restore position if specified (e.g. after media services reset)
                if let restore = targetTime, restore > 0 {
                    let cmTime = CMTime(seconds: restore, preferredTimescale: 600)
                    await slot.player.seek(to: cmTime, toleranceBefore: .zero, toleranceAfter: .zero)
                }

                // Start playback and notify AudioSessionCoordinator.
                // B2: honor intent — a pause/background/interruption that
                // landed mid-load wins over the finished load.
                if self.wantsPlayback {
                    self.isPlaying = true
                    slot.player.rate = self.effectiveRate
                    AudioSessionCoordinator.shared.activateSession()
                }

            } catch {
                if error is CancellationError { return }
                guard self.poolGeneration == generation else { return }
                self.handlePlaybackError(for: item, isLocal: isLocal)
            }
        }
    }

    private func configureSlot(_ slot: Slot, with item: ReelItem, generation: UInt64) {
        slot.teardown()

        let isLocal = LibraryPathResolver.shared.isLocalFileAvailable(for: currentWeekID, reelID: item.id)
        let resolvedLocal = LibraryPathResolver.shared.resolvedLocalFileURL(for: currentWeekID, reelID: item.id)
        let mediaURL = resolvedLocal ?? item.videoUrl

        let slotItem = SlotItem(reel: item, localURL: resolvedLocal, remoteURL: item.videoUrl, isLocal: isLocal)
        slot.slotItem = slotItem

        let assetOptions: [String: Any]? = isLocal ? nil : [AVURLAssetPreferPreciseDurationAndTimingKey: false]
        let asset = AVURLAsset(url: mediaURL, options: assetOptions)
        slot.inFlightTask?.cancel()
        slot.inFlightTask = Task { @MainActor [weak self, weak slot] in
            guard let self = self, let slot = slot else { return }
            do {
                if isLocal {
                    _ = try await asset.load(.isPlayable, .duration)
                }
                guard self.poolGeneration == generation else { return }

                let playerItem = AVPlayerItem(asset: asset)
                playerItem.audioTimePitchAlgorithm = .timeDomain
                if !isLocal {
                    playerItem.preferredForwardBufferDuration = 8.0
                }

                slot.player.automaticallyWaitsToMinimizeStalling = !isLocal
                slot.player.isMuted = true
                slot.player.volume = 0.0

                slot.currentItem = playerItem
                slot.player.replaceCurrentItem(with: playerItem)
                slot.playerLayer?.player = slot.player
                slot.playerLayer?.videoGravity = .resizeAspect
                slot.player.pause()
            } catch {
                if error is CancellationError { return }
                // Secondary slot loading failures can be silently ignored
            }
        }
    }

    // MARK: - Status & Failure Observation

    public var onAutoAdvanceToNext: (@MainActor () -> Void)?

    private func handlePlaybackEnded(for reel: ReelItem, slot: Slot) {
        guard slot === slotCurrent else { return }
        if currentIndex + 1 < currentItems.count {
            onAutoAdvanceToNext?()
        } else {
            // Last item: replay only after the seek completes, otherwise play()
            // can resume at the end position and re-fire DidPlayToEnd in a tight loop.
            slot.player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) { [weak self, weak slot] _ in
                guard let self = self, let s = slot, self.slotCurrent === s else { return }
                // B2: replaying the last item is playback — honor intent.
                if self.wantsPlayback {
                    s.player.rate = self.effectiveRate
                }
            }
        }
    }

    private func observePlayerItemStatus(
        for slot: Slot,
        item playerItem: AVPlayerItem,
        reel: ReelItem,
        isLocal: Bool,
        generation: UInt64
    ) {
        slot.statusCancellable = playerItem.publisher(for: \.status)
            .receive(on: RunLoop.main)
            .sink { [weak self, weak slot, weak playerItem] status in
                guard let self = self, let s = slot, s.currentItem === playerItem else { return }
                guard self.poolGeneration == generation else { return }

                if status == .readyToPlay {
                    // Reset failure strikes on successful load
                    self.reelStrikes[reel.id] = 0
                    self.localStallCount[reel.id] = 0
                } else if status == .failed {
                    self.handlePlaybackError(for: reel, isLocal: isLocal)
                }
            }

        slot.failedObserverToken = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: playerItem,
            queue: .main
        ) { [weak self, weak slot, weak playerItem] _ in
            guard let self = self, let s = slot, s.currentItem === playerItem else { return }
            self.handlePlaybackError(for: reel, isLocal: isLocal)
        }

        slot.didPlayToEndObserverToken = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: playerItem,
            queue: .main
        ) { [weak self, weak slot, weak playerItem] _ in
            guard let self = self, let s = slot, s.currentItem === playerItem else { return }
            self.handlePlaybackEnded(for: reel, slot: s)
        }
    }

    // MARK: - Time Observer & Watched Triggers

    private func attachTimeObserver(to slot: Slot, reel: ReelItem) {
        if let token = slot.timeObserverToken {
            slot.player.removeTimeObserver(token)
            slot.timeObserverToken = nil
        }
        // Throttled 0.5s time observer ticks per contract
        let interval = CMTime(seconds: 0.5, preferredTimescale: 600)
        slot.timeObserverToken = slot.player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self, weak slot] time in
            guard let self = self, let s = slot, self.slotCurrent === s else { return }
            // Always publish the clock so the hairline progress never looks
            // frozen while duration metadata is still resolving.
            let cur = time.seconds
            if cur.isFinite {
                self.currentTime = cur
            }
            guard let duration = s.player.currentItem?.duration.seconds, duration.isFinite, duration > 0 else { return }

            self.currentDuration = duration
            // NaN guard: a non-finite clock would publish NaN progress and
            // collapse the hairline bar layout (width NaN).
            let progress = cur.isFinite ? max(0.0, min(1.0, cur / duration)) : 0.0
            self.currentProgress = progress

            // 80% progress milestone trigger with once-per-reel latch
            if progress >= 0.80 && !self.watchedLatchedReelIDs.contains(reel.id) {
                self.watchedLatchedReelIDs.insert(reel.id)
                self.onWatchedMilestone?(reel)
            }
        }
    }

    // MARK: - Playback Controls

    public func togglePlayPause() {
        if isPlaying {
            pause()
        } else {
            play()
        }
    }

    public func play() {
        wantsPlayback = true
        playCurrentSlotRestartingIfNeeded()
    }

    public func pause() {
        wantsPlayback = false
        isPlaying = false
        slotCurrent.player.pause()
        AudioSessionCoordinator.shared.deactivateSession()
    }

    public func commitSeek(to targetFraction: Double) {
        guard let currentItem = slotCurrent.player.currentItem else { return }
        let duration = currentItem.duration.seconds
        guard duration.isFinite, duration > 0 else { return }

        let targetSec = duration * max(0.0, min(1.0, targetFraction))
        let targetTime = CMTime(seconds: targetSec, preferredTimescale: 600)

        let isLocal = slotCurrent.slotItem?.isLocal ?? false
        let tolerance = isLocal ? CMTime.zero : CMTime(seconds: 0.5, preferredTimescale: 600)

        let targetSlot = slotCurrent
        let expectedReelID = slotCurrent.slotItem?.reel.id
        let gen = self.poolGeneration

        slotCurrent.player.seek(to: targetTime, toleranceBefore: tolerance, toleranceAfter: tolerance) { [weak self, weak targetSlot] finished in
            guard let self = self, let slot = targetSlot, finished else { return }
            guard self.poolGeneration == gen, self.slotCurrent === slot, slot.slotItem?.reel.id == expectedReelID else { return }
            if self.isPlaying {
                slot.player.rate = self.effectiveRate
            }
            // Scrub past 35% trigger
            if targetFraction >= 0.35, let reel = slot.slotItem?.reel {
                self.onWatchedMilestone?(reel)
            }
        }
    }

    // MARK: - Failure Ladder & Watchdog

    private func handlePlaybackError(for item: ReelItem, isLocal: Bool) {
        if isLocal {
            let stalls = (localStallCount[item.id] ?? 0) + 1
            localStallCount[item.id] = stalls

            if stalls == 1 {
                // First stall: suspend downloads for 10s and retry local playback first with forced rebuild
                DownloadAllCoordinator.shared.suspendForWatchdog(durationSeconds: 10.0)
                onStallWatchdogTriggered?()

                // Retry local playback with forced rebuild
                let gen = poolGeneration
                Task { @MainActor [weak self] in
                    guard let self = self, self.poolGeneration == gen else { return }
                    self.configureCurrentSlot(with: item, generation: gen, forceRebuild: true)
                }
            } else {
                // Second consecutive local stall: evict corrupted file via actor, then fall back to remote stream
                Task {
                    await MediaCacheManager.shared.evictLocalFeedFile(weekID: self.currentWeekID, reelID: item.id)
                }

                self.slotCurrent.teardown()
                let remoteAsset = AVURLAsset(url: item.videoUrl)
                let itemGen = poolGeneration
                Task { @MainActor [weak self] in
                    guard let self = self, self.poolGeneration == itemGen else { return }
                    let playerItem = AVPlayerItem(asset: remoteAsset)
                    playerItem.audioTimePitchAlgorithm = .timeDomain
                    playerItem.preferredForwardBufferDuration = 8.0
                    self.slotCurrent.player.automaticallyWaitsToMinimizeStalling = true
                    // No AVPlayerLooper here: a looper would swallow DidPlayToEnd and
                    // break auto-advance. The end observer below drives advancement.
                    self.slotCurrent.slotItem = SlotItem(reel: item, localURL: nil, remoteURL: item.videoUrl, isLocal: false)
                    self.slotCurrent.currentItem = playerItem
                    self.slotCurrent.player.replaceCurrentItem(with: playerItem)
                    self.slotCurrent.playerLayer?.player = self.slotCurrent.player
                    self.slotCurrent.playerLayer?.videoGravity = .resizeAspect
                    self.slotCurrent.player.isMuted = false
                    self.slotCurrent.player.volume = 1.0
                    self.observePlayerItemStatus(for: self.slotCurrent, item: playerItem, reel: item, isLocal: false, generation: itemGen)
                    self.attachTimeObserver(to: self.slotCurrent, reel: item)
                    // B2: same intent guard as the primary load path.
                    if self.wantsPlayback {
                        self.isPlaying = true
                        self.slotCurrent.player.rate = self.effectiveRate
                        AudioSessionCoordinator.shared.activateSession()
                    }
                }
            }
        } else {
            // Remote stream error: apply two-strike rule
            let strikes = (reelStrikes[item.id] ?? 0) + 1
            reelStrikes[item.id] = strikes
            if strikes == 1 {
                // First strike: retry once with a fresh asset, like the local
                // ladder does — a transient error must not strand the reel.
                let gen = poolGeneration
                Task { @MainActor [weak self] in
                    guard let self = self, self.poolGeneration == gen else { return }
                    self.configureCurrentSlot(with: item, generation: gen, forceRebuild: true)
                }
            } else if currentIndex + 1 < currentItems.count {
                // Second strike: skip dead reel and advance
                setCurrentIndex(currentIndex + 1)
            }
        }
    }

    // MARK: - Media Services Reset Rebuild

    /// Forces a complete rebuild of the current slot after media-services crash and restores position
    public func rebuildCurrentSlot(restoringTo position: Double) {
        guard currentIndex >= 0, currentIndex < currentItems.count else { return }
        poolGeneration &+= 1
        let gen = poolGeneration
        cancelAllInFlightTasks()
        let item = currentItems[currentIndex]
        configureCurrentSlot(with: item, generation: gen, restoringTo: position)
    }

    // MARK: - Lifecycle & Memory Pressure

    private var wasPlayingBeforeBackground: Bool = false

    private func setupLifecycleObservers() {
        memoryPressureObserver = NotificationCenter.default.addObserver(
            forName: UIApplication.didReceiveMemoryWarningNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.collapsePoolToCurrentSlotOnly()
        }

        backgroundObserver = NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.handleDidEnterBackground()
        }

        foregroundObserver = NotificationCenter.default.addObserver(
            forName: UIApplication.willEnterForegroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.handleWillEnterForeground()
        }

        thermalObserver = NotificationCenter.default.addObserver(
            forName: ProcessInfo.thermalStateDidChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            let state = ProcessInfo.processInfo.thermalState
            if state == .serious || state == .critical {
                self?.collapsePoolToCurrentSlotOnly()
            }
        }
    }

    /// On memory warning, evict slots -1 and +1 immediately and cancel their background tasks
    public func collapsePoolToCurrentSlotOnly() {
        slotPrev.teardown()
        slotNext.teardown()
    }

    private func cancelAllInFlightTasks() {
        for slot in slots {
            slot.inFlightTask?.cancel()
            slot.inFlightTask = nil
        }
    }

    private func handleDidEnterBackground() {
        wasPlayingBeforeBackground = isPlaying
        pause()
        for slot in slots {
            slot.playerLayer?.player = nil
        }
    }

    private func handleWillEnterForeground() {
        for slot in slots {
            slot.playerLayer?.player = slot.player
        }
        NotificationCenter.default.post(name: Self.didEnterForegroundNotification, object: self)
        if wasPlayingBeforeBackground {
            play()
        }
    }

    public func attachLayer(_ layer: AVPlayerLayer, forSlotIndex index: Int) {
        let slot: Slot?
        switch index {
        case 0: slot = slotPrev
        case 1: slot = slotCurrent
        case 2: slot = slotNext
        default: slot = nil
        }
        if let s = slot {
            attachLayer(layer, to: s)
        }
    }

    public func attachLayer(_ layer: AVPlayerLayer, to slot: Slot) {
        // Disconnect layer from any other slot
        if slotCurrent !== slot && slotCurrent.playerLayer === layer { slotCurrent.playerLayer = nil }
        if slotPrev !== slot && slotPrev.playerLayer === layer { slotPrev.playerLayer = nil }
        if slotNext !== slot && slotNext.playerLayer === layer { slotNext.playerLayer = nil }

        // Disconnect old layer from this slot if different
        if let oldLayer = slot.playerLayer, oldLayer !== layer {
            oldLayer.player = nil
        }

        slot.playerLayer = layer
        if layer.player !== slot.player {
            layer.player = slot.player
        }
        layer.videoGravity = .resizeAspect
    }

    public func detachLayer(_ layer: AVPlayerLayer) {
        if slotCurrent.playerLayer === layer { slotCurrent.playerLayer = nil }
        if slotPrev.playerLayer === layer { slotPrev.playerLayer = nil }
        if slotNext.playerLayer === layer { slotNext.playerLayer = nil }
        if layer.player != nil {
            layer.player = nil
        }
    }
}
