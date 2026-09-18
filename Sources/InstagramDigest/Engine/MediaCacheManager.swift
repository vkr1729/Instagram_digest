import Foundation
import SwiftData

/// Serialized async actor managing local disk cache, LivePinSet protection,
/// 1.5 GB bookmark cap with LRU eviction, and .part download atomic promotion.
public actor MediaCacheManager {
    // MediaCacheManager actor synchronizes cache ledger, LivePinSet, and bookmark operations.
    // SwiftData operations instantiate an actor-local ModelContext from the shared ModelContainer.
    public static let shared = MediaCacheManager()

    /// 1.5 GB cap for offline bookmarked videos
    public static let maxBookmarkStorageBytes: Int64 = 1_500_000_000

    private let pathResolver: LibraryPathResolver
    private var modelContainer: ModelContainer?

    /// In-memory ledger reflecting cached bookmark MP4 bytes only
    public private(set) var totalBookmarkBytes: Int64 = 0

    /// Bound reel IDs currently active or preloaded in the video pool
    private var activeVideoPoolReelIDs: Set<String> = []
    private var latestLivePinSetGeneration: UInt64 = 0

    /// Flag indicating if a purge operation is in progress (for UI disabling)
    public private(set) var isPurging: Bool = false

    /// Optional hook to suspend/resume downloads during storage purge
    private var downloadSuspensionHandler: (@Sendable (Bool) async -> Void)?

    public init(pathResolver: LibraryPathResolver = .shared) {
        self.pathResolver = pathResolver
    }

    /// Attaches the ModelContainer for SwiftData operations
    public func setModelContainer(_ container: ModelContainer) {
        self.modelContainer = container
    }

    /// Configures download suspension callback
    public func setDownloadSuspensionHandler(_ handler: @escaping @Sendable (Bool) async -> Void) {
        self.downloadSuspensionHandler = handler
    }

    /// Updates active reel IDs bound to the AVPlayer pool (protected by LivePinSet) with generation fencing
    public func setActiveVideoPoolReelIDs(_ ids: Set<String>, generation: UInt64 = 0) {
        if generation > 0 {
            guard generation >= latestLivePinSetGeneration else { return }
            latestLivePinSetGeneration = generation
        }
        self.activeVideoPoolReelIDs = ids
    }

    // MARK: - Reconcile Ledger

    /// Reconciles in-memory totalBookmarkBytes ledger on app launch.
    /// Strictly verifies files in the isolated Bookmarks directory.
    /// If a previously cached bookmark file is missing, marks localStatus = .evicted.
    /// Never auto-promotes an evicted bookmark to cached from feed downloads.
    public func reconcileBookmarkStorageLedger() async {
        guard let container = modelContainer else { return }
        let context = ModelContext(container)

        do {
            let descriptor = FetchDescriptor<BookmarkItem>()
            let bookmarks = try context.fetch(descriptor)

            var computedBytes: Int64 = 0
            for bookmark in bookmarks {
                let fileURL = pathResolver.bookmarkFileURL(for: bookmark.reelID)
                if FileManager.default.fileExists(atPath: fileURL.path) {
                    if let size = Self.diskFileSize(atPath: fileURL.path), size > 0 {
                        // Only count and keep cached if it was intentionally cached
                        if bookmark.localStatus == .cached {
                            bookmark.sizeBytes = size
                            computedBytes += size
                        }
                    } else {
                        bookmark.localStatus = .evicted
                    }
                } else {
                    if bookmark.localStatus == .cached {
                        bookmark.localStatus = .evicted
                    }
                }
            }
            try context.save()
            self.totalBookmarkBytes = computedBytes
        } catch {
            // Reconcile failed gracefully
        }
    }

    // MARK: - LivePinSet Invariant & Purge

    /// Returns the live set of reel IDs that must NEVER be deleted from disk:
    /// LivePinSet = { reelID | BookmarkItem.localStatus == .cached && file exists } ∪ AVPlayerPool.activeReelIDs
    public func computeLivePinSet() async -> Set<String> {
        var pinSet = activeVideoPoolReelIDs

        guard let container = modelContainer else { return pinSet }
        let context = ModelContext(container)
        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.localStatusRaw == "cached" }
        )

        if let cachedBookmarks = try? context.fetch(descriptor) {
            for bm in cachedBookmarks {
                let url = pathResolver.bookmarkFileURL(for: bm.reelID)
                if FileManager.default.fileExists(atPath: url.path) {
                    pinSet.insert(bm.reelID)
                }
            }
        }
        return pinSet
    }

    /// Purges an older week's cache directory during weekly rollover, strictly protecting the LivePinSet.
    public func purgeOldWeekDirectory(oldWeekID: String) async {
        let livePins = await computeLivePinSet()
        let sanitizedPins = Set(livePins.map { LibraryPathResolver.sanitizeComponent($0) })

        let weekDir = pathResolver.weekDirectoryURL(for: oldWeekID)
        let fm = FileManager.default

        guard fm.fileExists(atPath: weekDir.path) else { return }

        guard let contents = try? fm.contentsOfDirectory(at: weekDir, includingPropertiesForKeys: nil) else { return }

        for fileURL in contents {
            // Robust filename parsing: strip .part and .mp4 suffixes only
            var baseName = fileURL.lastPathComponent
            if baseName.hasSuffix(".part") {
                baseName = (baseName as NSString).deletingPathExtension
            }
            if baseName.hasSuffix(".mp4") {
                baseName = (baseName as NSString).deletingPathExtension
            }

            // Never delete any file whose reel ID is in LivePinSet
            if !sanitizedPins.contains(baseName) {
                try? fm.removeItem(at: fileURL)
            }
        }

        // Try removing directory if empty
        if let remaining = try? fm.contentsOfDirectory(atPath: weekDir.path), remaining.isEmpty {
            try? fm.removeItem(at: weekDir)
        }

        // Also clean up stale ResumeData files not in LivePinSet
        let resumeDir = pathResolver.resumeDataDirectoryURL
        if let resumeContents = try? fm.contentsOfDirectory(at: resumeDir, includingPropertiesForKeys: nil) {
            for fileURL in resumeContents {
                var baseName = fileURL.lastPathComponent
                if baseName.hasSuffix(".dat") {
                    baseName = (baseName as NSString).deletingPathExtension
                }
                if !sanitizedPins.contains(baseName) {
                    try? fm.removeItem(at: fileURL)
                }
            }
        }
    }

    /// Serialized local file eviction for corrupted local feed files (called by failure ladder)
    public func evictLocalFeedFile(weekID: String, reelID: String) {
        // IOS-P1-1: never evict a reel the pool is actively playing or
        // preloading, and never evict when an isolated bookmark copy exists
        // (delete only the corrupt feed copy, keep the bookmark intact).
        guard !activeVideoPoolReelIDs.contains(reelID) else { return }
        let fileURL = pathResolver.localFileURL(for: weekID, reelID: reelID)
        if FileManager.default.fileExists(atPath: fileURL.path) {
            try? FileManager.default.removeItem(at: fileURL)
        }
    }

    // MARK: - LRU Admission & Eviction (1.5 GB Cap)

    /// Ensures space is available under the 1.5 GB cap before saving an offline bookmark.
    /// Pre-rejects files larger than the cap. Evicts oldest non-bound bookmarks by lastAccessedAt.
    public func ensureSpaceForBookmark(incomingBytes: Int64) async throws {
        // Pre-reject oversized admissions before touching existing files
        guard incomingBytes <= Self.maxBookmarkStorageBytes else {
            throw CacheError.insufficientStorage("Bookmark item exceeds maximum 1.5 GB capacity.")
        }
        // IOS-P2-4: never evict into a purge in flight.
        guard !isPurging else {
            throw CacheError.insufficientStorage("Bookmark storage purge in progress; retry shortly.")
        }

        if totalBookmarkBytes + incomingBytes <= Self.maxBookmarkStorageBytes {
            return
        }

        guard let container = modelContainer else { return }
        let context = ModelContext(container)

        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.localStatusRaw == "cached" },
            sortBy: [SortDescriptor(\.lastAccessedAt, order: .forward)]
        )

        let cachedBookmarks = (try? context.fetch(descriptor)) ?? []
        let fm = FileManager.default

        for candidate in cachedBookmarks {
            if totalBookmarkBytes + incomingBytes <= Self.maxBookmarkStorageBytes {
                break
            }

            // Never evict an actively playing or bound reel
            if activeVideoPoolReelIDs.contains(candidate.reelID) {
                continue
            }

            let fileURL = pathResolver.bookmarkFileURL(for: candidate.reelID)
            var sizeToDeduct: Int64 = candidate.sizeBytes

            if fm.fileExists(atPath: fileURL.path) {
                if let actualSize = Self.diskFileSize(atPath: fileURL.path) {
                    sizeToDeduct = actualSize
                }
                do {
                    try fm.removeItem(at: fileURL)
                    // Only decrement ledger on verified deletion
                    candidate.localStatus = .evicted
                    totalBookmarkBytes = max(0, totalBookmarkBytes - sizeToDeduct)
                } catch {
                    // Item removal failed, skip ledger decrement
                }
            } else {
                // IOS-P2-1: missing file must also clear the ledger share,
                // otherwise the cap stays inflated until next launch.
                totalBookmarkBytes = max(0, totalBookmarkBytes - candidate.sizeBytes)
                candidate.localStatus = .evicted
            }
        }

        try? context.save()

        if totalBookmarkBytes + incomingBytes > Self.maxBookmarkStorageBytes {
            throw CacheError.insufficientStorage("Cannot evict enough storage: active reels are currently bound.")
        }
    }

    /// Keep Offline action for a bookmark by ID: ensures space and copies media file to Bookmarks directory.
    /// Operates completely within actor context to avoid non-Sendable @Model crossing actor boundaries.
    public func keepBookmarkOffline(weekID: String, reelID: String, fallbackSizeBytes: Int64 = 0) async throws {
        // IOS-P2-4: a purge in flight may delete the destination mid-copy.
        guard !isPurging else {
            throw CacheError.insufficientStorage("Bookmark storage purge in progress; retry shortly.")
        }
        let fm = FileManager.default
        let bookmarkDestURL = pathResolver.bookmarkFileURL(for: reelID)

        guard let container = modelContainer else {
            throw CacheError.insufficientStorage("ModelContainer not configured")
        }
        let context = ModelContext(container)
        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.reelID == reelID }
        )
        guard let item = (try? context.fetch(descriptor))?.first else {
            throw CacheError.fileNotFound("Bookmark row not found in database.")
        }

        if fm.fileExists(atPath: bookmarkDestURL.path) {
            if item.localStatus != .cached {
                item.localStatus = .cached
                if let size = Self.diskFileSize(atPath: bookmarkDestURL.path) {
                    item.sizeBytes = size
                    do {
                        try context.save()
                        self.totalBookmarkBytes += size
                    } catch {
                        context.rollback()
                    }
                } else {
                    try? context.save()
                }
            }
            return
        }

        // Locate existing media file from feed, or fallback to remote download directly
        let feedURL = pathResolver.localFileURL(for: weekID, reelID: reelID)
        if fm.fileExists(atPath: feedURL.path) {
            let attrs = try fm.attributesOfItem(atPath: feedURL.path)
            let fileSize = Self.diskFileSize(atPath: feedURL.path) ?? (item.sizeBytes > 0 ? item.sizeBytes : fallbackSizeBytes)

            // Ensure space under 1.5 GB cap
            try await ensureSpaceForBookmark(incomingBytes: fileSize)

            // Perform disk copy off-actor to avoid blocking actor during large file I/O
            let dest = bookmarkDestURL
            let src = feedURL
            let resolver = self.pathResolver
            try await Task.detached {
                let fileMgr = FileManager.default
                try resolver.ensureDirectoryExists(at: resolver.bookmarksDirectoryURL)
                if fileMgr.fileExists(atPath: dest.path) {
                    try? fileMgr.removeItem(at: dest)
                }
                try fileMgr.copyItem(at: src, to: dest)
                try resolver.applyProtectionAndBackupExclusion(to: dest)
            }.value

            item.localStatus = .cached
            item.sizeBytes = fileSize
            item.lastAccessedAt = Date()
            do {
                try context.save()
                // IOS-P1-2: ledger moves only after the DB save succeeds;
                // a rollback must not leave phantom bytes counted.
                self.totalBookmarkBytes += fileSize
            } catch {
                context.rollback()
            }
        } else if let remoteURL = item.videoUrl {
            // Direct download from remote URL (e.g. Cloudflare R2 worker)
            let estimatedBytes = item.sizeBytes > 0 ? item.sizeBytes : (fallbackSizeBytes > 0 ? fallbackSizeBytes : 10_000_000)
            try await ensureSpaceForBookmark(incomingBytes: estimatedBytes)

            // IOS-P2-3: bounded session so a stalled worker cannot hang
            // keepBookmarkOffline forever (shared session has no timeouts).
            let boundedConfig = URLSessionConfiguration.ephemeral
            boundedConfig.timeoutIntervalForRequest = 15
            boundedConfig.timeoutIntervalForResource = 60
            let boundedSession = URLSession(configuration: boundedConfig)
            let (tempURL, _) = try await boundedSession.download(from: remoteURL)
            let dest = bookmarkDestURL
            let actualBytes: Int64 = Self.diskFileSize(atPath: tempURL.path) ?? estimatedBytes
            let resolver = self.pathResolver

            try await Task.detached {
                let fileMgr = FileManager.default
                try resolver.ensureDirectoryExists(at: resolver.bookmarksDirectoryURL)
                if fileMgr.fileExists(atPath: dest.path) {
                    try? fileMgr.removeItem(at: dest)
                }
                try fileMgr.moveItem(at: tempURL, to: dest)
                try resolver.applyProtectionAndBackupExclusion(to: dest)
            }.value

            item.localStatus = .cached
            item.sizeBytes = actualBytes
            item.lastAccessedAt = Date()
            do {
                try context.save()
                // IOS-P1-2: ledger moves only after the DB save succeeds.
                self.totalBookmarkBytes += actualBytes
            } catch {
                context.rollback()
            }
        } else {
            throw CacheError.fileNotFound("No local or remote media available to store offline.")
        }
    }

    /// Synchronizes remote bookmark manifests into SwiftData without downgrading localStatus
    public func syncRemoteBookmarks(dtos: [BookmarkRemoteDTO]) async {
        guard let container = modelContainer else { return }
        let context = ModelContext(container)
        let fm = FileManager.default

        do {
            let descriptor = FetchDescriptor<BookmarkItem>()
            let existingList = try context.fetch(descriptor)
            // Duplicate-safe map: pre-existing duplicate reelIDs collapse to
            // the first row; extras are deleted so the unique constraint holds
            // on disk instead of persisting forever (IOS-P2-12).
            var existingMap: [String: BookmarkItem] = [:]
            existingMap.reserveCapacity(existingList.count)
            for item in existingList {
                if existingMap[item.reelID] == nil {
                    existingMap[item.reelID] = item
                } else {
                    context.delete(item)
                }
            }

            for dto in dtos {
                let fileURL = pathResolver.bookmarkFileURL(for: dto.id)
                let fileExists = fm.fileExists(atPath: fileURL.path)

                if let existing = existingMap[dto.id] {
                    // Update metadata only, never downgrade cached to evicted
                    existing.creatorHandle = dto.creatorHandle
                    existing.caption = dto.caption
                    existing.videoUrlString = dto.videoUrl.absoluteString
                    existing.thumbnailUrlString = dto.thumbnailUrl?.absoluteString
                    if let sb = dto.sizeBytes, sb > 0 {
                        existing.sizeBytes = sb
                    }
                    if fileExists {
                        existing.localStatus = .cached
                    }
                } else {
                    let newItem = BookmarkItem(
                        reelID: dto.id,
                        weekID: "bookmarks",
                        creatorHandle: dto.creatorHandle,
                        caption: dto.caption,
                        rank: 1,
                        videoUrl: dto.videoUrl,
                        thumbnailUrl: dto.thumbnailUrl,
                        localStatus: fileExists ? .cached : .evicted,
                        sizeBytes: dto.sizeBytes ?? 0
                    )
                    context.insert(newItem)
                    existingMap[dto.id] = newItem
                }
            }

            try context.save()
            await reconcileBookmarkStorageLedger()
        } catch {
            // Bookmark sync failure gracefully handled
        }
    }

    /// Serialized deletion of a bookmark's offline file (avoids orphaned files on unbookmark or delete)
    public func deleteBookmarkFile(reelID: String) {
        let bookmarkDestURL = pathResolver.bookmarkFileURL(for: reelID)
        let fm = FileManager.default
        if fm.fileExists(atPath: bookmarkDestURL.path) {
            if let size = Self.diskFileSize(atPath: bookmarkDestURL.path) {
                self.totalBookmarkBytes = max(0, self.totalBookmarkBytes - size)
            }
            try? fm.removeItem(at: bookmarkDestURL)
        }
        // IOS-P1-5: sync the DB row so the UI stops showing "Saved offline"
        // and computeLivePinSet stops pinning a deleted file.
        if let container = modelContainer {
            let context = ModelContext(container)
            let descriptor = FetchDescriptor<BookmarkItem>(
                predicate: #Predicate { $0.reelID == reelID }
            )
            if let item = (try? context.fetch(descriptor))?.first,
               item.localStatus != .evicted {
                item.localStatus = .evicted
                item.sizeBytes = 0
                do {
                    try context.save()
                } catch {
                    context.rollback()
                }
            }
        }
    }

    // MARK: - Free Local Storage Action

    /// Executes the 5-step purge for the "Free Local Storage" button:
    /// 1. Sets isPurging = true and suspends the download queue.
    /// 2. Filters purge candidates against activeReelIDs (preserves playing files).
    /// 3. Deletes completed cached bookmark MP4s from Bookmarks directory.
    /// 4. Updates evicted rows in SwiftData.
    /// 5. Resets totalBookmarkBytes ledger, resumes download queue, and resets isPurging.
    public func freeBookmarkStorage() async {
        isPurging = true

        // 1. Suspend downloads
        await downloadSuspensionHandler?(true)
        // IOS-P1-6: every exit path resumes downloads and clears the flag.
        defer {
            self.isPurging = false
        }

        guard let container = modelContainer else {
            await downloadSuspensionHandler?(false)
            return
        }

        let context = ModelContext(container)
        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.localStatusRaw == "cached" }
        )

        guard let bookmarks = try? context.fetch(descriptor) else {
            await downloadSuspensionHandler?(false)
            return
        }

        var remainingBytes: Int64 = 0
        let fm = FileManager.default

        for bookmark in bookmarks {
            // Never delete or flip an actively bound reel
            if activeVideoPoolReelIDs.contains(bookmark.reelID) {
                let fileURL = pathResolver.bookmarkFileURL(for: bookmark.reelID)
                if let size = Self.diskFileSize(atPath: fileURL.path) {
                    remainingBytes += size
                }
                continue
            }

            let fileURL = pathResolver.bookmarkFileURL(for: bookmark.reelID)
            if fm.fileExists(atPath: fileURL.path) {
                try? fm.removeItem(at: fileURL)
            }
            // IOS-P1-7: only mark evicted when the file is actually gone. A
            // failed delete must keep the row cached (and counted) so the
            // 1.5 GB cap cannot be bypassed by phantom evictions.
            if !fm.fileExists(atPath: fileURL.path) {
                bookmark.localStatus = .evicted
            } else if let size = Self.diskFileSize(atPath: fileURL.path) {
                remainingBytes += size
            }
        }

        try? context.save()
        self.totalBookmarkBytes = remainingBytes

        // Resume downloads and clear purging flag
        await downloadSuspensionHandler?(false)
    }

    // MARK: - .part File Promotion & Validation

    /// Atomically promotes a temporary download (.part) file to a finalized .mp4 file.
    /// Validates sizeBytes if provided, excludes from backup, and applies completeUntilFirstUserAuthentication.
    @discardableResult
    public func promotePartFile(
        from temporaryURL: URL,
        weekID: String,
        reelID: String,
        expectedSizeBytes: Int64? = nil
    ) async throws -> URL {
        let fm = FileManager.default

        guard fm.fileExists(atPath: temporaryURL.path) else {
            throw CacheError.fileNotFound(temporaryURL.path)
        }

        let attrs = try fm.attributesOfItem(atPath: temporaryURL.path)
        let actualSize = (attrs[.size] as? NSNumber)?.int64Value ?? 0

        guard actualSize > 0 else {
            try? fm.removeItem(at: temporaryURL)
            throw CacheError.corruptedFile("Temporary download file is 0 bytes")
        }

        if let expected = expectedSizeBytes, expected > 0 {
            // IOS-P1-3: manifest sizeBytes is often an estimate, so require
            // the file to be within 10% (or 1 MB) instead of exact equality.
            // Strict equality discarded valid downloads and retried 3x.
            let lowerBound = min(Int64(Double(expected) * 0.9), expected - 1_000_000)
            if actualSize < max(1, lowerBound) {
                try? fm.removeItem(at: temporaryURL)
                throw CacheError.sizeMismatch(expected: expected, actual: actualSize)
            }
        }

        try pathResolver.ensureDirectoriesExist(for: weekID)
        let destinationURL = pathResolver.localFileURL(for: weekID, reelID: reelID)

        // Atomic replacement via replaceItemAt or atomic move
        if fm.fileExists(atPath: destinationURL.path) {
            // IOS-P1-4: a failed replace must surface, never silently leave
            // the old/corrupt destination while reporting success.
            guard let _ = try? fm.replaceItemAt(destinationURL, withItemAt: temporaryURL) else {
                try? fm.removeItem(at: temporaryURL)
                throw CacheError.corruptedFile("Atomic replace failed for \(reelID)")
            }
        } else {
            try fm.moveItem(at: temporaryURL, to: destinationURL)
        }

        try pathResolver.applyProtectionAndBackupExclusion(to: destinationURL)

        return destinationURL
    }

    /// Reads a file's byte size via NSNumber bridging (FileManager reports sizes as
    /// NSNumber; a direct `as? Int64` conditional cast is not guaranteed to succeed).
    public static func diskFileSize(atPath path: String) -> Int64? {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let number = attrs[.size] as? NSNumber else { return nil }
        return number.int64Value
    }

    public enum CacheError: Error, LocalizedError {
        case fileNotFound(String)
        case corruptedFile(String)
        case sizeMismatch(expected: Int64, actual: Int64)
        case insufficientStorage(String)

        public var errorDescription: String? {
            switch self {
            case .fileNotFound(let path):
                return "File not found at \(path)"
            case .corruptedFile(let msg):
                return "Corrupted file: \(msg)"
            case .sizeMismatch(let exp, let act):
                return "Size mismatch: expected \(exp) bytes, got \(act) bytes"
            case .insufficientStorage(let msg):
                return "Insufficient storage: \(msg)"
            }
        }
    }
}
