import Foundation
import UIKit
import Combine

/// Manages background bulk downloads for weekly reels with adaptive concurrency (3 idle / 1 playing),
/// thermal throttling, storage preflight, persisted resumeData, and 10s watchdog stall suspension.
@MainActor
public final class DownloadAllCoordinator: NSObject, ObservableObject, URLSessionDownloadDelegate, @unchecked Sendable {
    public static let shared = DownloadAllCoordinator()

    public struct DownloadItem: Identifiable, Sendable {
        public let id: String
        public let reel: ReelItem
        public let weekID: String
    }

    public enum CoordinatorState: Equatable, Sendable {
        case idle
        case preparing
        case downloading(completed: Int, total: Int, currentReelID: String?)
        case completed
        case completedWithFailures(ready: Int, failed: Int)
        case paused
        case failed(String)
    }

    @Published public var state: CoordinatorState = .idle
    @Published public var overallProgress: Double = 0.0 // 0.0 to 1.0
    @Published public var activeConcurrency: Int = 3
    @Published public var isSuspended: Bool = false

    private var queue: [DownloadItem] = []
    private var inFlightTasks: [Int: (item: DownloadItem, task: URLSessionDownloadTask)] = [:]
    private var retryCounts: [String: Int] = [:]
    private let maxRetriesPerItem: Int = 3

    private var totalInBatch: Int = 0
    private var completedInBatch: Int = 0
    @Published public private(set) var failedInBatch: Int = 0
    private var hasRefreshedManifestForExpiredURLs: Bool = false
    private var isRefreshingForExpiry: Bool = false
    private var currentWeekID: String = ""

    private var urlSession: URLSession!
    private var cancellables = Set<AnyCancellable>()
    private var watchdogResumeTask: Task<Void, Never>?
    private var notificationTokens: [NSObjectProtocol] = []

    private var hasActiveDownloads: Bool {
        !queue.isEmpty || !inFlightTasks.isEmpty
    }

    private override init() {
        super.init()
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = true
        config.timeoutIntervalForResource = 300
        let delegateQueue = OperationQueue()
        delegateQueue.name = "com.instagramdigest.downloadqueue"
        delegateQueue.qualityOfService = .utility
        delegateQueue.maxConcurrentOperationCount = 1

        self.urlSession = URLSession(configuration: config, delegate: self, delegateQueue: delegateQueue)

        setupAdaptiveConcurrencyObservers()
        setupLifecycleObservers()
    }

    // MARK: - Preflight Storage Verification

