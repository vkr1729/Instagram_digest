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

    public enum CoordinatorState: Equatable {
        case idle
        case downloading(completed: Int, total: Int, currentReelID: String?)
        case completed
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
    private var failedInBatch: Int = 0
    private var currentWeekID: String = ""

    private var urlSession: URLSession!
    private var cancellables = Set<AnyCancellable>()
    private var watchdogResumeTask: Task<Void, Never>?

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

    /// Checks if device has sufficient storage: ManifestTotalBytes (or N * 20MB) + 1.0 GB Headroom
    public func preflightStorage(reels: [ReelItem]) -> (isSufficient: Bool, requiredBytes: Int64, availableBytes: Int64) {
        var estimatedTotal: Int64 = 0
        for r in reels {
            if let sb = r.sizeBytes, sb > 0 {
                estimatedTotal += sb
            } else {
                estimatedTotal += 20_000_000 // 20 MB fallback
            }
        }
        let headroom: Int64 = 1_000_000_000 // 1.0 GB headroom
        let required = estimatedTotal + headroom

        let cacheDir = LibraryPathResolver.shared.mediaCacheBaseURL
        var available: Int64 = 0
        if let values = try? cacheDir.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey]) {
            available = values.volumeAvailableCapacityForImportantUsage ?? Int64(values.volumeAvailableCapacity ?? 0)
        }

        let sufficient = available >= required
        return (sufficient, required, available)
    }

    // MARK: - Queue Management

    public func startDownloadAll(reels: [ReelItem], weekID: String) {
        let (sufficient, required, available) = preflightStorage(reels: reels)
        guard sufficient else {
            let reqMB = required / 1_000_000
            let availMB = available / 1_000_000
            self.state = .failed("Insufficient free space (\(availMB) MB available, \(reqMB) MB required).")
            return
        }

        self.currentWeekID = weekID
        let pending = reels.filter {
            !LibraryPathResolver.shared.isLocalFileAvailable(for: weekID, reelID: $0.id)
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
        self.watchdogResumeTask?.cancel()
        self.watchdogResumeTask = nil
        self.isSuspended = false
        self.state = .downloading(completed: 0, total: totalInBatch, currentReelID: nil)
        self.overallProgress = 0.0

        UIApplication.shared.isIdleTimerDisabled = true
        drainQueue()
    }

    public func cancelAll() {
        watchdogResumeTask?.cancel()
        watchdogResumeTask = nil
        isSuspended = false
        for (_, entry) in inFlightTasks {
            entry.task.cancel()
        }
        inFlightTasks.removeAll()
        queue.removeAll()
        state = .idle
        UIApplication.shared.isIdleTimerDisabled = false
    }

    public func suspendQueue() {
        isSuspended = true
        for (_, entry) in inFlightTasks {
            entry.task.suspend()
        }
        UIApplication.shared.isIdleTimerDisabled = false
    }

    public func resumeQueue() {
        isSuspended = false
        for (_, entry) in inFlightTasks {
            entry.task.resume()
        }
        if hasActiveDownloads {
            UIApplication.shared.isIdleTimerDisabled = true
        }
        drainQueue()
    }

    /// Suspends downloads for 10s during local-file stall watchdog precedence
    public func suspendForWatchdog(durationSeconds: Double = 10.0) {
        suspendQueue()
        watchdogResumeTask?.cancel()
        watchdogResumeTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(durationSeconds * 1_000_000_000))
            guard !Task.isCancelled else { return }
            guard let self = self, self.isSuspended else { return }
            // Only resume if still in foreground
            if UIApplication.shared.applicationState != .background {
                self.resumeQueue()
            }
        }
    }

    // MARK: - Queue Drain & Concurrency Preemption

    private func drainQueue() {
        guard !isSuspended else { return }

        let targetConcurrency = computeTargetConcurrency()

        // Immediate preemption: if running tasks exceed target concurrency (e.g. video started playing),
        // suspend excess tasks and return them to the queue
        if inFlightTasks.count > targetConcurrency {
            let excessCount = inFlightTasks.count - targetConcurrency
            let tasksToSuspend = Array(inFlightTasks.keys.prefix(excessCount))
            for taskID in tasksToSuspend {
                if let entry = inFlightTasks.removeValue(forKey: taskID) {
                    entry.task.cancel { resumeData in
                        if let data = resumeData {
                            self.persistResumeData(data, for: entry.item.id)
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
            if failedInBatch > 0 && completedInBatch == 0 {
                state = .failed("All downloads failed in this batch.")
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
        if let resumeData = loadPersistedResumeData(for: item.id) {
            task = urlSession.downloadTask(withResumeData: resumeData)
            removePersistedResumeData(for: item.id)
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

    private func persistResumeData(_ data: Data, for reelID: String) {
        let fileURL = LibraryPathResolver.shared.resumeDataFileURL(for: reelID)
        try? LibraryPathResolver.shared.ensureDirectoryExists(at: LibraryPathResolver.shared.resumeDataDirectoryURL)
        try? data.write(to: fileURL, options: .atomic)
    }

    private func loadPersistedResumeData(for reelID: String) -> Data? {
        let fileURL = LibraryPathResolver.shared.resumeDataFileURL(for: reelID)
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        return try? Data(contentsOf: fileURL)
    }

    private func removePersistedResumeData(for reelID: String) {
        let fileURL = LibraryPathResolver.shared.resumeDataFileURL(for: reelID)
        try? FileManager.default.removeItem(at: fileURL)
    }

    // MARK: - URLSessionDownloadDelegate

    nonisolated public func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        Task { @MainActor in
            guard let entry = self.inFlightTasks.removeValue(forKey: downloadTask.taskIdentifier) else { return }
            let item = entry.item
            self.removePersistedResumeData(for: item.id)

            do {
                let partURL = LibraryPathResolver.shared.localPartFileURL(for: item.weekID, reelID: item.reel.id)
                try LibraryPathResolver.shared.ensureDirectoriesExist(for: item.weekID)
                if FileManager.default.fileExists(atPath: partURL.path) {
                    try? FileManager.default.removeItem(at: partURL)
                }
                try FileManager.default.moveItem(at: location, to: partURL)

                try await MediaCacheManager.shared.promotePartFile(
                    from: partURL,
                    weekID: item.weekID,
                    reelID: item.reel.id,
                    expectedSizeBytes: item.reel.sizeBytes
                )

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

            if let resumeData = (error as NSError).userInfo[NSURLSessionDownloadTaskResumeData] as? Data {
                self.persistResumeData(resumeData, for: item.id)
            }

            let isCancelled = (error as NSError).code == NSURLErrorCancelled
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

    // MARK: - Observers

    private func setupAdaptiveConcurrencyObservers() {
        AVPlayerPool.shared.$isPlaying
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in
                guard let self = self else { return }
                self.activeConcurrency = self.computeTargetConcurrency()
                self.drainQueue()
            }
            .store(in: &cancellables)

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
    }

    private func setupLifecycleObservers() {
        NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.suspendQueue()
            }
        }

        NotificationCenter.default.addObserver(
            forName: UIApplication.willEnterForegroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                if self.hasActiveDownloads {
                    self.resumeQueue()
                }
            }
        }
    }
}
