import Foundation
import SwiftData

/// Serialized async actor managing local disk cache, LivePinSet protection,
/// 1.5 GB bookmark cap with LRU eviction, and .part download atomic promotion.
public actor MediaCacheManager {
    public static let shared = MediaCacheManager()

    /// 1.5 GB cap for offline bookmarked videos
    public static let maxBookmarkStorageBytes: Int64 = 1_500_000_000

    private let pathResolver: LibraryPathResolver
    private var modelContainer: ModelContainer?

    /// In-memory ledger reflecting cached bookmark MP4 bytes only
    public private(set) var totalBookmarkBytes: Int64 = 0

    /// Bound reel IDs currently active or preloaded in the video pool
    private var activeVideoPoolReelIDs: Set<String> = []

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

    /// Updates active reel IDs bound to the AVPlayer pool (protected by LivePinSet)
    public func setActiveVideoPoolReelIDs(_ ids: Set<String>) {
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
                    if let attrs = try? FileManager.default.attributesOfItem(atPath: fileURL.path),
                       let size = attrs[.size] as? Int64, size > 0 {
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
    }

    /// Serialized local file eviction for corrupted local feed files (called by failure ladder)
    public func evictLocalFeedFile(weekID: String, reelID: String) {
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
                if let attrs = try? fm.attributesOfItem(atPath: fileURL.path),
                   let actualSize = attrs[.size] as? Int64 {
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
                if let attrs = try? fm.attributesOfItem(atPath: bookmarkDestURL.path),
                   let size = attrs[.size] as? Int64 {
                    item.sizeBytes = size
                    self.totalBookmarkBytes += size
                }
                try? context.save()
            }
            return
        }

        // Locate existing media file from feed
        let feedURL = pathResolver.localFileURL(for: weekID, reelID: reelID)
        guard fm.fileExists(atPath: feedURL.path) else {
            throw CacheError.fileNotFound("Local media not available to store offline. Download first.")
        }

        let attrs = try fm.attributesOfItem(atPath: feedURL.path)
        let fileSize = attrs[.size] as? Int64 ?? (item.sizeBytes > 0 ? item.sizeBytes : fallbackSizeBytes)

        // Ensure space under 1.5 GB cap
        try await ensureSpaceForBookmark(incomingBytes: fileSize)

        try pathResolver.ensureDirectoryExists(at: pathResolver.bookmarksDirectoryURL)
        try fm.copyItem(at: feedURL, to: bookmarkDestURL)
        try pathResolver.applyProtectionAndBackupExclusion(to: bookmarkDestURL)

        item.localStatus = .cached
        item.sizeBytes = fileSize
        item.lastAccessedAt = Date()
        self.totalBookmarkBytes += fileSize
        try? context.save()
    }

    /// Serialized deletion of a bookmark's offline file (avoids orphaned files on unbookmark or delete)
    public func deleteBookmarkFile(reelID: String) {
        let bookmarkDestURL = pathResolver.bookmarkFileURL(for: reelID)
        let fm = FileManager.default
        if fm.fileExists(atPath: bookmarkDestURL.path) {
            if let attrs = try? fm.attributesOfItem(atPath: bookmarkDestURL.path),
               let size = attrs[.size] as? Int64 {
                self.totalBookmarkBytes = max(0, self.totalBookmarkBytes - size)
            }
            try? fm.removeItem(at: bookmarkDestURL)
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

        guard let container = modelContainer else {
            isPurging = false
            await downloadSuspensionHandler?(false)
            return
        }

        let context = ModelContext(container)
        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.localStatusRaw == "cached" }
        )

        guard let bookmarks = try? context.fetch(descriptor) else {
            isPurging = false
            await downloadSuspensionHandler?(false)
            return
        }

        var remainingBytes: Int64 = 0
        let fm = FileManager.default

        for bookmark in bookmarks {
            // Never delete or flip an actively bound reel
            if activeVideoPoolReelIDs.contains(bookmark.reelID) {
                let fileURL = pathResolver.bookmarkFileURL(for: bookmark.reelID)
                if let attrs = try? fm.attributesOfItem(atPath: fileURL.path),
                   let size = attrs[.size] as? Int64 {
                    remainingBytes += size
                }
                continue
            }

            let fileURL = pathResolver.bookmarkFileURL(for: bookmark.reelID)
            if fm.fileExists(atPath: fileURL.path) {
                try? fm.removeItem(at: fileURL)
            }

            bookmark.localStatus = .evicted
        }

        try? context.save()
        self.totalBookmarkBytes = remainingBytes

        // Resume downloads and clear purging flag
        await downloadSuspensionHandler?(false)
        self.isPurging = false
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
        let actualSize = attrs[.size] as? Int64 ?? 0

        guard actualSize > 0 else {
            try? fm.removeItem(at: temporaryURL)
            throw CacheError.corruptedFile("Temporary download file is 0 bytes")
        }

        if let expected = expectedSizeBytes, expected > 0 {
            if actualSize != expected {
                try? fm.removeItem(at: temporaryURL)
                throw CacheError.sizeMismatch(expected: expected, actual: actualSize)
            }
        }

        try pathResolver.ensureDirectoriesExist(for: weekID)
        let destinationURL = pathResolver.localFileURL(for: weekID, reelID: reelID)

        // Atomic replacement via replaceItemAt or atomic move
        if fm.fileExists(atPath: destinationURL.path) {
            _ = try? fm.replaceItemAt(destinationURL, withItemAt: temporaryURL)
        } else {
            try fm.moveItem(at: temporaryURL, to: destinationURL)
        }

        try pathResolver.applyProtectionAndBackupExclusion(to: destinationURL)

        return destinationURL
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
