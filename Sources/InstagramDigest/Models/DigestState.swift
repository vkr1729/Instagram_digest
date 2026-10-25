import Foundation
import SwiftData

/// Local status of a bookmarked media file on disk.
public enum BookmarkLocalStatus: String, Codable, Sendable {
    case cached = "cached"
    case evicted = "evicted"
}

/// Append-only log of watched reels to maintain history across weeks.
@Model
public final class WatchedEvent {
    @Attribute(.unique) public var compoundKey: String = "" // "{weekID}\u{1F}{reelID}"
    public var reelID: String = ""
    public var weekID: String = ""
    public var timestamp: Date = Date()

    public init(
        reelID: String,
        weekID: String,
        timestamp: Date = Date()
    ) {
        self.compoundKey = "\(weekID)\u{1F}\(reelID)"
        self.reelID = reelID
        self.weekID = weekID
        self.timestamp = timestamp
    }
}

/// Denormalized snapshot of a bookmarked reel with disk storage status and access time for LRU eviction.
@Model
public final class BookmarkItem {
    @Attribute(.unique) public var reelID: String = ""
    public var weekID: String = ""
    public var creatorHandle: String = ""
    public var caption: String = ""
    public var rank: Int = 1
    public var videoUrlString: String = ""
    public var thumbnailUrlString: String? = nil
    public var canonicalUrlString: String? = nil
    public var videoAssetName: String? = nil
    public var releaseTag: String? = nil
    public var bookmarkedAt: Date = Date()
    public var localStatusRaw: String = BookmarkLocalStatus.cached.rawValue
    public var sizeBytes: Int64 = 0
    public var lastAccessedAt: Date = Date()

    public init(
        reelID: String,
        weekID: String,
        creatorHandle: String,
        caption: String,
        rank: Int,
        videoUrl: URL? = nil,
        thumbnailUrl: URL? = nil,
        canonicalUrl: URL? = nil,
        videoAssetName: String? = nil,
        releaseTag: String? = nil,
        bookmarkedAt: Date = Date(),
        localStatus: BookmarkLocalStatus = .cached,
        sizeBytes: Int64 = 0,
        lastAccessedAt: Date = Date()
    ) {
        self.reelID = reelID
        self.weekID = weekID
        self.creatorHandle = creatorHandle
        self.caption = caption
        self.rank = rank
        self.videoUrlString = videoUrl?.absoluteString ?? ""
        self.thumbnailUrlString = thumbnailUrl?.absoluteString
        let safeReel = LibraryPathResolver.sanitizeComponent(reelID, fallback: "unknown")
        self.canonicalUrlString = canonicalUrl?.absoluteString ?? "https://www.instagram.com/reel/\(safeReel)/"
        self.videoAssetName = videoAssetName
        self.releaseTag = releaseTag
        self.bookmarkedAt = bookmarkedAt
        self.localStatusRaw = localStatus.rawValue
        self.sizeBytes = sizeBytes
        self.lastAccessedAt = lastAccessedAt
    }

    public var localStatus: BookmarkLocalStatus {
        get {
            BookmarkLocalStatus(rawValue: localStatusRaw) ?? .evicted
        }
        set {
            localStatusRaw = newValue.rawValue
        }
    }

    public var videoUrl: URL? {
        URL(string: videoUrlString)
    }

    public var canonicalUrl: URL? {
        if let s = canonicalUrlString, let u = URL(string: s) {
            return u
        }
        let safe = LibraryPathResolver.sanitizeComponent(reelID, fallback: "unknown")
        return URL(string: "https://www.instagram.com/reel/\(safe)/")
    }

    public var thumbnailUrl: URL? {
        if let s = thumbnailUrlString {
            if s.hasPrefix("thumbnails/") {
                let repo = UserDefaults.standard.string(forKey: "gh_bookmark_repo") ?? "vkreddy1729-ops/ig-bookmarks"
                return URL(string: "https://raw.githubusercontent.com/\(repo)/main/\(s)")
            }
            if let u = URL(string: s), u.host?.contains("fbcdn.net") != true {
                if s.contains("/thumbnails/") && !s.contains("_portrait.jpg") {
                    return URL(string: s.replacingOccurrences(of: ".jpg", with: "_portrait.jpg"))
                }
                return u
            }
        }
        let safe = LibraryPathResolver.sanitizeComponent(reelID, fallback: "unknown")
        return URL(string: "https://vkr1729.github.io/Instagram_digest/thumbnails/\(safe)_portrait.jpg")
    }
}

/// Tracks daily viewing velocity for mindful usage reminders (time quota and completion modal).
@Model
public final class DailyProgress {
    @Attribute(.unique) public var dateString: String = "" // "YYYY-MM-DD" local calendar date
    public var viewedCount: Int = 0
    public var snoozeUntil: Date? = nil
    public var watchDurationSeconds: Double = 0.0
    public var targetMinutes: Int = 20
    public var enforcementMode: String = "gentle" // "gentle" or "hard"

    public init(
        dateString: String,
        viewedCount: Int = 0,
        snoozeUntil: Date? = nil,
        watchDurationSeconds: Double = 0.0,
        targetMinutes: Int = 20,
        enforcementMode: String = "gentle"
    ) {
        self.dateString = dateString
        self.viewedCount = viewedCount
        self.snoozeUntil = snoozeUntil
        self.watchDurationSeconds = watchDurationSeconds
        self.targetMinutes = targetMinutes
        self.enforcementMode = enforcementMode
    }
}

/// Global persistent app state tracking current week and last active reel.
@Model
public final class AppState {
    @Attribute(.unique) public var id: String = "primary_state"
    public var currentWeekID: String = ""
    public var lastActiveReelID: String? = nil

    public init(
        id: String = "primary_state",
        currentWeekID: String = "",
        lastActiveReelID: String? = nil
    ) {
        self.id = id
        self.currentWeekID = currentWeekID
        self.lastActiveReelID = lastActiveReelID
    }
}
