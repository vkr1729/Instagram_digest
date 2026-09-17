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
        videoUrl: URL,
        thumbnailUrl: URL? = nil,
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
        self.videoUrlString = videoUrl.absoluteString
        self.thumbnailUrlString = thumbnailUrl?.absoluteString
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

    public var thumbnailUrl: URL? {
        guard let s = thumbnailUrlString else { return nil }
        return URL(string: s)
    }
}

/// Tracks daily viewing velocity for mindful usage reminders (50/day completion modal).
@Model
public final class DailyProgress {
    @Attribute(.unique) public var dateString: String = "" // "YYYY-MM-DD" local calendar date
    public var viewedCount: Int = 0
    public var snoozeUntil: Date? = nil

    public init(
        dateString: String,
        viewedCount: Int = 0,
        snoozeUntil: Date? = nil
    ) {
        self.dateString = dateString
        self.viewedCount = viewedCount
        self.snoozeUntil = snoozeUntil
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
