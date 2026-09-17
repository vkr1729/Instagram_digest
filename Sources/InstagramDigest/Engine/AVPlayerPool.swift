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

        public init(index: Int) {
            self.index = index
            self.player = AVQueuePlayer()
            self.player.actionAtItemEnd = .none
        }

        public func teardown() {
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
            playerLayer?.player = nil
            playerLayer = nil
            currentItem = nil
            slotItem = nil
        }
    }

    // 3 slots: 0 -> prev (-1), 1 -> current (0), 2 -> next (+1)
    public let slotPrev = Slot(index: 0)
    public let slotCurrent = Slot(index: 1)
    public let slotNext = Slot(index: 2)

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

    /// Active index in playlist
    public private(set) var currentIndex: Int = -1
    public private(set) var currentItems: [ReelItem] = []
    public var currentWeekID: String = ""

    /// Callback hooks for watched events and stall alerts
    public var onWatchedMilestone: (@MainActor (ReelItem) -> Void)?
    public var onStallWatchdogTriggered: (@MainActor () -> Void)?

    private var inFlightTasks: [Int: Task<Void, Never>] = [:] // slotIndex -> Task
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

    // MARK: - Navigation & Loading

    public func setReels(_ items: [ReelItem], weekID: String, startIndex: Int = 0) {
        self.currentItems = items
        self.currentWeekID = weekID
        self.watchedLatchedReelIDs.removeAll()
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
    public func filterByCategory(_ categoryKey: String, allReels: [ReelItem], weekID: String) {
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

        setReels(filtered.isEmpty ? allReels : filtered, weekID: weekID, startIndex: 0)
    }

    public func setCurrentIndex(_ newIndex: Int) {
        guard !currentItems.isEmpty, newIndex >= 0, newIndex < currentItems.count else { return }

        poolGeneration &+= 1
        let thisGeneration = poolGeneration

        cancelAllInFlightTasks()

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

        // Configure Slot 1 (Current)
        configureCurrentSlot(with: currItem, generation: thisGeneration)

        // Check thermal state before preloading slots -1 and +1
        let isThermalThrottled = ProcessInfo.processInfo.thermalState == .serious || ProcessInfo.processInfo.thermalState == .critical
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

    // MARK: - Slot Configuration

    private func configureCurrentSlot(with item: ReelItem, generation: UInt64, restoringTo targetTime: Double? = nil, forceRebuild: Bool = false) {
        let slot = slotCurrent

        // If slot already holds this item and has currentItem, resume unless rebuilding
        if !forceRebuild, targetTime == nil, slot.slotItem?.reel.id == item.id, slot.player.currentItem != nil {
            applyPlaybackRate()
            self.isPlaying = true
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
        inFlightTasks[slot.index]?.cancel()
        inFlightTasks[slot.index] = Task { @MainActor [weak self] in
            guard let self = self else { return }
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

                // Start playback and notify AudioSessionCoordinator
                self.isPlaying = true
                slot.player.rate = self.effectiveRate
                AudioSessionCoordinator.shared.activateSession()

            } catch {
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
        inFlightTasks[slot.index]?.cancel()
        inFlightTasks[slot.index] = Task { @MainActor [weak self] in
            guard let self = self else { return }
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
                slot.player.pause()
            } catch {
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
            slot.player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) { [weak slot] _ in
                slot?.player.play()
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
        // Throttled 0.5s time observer ticks per contract
        let interval = CMTime(seconds: 0.5, preferredTimescale: 600)
        slot.timeObserverToken = slot.player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self, weak slot] time in
            guard let self = self, let s = slot, self.slotCurrent === s else { return }
            guard let duration = s.player.currentItem?.duration.seconds, duration.isFinite, duration > 0 else { return }

            let cur = time.seconds
            self.currentTime = cur
            self.currentDuration = duration
            let progress = max(0.0, min(1.0, cur / duration))
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
        isPlaying = true
        AudioSessionCoordinator.shared.activateSession()
        slotCurrent.player.rate = effectiveRate
    }

    public func pause() {
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

        slotCurrent.player.seek(to: targetTime, toleranceBefore: tolerance, toleranceAfter: tolerance) { [weak self] finished in
            guard let self = self, finished else { return }
            if self.isPlaying {
                self.slotCurrent.player.rate = self.effectiveRate
            }
            // Scrub past 35% trigger
            if targetFraction >= 0.35, let reel = self.slotCurrent.slotItem?.reel {
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
                    self.slotCurrent.player.isMuted = false
                    self.slotCurrent.player.volume = 1.0
                    self.observePlayerItemStatus(for: self.slotCurrent, item: playerItem, reel: item, isLocal: false, generation: itemGen)
                    self.attachTimeObserver(to: self.slotCurrent, reel: item)
                    self.isPlaying = true
                    self.slotCurrent.player.rate = self.effectiveRate
                    AudioSessionCoordinator.shared.activateSession()
                }
            }
        } else {
            // Remote stream error: apply two-strike rule
            let strikes = (reelStrikes[item.id] ?? 0) + 1
            reelStrikes[item.id] = strikes
            if strikes >= 2 {
                // Skip dead reel and advance
                if currentIndex + 1 < currentItems.count {
                    setCurrentIndex(currentIndex + 1)
                }
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
        inFlightTasks[slotPrev.index]?.cancel()
        inFlightTasks[slotPrev.index] = nil
        slotPrev.teardown()

        inFlightTasks[slotNext.index]?.cancel()
        inFlightTasks[slotNext.index] = nil
        slotNext.teardown()
    }

    private func cancelAllInFlightTasks() {
        for (_, task) in inFlightTasks {
            task.cancel()
        }
        inFlightTasks.removeAll()
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
        let slot = slots.first { $0.index == index }
        slot?.playerLayer = layer
        layer.player = slot?.player
    }
}
