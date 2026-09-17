import Foundation

/// Centralized Sendable resolver that turns weekID and reelID into sandboxed file URLs inside
/// Library/Application Support/MediaCache/ with filesystem hardening and bookmark isolation.
public struct LibraryPathResolver: Sendable {
    public static let shared = LibraryPathResolver()

    public static let mediaCacheFolderName = "MediaCache"
    public static let bookmarksFolderName = "Bookmarks"
    public static let resumeDataFolderName = "ResumeData"

    public init() {}

    /// Root directory URL for media cache inside Library/Application Support/
    public var mediaCacheBaseURL: URL {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return appSupport.appendingPathComponent(Self.mediaCacheFolderName, isDirectory: true)
    }

    /// Dedicated directory for offline bookmarked reels (isolated from weekly feed cache)
    public var bookmarksDirectoryURL: URL {
        mediaCacheBaseURL.appendingPathComponent(Self.bookmarksFolderName, isDirectory: true)
    }

    /// Dedicated directory for persisting download resumeData
    public var resumeDataDirectoryURL: URL {
        mediaCacheBaseURL.appendingPathComponent(Self.resumeDataFolderName, isDirectory: true)
    }

    /// Sanitizes component strings to prevent path traversal or filesystem escapes.
    public static func sanitizeComponent(_ value: String, fallback: String = "unknown") -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              !trimmed.hasPrefix("/"),
              !trimmed.hasPrefix("~"),
              !trimmed.contains("\0") else { return fallback }

        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "._-"))
        let filtered = trimmed.unicodeScalars.filter { allowed.contains($0) }
        let result = String(String.UnicodeScalarView(filtered)).trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return result.isEmpty ? fallback : result
    }

    /// URL for a specific week directory inside Application Support/MediaCache/{week_id}/
    public func weekDirectoryURL(for weekID: String) -> URL {
        let safeWeek = Self.sanitizeComponent(weekID, fallback: "default_week")
        return mediaCacheBaseURL.appendingPathComponent(safeWeek, isDirectory: true)
    }

    /// Local completed MP4 file URL for feed: {reel_id}.mp4
    public func localFileURL(for weekID: String, reelID: String) -> URL {
        let weekURL = weekDirectoryURL(for: weekID)
        let safeReel = Self.sanitizeComponent(reelID, fallback: "reel")
        return weekURL.appendingPathComponent("\(safeReel).mp4", isDirectory: false)
    }

    /// Local temporary download file URL: {reel_id}.mp4.part
    public func localPartFileURL(for weekID: String, reelID: String) -> URL {
        let weekURL = weekDirectoryURL(for: weekID)
        let safeReel = Self.sanitizeComponent(reelID, fallback: "reel")
        return weekURL.appendingPathComponent("\(safeReel).mp4.part", isDirectory: false)
    }

    /// Local offline storage URL for bookmarked reels: MediaCache/Bookmarks/{reel_id}.mp4
    public func bookmarkFileURL(for reelID: String) -> URL {
        let safeReel = Self.sanitizeComponent(reelID, fallback: "reel")
        return bookmarksDirectoryURL.appendingPathComponent("\(safeReel).mp4", isDirectory: false)
    }

    /// Local storage URL for persisting resumeData: MediaCache/ResumeData/{reel_id}.dat
    public func resumeDataFileURL(for reelID: String) -> URL {
        let safeReel = Self.sanitizeComponent(reelID, fallback: "reel")
        return resumeDataDirectoryURL.appendingPathComponent("\(safeReel).dat", isDirectory: false)
    }

    /// Checks whether completed MP4 file exists and has non-zero size.
    public func isLocalFileAvailable(for weekID: String, reelID: String) -> Bool {
        // Check week directory first
        let weekURL = localFileURL(for: weekID, reelID: reelID)
        if fileExistsAndNonEmpty(at: weekURL) {
            return true
        }
        // Check isolated bookmarks directory
        let bookmarkURL = bookmarkFileURL(for: reelID)
        return fileExistsAndNonEmpty(at: bookmarkURL)
    }

    /// Returns the best available local file URL (feed directory or bookmark directory)
    public func resolvedLocalFileURL(for weekID: String, reelID: String) -> URL? {
        let weekURL = localFileURL(for: weekID, reelID: reelID)
        if fileExistsAndNonEmpty(at: weekURL) {
            return weekURL
        }
        let bookmarkURL = bookmarkFileURL(for: reelID)
        if fileExistsAndNonEmpty(at: bookmarkURL) {
            return bookmarkURL
        }
        return nil
    }

    /// Gets exact byte size of the local file if present.
    public func localFileSize(for weekID: String, reelID: String) -> Int64? {
        if let url = resolvedLocalFileURL(for: weekID, reelID: reelID),
           let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
           let number = attrs[.size] as? NSNumber {
            return number.int64Value
        }
        return nil
    }

    private func fileExistsAndNonEmpty(at url: URL) -> Bool {
        var isDir: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), !isDir.boolValue {
            if let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
               let number = attrs[.size] as? NSNumber {
                return number.int64Value > 0
            }
            return true
        }
        return false
    }

    /// Ensures directory exists and is hardened with iCloud backup exclusion and complete-until-auth protection.
    @discardableResult
    public func ensureDirectoryExists(at directoryURL: URL) throws -> URL {
        let fm = FileManager.default
        if !fm.fileExists(atPath: directoryURL.path) {
            try fm.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        }
        try applyProtectionAndBackupExclusion(to: directoryURL)
        return directoryURL
    }

    /// Ensures parent directory exists for weekID
    @discardableResult
    public func ensureDirectoriesExist(for weekID: String) throws -> URL {
        let dir = weekDirectoryURL(for: weekID)
        return try ensureDirectoryExists(at: dir)
    }

    /// Applies URLResourceKey.isExcludedFromBackupKey = true and
    /// FileProtectionType.completeUntilFirstUserAuthentication to the given URL.
    public func applyProtectionAndBackupExclusion(to url: URL) throws {
        let fm = FileManager.default
        guard fm.fileExists(atPath: url.path) else { return }

        // 1. Exclude from iCloud backup
        var mutableURL = url
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try mutableURL.setResourceValues(values)

        // 2. Set completeUntilFirstUserAuthentication
        try fm.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
    }

    /// Best-effort pre-creation of Application Support directory before SwiftData initialization
    public func ensureApplicationSupportExists() {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        if let appSupport {
            try? FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
        }
    }
}