    /// Checks if device has sufficient storage: sum of pending reel sizes (~7.5 MB average)
    public func preflightStorage(reels: [ReelItem], weekID: String? = nil) -> (isSufficient: Bool, requiredBytes: Int64, availableBytes: Int64) {
        let targetWeek = weekID ?? self.currentWeekID
        let pending = reels.filter { reel in
            if targetWeek.isEmpty { return true }
            return !LibraryPathResolver.shared.isLocalFileAvailable(for: targetWeek, reelID: reel.id)
        }
        // Only pending reels count: when everything is already downloaded the
        // requirement is zero, never a re-measure of the full batch.
        var estimatedTotal: Int64 = 0
        for r in pending {
            if let sb = r.sizeBytes, sb > 0 {
                estimatedTotal += sb
            } else {
                estimatedTotal += 7_500_000 // 7.5 MB average
            }
        }
        let required = estimatedTotal

        let cacheDir = LibraryPathResolver.shared.mediaCacheBaseURL
        var available: Int64 = 0
        if let values = try? cacheDir.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey]) {
            available = values.volumeAvailableCapacityForImportantUsage ?? Int64(values.volumeAvailableCapacity ?? 0)
        }

        let sufficient = available >= required
        return (sufficient, required, available)
    }

    /// Returns whether any reel in the batch is not yet cached locally
    public func hasUndownloadedItems(reels: [ReelItem], weekID: String) -> Bool {
        guard !reels.isEmpty else { return false }
        return reels.contains { !LibraryPathResolver.shared.isLocalFileAvailable(for: weekID, reelID: $0.id) }
    }

    /// Calculates total bytes currently occupied by older weeks' directories (excluding Bookmarks and active incoming week) (AUDIT-021)
    public func calculateReclaimableRolloverBytes(incomingWeekID: String, pathResolver: LibraryPathResolver = .shared) -> Int64 {
        let fm = FileManager.default
        let safeIncoming = LibraryPathResolver.sanitizeComponent(incomingWeekID)
        let cacheBase = pathResolver.mediaCacheBaseURL
        var reclaimable: Int64 = 0

        if let subdirs = try? fm.contentsOfDirectory(at: cacheBase, includingPropertiesForKeys: [.isDirectoryKey]) {
            for dir in subdirs {
                let name = dir.lastPathComponent
                if name == safeIncoming || name == LibraryPathResolver.bookmarksFolderName || name == LibraryPathResolver.resumeDataFolderName {
                    continue
                }
                var isDir: ObjCBool = false
                if fm.fileExists(atPath: dir.path, isDirectory: &isDir), isDir.boolValue {
                    if let files = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.fileSizeKey]) {
                        for fileURL in files {
                            if let size = (try? fileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize) {
                                reclaimable += Int64(size)
                            }
                        }
                    }
                }
            }
        }
        return reclaimable
    }

    // MARK: - Queue Management

    /// Pure helper to measure manifest age against the 48-hour Meta signed URL expiry window (AUDIT-070)
    public static func manifestAgeHours(_ m: DigestManifest) -> Double? {
        guard let g = m.generatedAt else { return nil }
        return Date().timeIntervalSince(g) / 3600
    }

    /// Pure helper to measure manifest age against the 48-hour Meta signed URL expiry window (AUDIT-070)
    public static func manifestAgeHours(generatedAt: Date?) -> Double? {
        guard let g = generatedAt else { return nil }
        return Date().timeIntervalSince(g) / 3600
    }

    public func startDownloadAll(reels: [ReelItem], weekID: String, excludeWatchedIDs: Set<String> = [], generatedAt: Date? = nil) {
        // AUDIT-020: Guard against duplicate taps during .preparing, .downloading, and .paused
        switch self.state {
        case .preparing, .downloading, .paused:
            return
        default:
            break
        }

        // AUDIT-070: Proactive freshness gate: reject starting ~3 GB bulk download if manifest is >48h old
        if let age = Self.manifestAgeHours(generatedAt: generatedAt), age > 48 {
            self.state = .failed("Week links likely expired (\(Int(age))h old) — please refresh or re-sync from workstation.")
            return
        }

        let batch = excludeWatchedIDs.isEmpty ? reels : reels.filter { !excludeWatchedIDs.contains($0.id) }
        guard !batch.isEmpty else { return }
        self.state = .preparing

        Task { @MainActor [weak self] in
            guard let self = self else { return }
            self.cancelAll()
            self.state = .preparing
            AVPlayerPool.shared.pause()

            // Pre-purge storage check (AUDIT-033): Ensure available space + reclaimable bytes can fit the batch.
            let preCheck = self.preflightStorage(reels: batch, weekID: weekID)
            let reclaimable = self.calculateReclaimableRolloverBytes(incomingWeekID: weekID)
            if (preCheck.availableBytes + reclaimable) < preCheck.requiredBytes {
                let reqMB = preCheck.requiredBytes / 1_000_000
                let availMB = (preCheck.availableBytes + reclaimable) / 1_000_000
                self.state = .failed("Insufficient free space (\(availMB) MB available, \(reqMB) MB required).")
                return
            }

            // 1. Single-transaction rollover: wipe older week cache before starting new week
            let incomingIDs = Set(batch.map { $0.id })
            await MediaCacheManager.shared.executeRolloverPurge(forNewWeek: weekID, protectedReelIDs: incomingIDs)

            // 2. Preflight storage after purge
            let (sufficient, required, available) = self.preflightStorage(reels: batch, weekID: weekID)
            guard sufficient else {
                let reqMB = required / 1_000_000
                let availMB = available / 1_000_000
                self.state = .failed("Insufficient free space (\(availMB) MB available, \(reqMB) MB required).")
                return
            }

            self.currentWeekID = weekID
            self.watchdogResumeTask?.cancel()
            self.watchdogResumeTask = nil
            self.isSuspended = false
            self.suspensionSource = .none   // a prior user pause must not stick to the new batch
            // AUDIT-067: Batch completeness checks verify week cache file existence specifically
            let pending = batch.filter {
                let weekURL = LibraryPathResolver.shared.localFileURL(for: weekID, reelID: $0.id)
                return !FileManager.default.fileExists(atPath: weekURL.path)
            }

            guard !pending.isEmpty else {
                self.state = .completed
                self.overallProgress = 1.0
                return
            }

            self.queue = pending.map { DownloadItem(id: $0.id, reel: $0, weekID: weekID) }
            self.totalInBatch = self.queue.count
            self.completedInBatch = 0
            self.failedInBatch = 0
            self.retryCounts.removeAll()
            self.state = .downloading(completed: 0, total: self.totalInBatch, currentReelID: nil)
            self.overallProgress = 0.0

            UIApplication.shared.isIdleTimerDisabled = true
            // Rec #4: thumbnails ride along best-effort (~20–50 KB each) so the
            // grid/bookmarks browse offline too. Never fails the video batch.
            let thumbReels = batch
            Task { @MainActor [weak self] in
                await self?.prefetchThumbnails(reels: thumbReels, weekID: weekID)
            }
            self.drainQueue()
        }
    }

    /// Best-effort thumbnail prefetch into the week's MediaCache dir.
    /// One small file per reel, same LivePinSet/rollover treatment as video.
    private func prefetchThumbnails(reels: [ReelItem], weekID: String) async {
        let resolver = LibraryPathResolver.shared
        try? resolver.ensureDirectoriesExist(for: weekID)
        for reel in reels {
            guard let remote = reel.thumbnailUrl else { continue }
            let dest = resolver.thumbnailFileURL(for: weekID, reelID: reel.id)
            if FileManager.default.fileExists(atPath: dest.path) { continue }
            do {
                let (tmpURL, resp) = try await URLSession.shared.download(from: remote)
                guard resp.isHTTPSuccess else { try? FileManager.default.removeItem(at: tmpURL); continue }
                try? FileManager.default.createDirectory(
                    at: dest.deletingLastPathComponent(),
                    withIntermediateDirectories: true)
                if FileManager.default.fileExists(atPath: dest.path) {
                    try? FileManager.default.removeItem(at: tmpURL)
                    continue
                }
                try FileManager.default.moveItem(at: tmpURL, to: dest)
                try? resolver.applyProtectionAndBackupExclusion(to: dest)
            } catch {
                continue
            }
        }
    }

    public func cancelAll() {
        watchdogResumeTask?.cancel()
        watchdogResumeTask = nil
        isSuspended = false
        suspensionSource = .none        // otherwise the sticky-user branch misfires forever
        for (_, entry) in inFlightTasks {
            entry.task.cancel()
        }
        inFlightTasks.removeAll()
        queue.removeAll()
        totalInBatch = 0
        completedInBatch = 0
        failedInBatch = 0
        hasRefreshedManifestForExpiredURLs = false
        retryCounts.removeAll()
        currentWeekID = ""
        overallProgress = 0.0
        state = .idle
        UIApplication.shared.isIdleTimerDisabled = false
    }

    /// A finished batch must not strand the (singleton) sheet on "Done" forever.
    public func resetIfFinished() {
        switch state {
        case .completed, .completedWithFailures:
            if !hasActiveDownloads {
                state = .idle
                overallProgress = 0
            }
        default:
            break
        }
    }

    /// Targeted retry for failed batch items without re-downloading already cached files.
    public func retryFailedItems(reels: [ReelItem], weekID: String) {
        let failedReels = reels.filter { reel in
            !LibraryPathResolver.shared.isLocalFileAvailable(for: weekID, reelID: reel.id)
        }
        startDownloadAll(reels: failedReels, weekID: weekID)
    }

    private enum SuspensionSource { case none, user, watchdog, background }

    private var suspensionSource: SuspensionSource = .none

    public func suspendQueue() {
        suspendQueue(source: .user)
    }

    private func suspendQueue(source: SuspensionSource) {
        // B15: a watchdog armed earlier must not fire mid-purge (or mid-user-
        // pause) and resume behind our back — the purge invariant depends on
        // suspension holding until explicitly resumed.
        // A user pause is sticky: later system suspensions must not
        // downgrade it, or the watchdog/foreground would resume behind the
        // user's back.
        if suspensionSource == .user && source != .user {
            watchdogResumeTask?.cancel()
            watchdogResumeTask = nil
            isSuspended = true
            return
        }
        watchdogResumeTask?.cancel()
        watchdogResumeTask = nil
        suspensionSource = source
        isSuspended = true
        for (_, entry) in inFlightTasks {
            entry.task.suspend()
        }
        // IOS-P2-11: surface the paused state so the sheet's .paused branch
        // is reachable; drainQueue is gated on isSuspended so no extra work starts.
        if case .downloading = state {
            state = .paused
        }
        UIApplication.shared.isIdleTimerDisabled = false
    }

    public func resumeQueue() {
        suspensionSource = .none
        isSuspended = false
        for (_, entry) in inFlightTasks {
            entry.task.resume()
        }
        if hasActiveDownloads {
            UIApplication.shared.isIdleTimerDisabled = true
        }
        // Restore the downloading state before draining so the gate passes.
        if case .paused = state {
            state = .downloading(completed: completedInBatch, total: totalInBatch, currentReelID: inFlightTasks.values.first?.item.reel.id)
        }
        drainQueue()
    }

    // MARK: - Purge-linked suspension (must never lift an explicit user pause)
    private var suspendedForPurge = false

    public func suspendQueueForPurge() {
        guard suspensionSource != .user else { suspendedForPurge = false; return }
        suspendedForPurge = true
        suspendQueue(source: .background)
    }

    public func resumeQueueAfterPurge() {
        guard suspendedForPurge else { return }
        suspendedForPurge = false
        resumeQueue()
    }

    /// Suspends downloads for 10s during local-file stall watchdog precedence
    public func suspendForWatchdog(durationSeconds: Double = 10.0) {
        suspendQueue(source: .watchdog)
        watchdogResumeTask?.cancel()
        watchdogResumeTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(durationSeconds * 1_000_000_000))
            guard !Task.isCancelled else { return }
            guard let self = self, self.isSuspended else { return }
            // Never resume behind an explicit user pause.
            guard self.suspensionSource == .watchdog else { return }
            // Only resume if still in foreground
            if UIApplication.shared.applicationState != .background {
                self.resumeQueue()
            }
        }
    }

    // MARK: - Queue Drain & Concurrency Preemption

    private func drainQueue() {
        guard !isSuspended else { return }
        guard state != .idle else { return }

        let targetConcurrency = computeTargetConcurrency()

        // Immediate preemption: if running tasks exceed target concurrency (e.g. video started playing),
        // suspend excess tasks and return them to the queue
        if inFlightTasks.count > targetConcurrency {
            let excessCount = inFlightTasks.count - targetConcurrency
            let tasksToSuspend = Array(inFlightTasks.keys.prefix(excessCount))
            for taskID in tasksToSuspend {
                if let entry = inFlightTasks.removeValue(forKey: taskID) {
                    // IOS-P0-2: cancel(handler:) runs on the URLSession
                    // delegate queue, not MainActor. Hop before touching
                    // actor state, and capture self weakly.
                    entry.task.cancel { [weak self] resumeData in
                        guard let data = resumeData else { return }
                        let reelID = entry.item.id
                        let weekID = entry.item.weekID
                        Task { @MainActor [weak self] in
                            self?.persistResumeData(data, for: reelID, weekID: weekID)
                        }
                    }
                    queue.insert(entry.item, at: 0)
                }
            }
        }

        while inFlightTasks.count < targetConcurrency && !queue.isEmpty {
            let item = queue.removeFirst()
            startItemDownload(item)
        }

        let finishedCount = completedInBatch + failedInBatch
        if inFlightTasks.isEmpty && queue.isEmpty {
            guard totalInBatch > 0 else {
                state = .idle
                overallProgress = 0.0
                return
            }
            if failedInBatch == totalInBatch {
                state = .failed("All \(totalInBatch) downloads failed in this batch.")
            } else if completedInBatch > 0 && failedInBatch > 0 {
                state = .completedWithFailures(ready: completedInBatch, failed: failedInBatch)
            } else if failedInBatch > 0 {
                state = .failed("\(failedInBatch) of \(totalInBatch) downloads failed — tap Retry.")
            } else {
                state = .completed
                overallProgress = 1.0
            }
            UIApplication.shared.isIdleTimerDisabled = false
        } else {
            let currentReel = inFlightTasks.values.first?.item.reel.id
            state = .downloading(completed: completedInBatch, total: totalInBatch, currentReelID: currentReel)
            if totalInBatch > 0 {
                overallProgress = Double(finishedCount) / Double(totalInBatch)
            }
        }
    }

    private func startItemDownload(_ item: DownloadItem) {
        let task: URLSessionDownloadTask
        if let resumeData = loadPersistedResumeData(for: item.id, weekID: item.weekID) {
            task = urlSession.downloadTask(withResumeData: resumeData)
            // Keep the .dat until promotion succeeds (IOS-P1-8): a crash
            // mid-download must be resumable from the persisted bytes.
        } else {
            task = urlSession.downloadTask(with: item.reel.videoUrl)
        }

        inFlightTasks[task.taskIdentifier] = (item: item, task: task)
        task.resume()
    }

    private func computeTargetConcurrency() -> Int {
        let thermal = ProcessInfo.processInfo.thermalState
        if thermal == .serious || thermal == .critical {
            return 1
        }
        return AVPlayerPool.shared.isPlaying ? 1 : 3
    }

    // MARK: - ResumeData Persistence to Disk

    private func persistResumeData(_ data: Data, for reelID: String, weekID: String) {
        let fileURL = LibraryPathResolver.shared.resumeDataFileURL(forWeekID: weekID, reelID: reelID)
        try? LibraryPathResolver.shared.ensureDirectoryExists(at: LibraryPathResolver.shared.resumeDataDirectoryURL)
        try? data.write(to: fileURL, options: .atomic)
    }

    private func loadPersistedResumeData(for reelID: String, weekID: String) -> Data? {
        let fileURL = LibraryPathResolver.shared.resumeDataFileURL(forWeekID: weekID, reelID: reelID)
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        return try? Data(contentsOf: fileURL)
    }

    private func removePersistedResumeData(for reelID: String, weekID: String) {
        let fileURL = LibraryPathResolver.shared.resumeDataFileURL(forWeekID: weekID, reelID: reelID)
        try? FileManager.default.removeItem(at: fileURL)
    }

    // MARK: - URLSessionDownloadDelegate

    nonisolated public func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        if let resp = downloadTask.response, !resp.isHTTPSuccess {
            let status = (resp as? HTTPURLResponse)?.statusCode ?? -1
            try? FileManager.default.removeItem(at: location)
            Task { @MainActor in
                guard let entry = self.inFlightTasks.removeValue(forKey: downloadTask.taskIdentifier) else { return }
                let item = entry.item
                self.removePersistedResumeData(for: item.id, weekID: item.weekID)

                if status == 403 || status == 410 {
                    await self.handleExpiredURLResponse(failingItem: item)
                    return
                }

                let retries = (self.retryCounts[item.id] ?? 0) + 1
                self.retryCounts[item.id] = retries
                if status >= 500 && retries <= self.maxRetriesPerItem { self.queue.append(item) } else { self.failedInBatch += 1 }
                self.drainQueue()
            }
            return
        }
        // Synchronously move the file to a sandbox temporary staging path BEFORE this delegate method returns!
        // Otherwise, the iOS system automatically unlinks/deletes the file at `location` the moment the method returns.
        let tempStagingURL = FileManager.default.temporaryDirectory.appendingPathComponent("staging_\(UUID().uuidString).tmp")
        do {
            try FileManager.default.moveItem(at: location, to: tempStagingURL)
        } catch {
            // Staging failed: the entry must still be retired with retry accounting,
            // otherwise the slot leaks and the queue stalls in .downloading forever.
            Task { @MainActor in
                guard let entry = self.inFlightTasks.removeValue(forKey: downloadTask.taskIdentifier) else { return }
                let item = entry.item
                let retries = (self.retryCounts[item.id] ?? 0) + 1
                self.retryCounts[item.id] = retries
                if retries <= self.maxRetriesPerItem {
                    self.queue.append(item)
                } else {
                    self.failedInBatch += 1
                }
                self.drainQueue()
            }
            return
        }

        Task { @MainActor in
            defer {
                try? FileManager.default.removeItem(at: tempStagingURL)
            }
            guard let entry = self.inFlightTasks.removeValue(forKey: downloadTask.taskIdentifier) else { return }
            let item = entry.item

            do {
                let partURL = LibraryPathResolver.shared.localPartFileURL(for: item.weekID, reelID: item.reel.id)
                try LibraryPathResolver.shared.ensureDirectoriesExist(for: item.weekID)
                if FileManager.default.fileExists(atPath: partURL.path) {
                    try? FileManager.default.removeItem(at: partURL)
                }
                try FileManager.default.moveItem(at: tempStagingURL, to: partURL)

                try await MediaCacheManager.shared.promotePartFile(
                    from: partURL,
                    weekID: item.weekID,
                    reelID: item.reel.id,
                    expectedSizeBytes: item.reel.sizeBytes
                )

                // IOS-P1-8: delete resume data only after the atomic
                // promotion succeeds. A crash between the .part move and
                // promotion must keep resume bytes for a cheap retry.
                self.removePersistedResumeData(for: item.id, weekID: item.weekID)

                self.completedInBatch += 1
            } catch {
                let retries = (self.retryCounts[item.id] ?? 0) + 1
                self.retryCounts[item.id] = retries
                if retries <= self.maxRetriesPerItem {
                    self.queue.append(item)
                } else {
                    self.failedInBatch += 1
                }
            }

            self.drainQueue()
        }
    }

    nonisolated public func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        guard let error = error else { return }
        Task { @MainActor in
            guard let entry = self.inFlightTasks.removeValue(forKey: task.taskIdentifier) else { return }
            let item = entry.item

            if let http = task.response as? HTTPURLResponse, (http.statusCode == 403 || http.statusCode == 410) {
                self.removePersistedResumeData(for: item.id, weekID: item.weekID)
                await self.handleExpiredURLResponse(failingItem: item)
                return
            }

            let isCancelled = (error as NSError).code == NSURLErrorCancelled
            if let resumeData = (error as NSError).userInfo[NSURLSessionDownloadTaskResumeData] as? Data {
                self.persistResumeData(resumeData, for: item.id, weekID: item.weekID)
            } else if !isCancelled {
                // No fresh resume data => any persisted blob is unusable (tmp purged,
                // object changed). Drop it so the retry starts clean.
                self.removePersistedResumeData(for: item.id, weekID: item.weekID)
            }

            if !isCancelled {
                let retries = (self.retryCounts[item.id] ?? 0) + 1
                self.retryCounts[item.id] = retries
                if retries <= self.maxRetriesPerItem {
                    self.queue.append(item)
                } else {
                    self.failedInBatch += 1
                }
            }

            self.drainQueue()
        }
    }

    private func handleExpiredURLResponse(failingItem: DownloadItem) async {
        // AUDIT-022: Prevent concurrent refresh race across multiple 403/410 tasks
        guard !isRefreshingForExpiry else {
            self.failedInBatch += 1
            return
        }

        if hasRefreshedManifestForExpiredURLs {
            self.failedInBatch += 1
            cancelAllRemainingAndFail(message: "Download links expired — please refresh or sync")
            return
        }

        isRefreshingForExpiry = true
        defer { isRefreshingForExpiry = false }

        hasRefreshedManifestForExpiredURLs = true
        let priorSuspensionSource = self.suspensionSource
        suspendQueue(source: .background)

        do {
            let freshManifest = try await DigestDataService.shared.fetchManifest(bypassCache: true)
            // Re-check state before mutating queue
            if case .failed = self.state { return }

            var updatedCount = 0
            let freshMap = Dictionary(uniqueKeysWithValues: freshManifest.items.map { ($0.id, $0) })

            // Update pending items in queue
            self.queue = self.queue.map { qItem in
                if let fresh = freshMap[qItem.id], fresh.videoUrl != qItem.reel.videoUrl {
                    updatedCount += 1
                    return DownloadItem(id: qItem.id, reel: fresh, weekID: qItem.weekID)
                }
                return qItem
            }

            // Also check and re-enqueue failing item if updated
            if let fresh = freshMap[failingItem.id], fresh.videoUrl != failingItem.reel.videoUrl {
                updatedCount += 1
                self.queue.append(DownloadItem(id: failingItem.id, reel: fresh, weekID: failingItem.weekID))
            } else {
                self.failedInBatch += 1
            }

            if updatedCount > 0 {
                // AUDIT-022: Preserve sticky user pause if user explicitly paused earlier
                if priorSuspensionSource == .user {
                    self.suspensionSource = .user
                } else {
                    resumeQueue()
                }
            } else {
                cancelAllRemainingAndFail(message: "Download links expired — please refresh or sync")
            }
        } catch {
            self.failedInBatch += 1
            cancelAllRemainingAndFail(message: "Download links expired — please refresh or sync")
        }
    }

    private func cancelAllRemainingAndFail(message: String) {
        for (_, entry) in inFlightTasks {
            entry.task.cancel()
        }
        inFlightTasks.removeAll()
        queue.removeAll()
        state = .failed(message)
        UIApplication.shared.isIdleTimerDisabled = false
    }

    // MARK: - Observers

    private func setupAdaptiveConcurrencyObservers() {
        AVPlayerPool.shared.$isPlaying
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self = self else { return }
                    self.activeConcurrency = self.computeTargetConcurrency()
                    self.drainQueue()
                }
            }
            .store(in: &cancellables)

        // IOS-P2-10: store observer tokens so deinit can remove them;
        // discarded tokens leak and double-fire drainQueue after re-init.
        notificationTokens.append(
            NotificationCenter.default.addObserver(
                forName: ProcessInfo.thermalStateDidChangeNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self = self else { return }
                    self.activeConcurrency = self.computeTargetConcurrency()
                    self.drainQueue()
                }
            }
        )
    }

    private func setupLifecycleObservers() {
        notificationTokens.append(
            NotificationCenter.default.addObserver(
                forName: UIApplication.didEnterBackgroundNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.suspendQueue(source: .background)
                }
            }
        )

        notificationTokens.append(
            NotificationCenter.default.addObserver(
                forName: UIApplication.willEnterForegroundNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self = self else { return }
                    // Never resume behind an explicit user pause.
                    guard self.suspensionSource != .user else { return }
                    if self.hasActiveDownloads {
                        self.resumeQueue()
                    }
                }
            }
        )
    }

    deinit {
        for token in notificationTokens {
            NotificationCenter.default.removeObserver(token)
        }
    }
}
