import Foundation
import SwiftData

/// Single owner for the bookmark add/remove lifecycle (Rec #1).
///
/// Previously duplicated between the feed
/// (`InstagramDigestApp.toggleBookmarkCurrentReel`) and the Bookmarks sheet
/// (`BookmarksSheet.deleteBookmark`). The copies drifted — only the feed path
/// tombstoned un-bookmarks (P1-1), and their file/ledger/remote lifecycles
/// differed subtly. Both views now call this one pair.
@MainActor
public enum BookmarkController {
    private static var tasks: [String: Task<Void, Never>] = [:]

    /// Full remove lifecycle: tombstone, file delete, ledger reconcile,
    /// fire-and-forget remote delete, SwiftData row delete.
    public static func remove(reelID: String, context: ModelContext) {
        if DigestDataService.directMode {
            let resolver = LibraryPathResolver.shared
            let bmThumb = resolver.bookmarkThumbnailFileURL(for: reelID)
            try? FileManager.default.removeItem(at: bmThumb)
            // AUDIT-023: Route direct-mode video delete through MediaCacheManager to defer if currently bound/playing
            let rID = reelID
            Task {
                await MediaCacheManager.shared.deleteBookmarkFile(reelID: rID)
            }

            let descriptor = FetchDescriptor<BookmarkItem>(
                predicate: #Predicate { $0.reelID == reelID }
            )
            if let found = try? context.fetch(descriptor), let item = found.first {
                context.delete(item)
                do {
                    try context.save()
                } catch {
                    context.rollback()
                }
            }
            return
        }

        // Legacy Cloudflare Worker remove lifecycle
        MediaCacheManager.addUnbookmarkedTombstone(reelID)
        MediaCacheManager.removePendingRemoteBookmark(reelID)
        let rID = reelID
        tasks[reelID]?.cancel()
        tasks[reelID] = Task {
            defer { tasks[rID] = nil }
            await MediaCacheManager.shared.deleteBookmarkFile(reelID: rID)
            guard !Task.isCancelled else { return }
            if ProcessInfo.processInfo.arguments.contains("-ui-testing") { return }
            await MediaCacheManager.shared.reconcileBookmarkStorageLedger()
            _ = try? await DigestDataService.shared.deleteRemoteBookmark(reelID: rID)
        }
        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.reelID == reelID }
        )
        if let found = try? context.fetch(descriptor), let item = found.first {
            context.delete(item)
            do {
                try context.save()
            } catch {
                context.rollback()
            }
        }
    }

    /// Full add lifecycle: saves bookmark metadata row. In direct mode, uses feed cache without isolated copies or remote worker.
    public static func add(reel: ReelItem, weekID: String, context: ModelContext) {
        let targetID = reel.id
        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.reelID == targetID }
        )
        let existingItem = (try? context.fetch(descriptor))?.first

        if DigestDataService.directMode {
            let resolver = LibraryPathResolver.shared
            try? resolver.ensureDirectoryExists(at: resolver.bookmarksDirectoryURL)

            // 1. Preserve thumbnail locally for offline browsing
            let bmThumb = resolver.bookmarkThumbnailFileURL(for: reel.id)
            let weekThumb = resolver.thumbnailFileURL(for: weekID, reelID: reel.id)
            if FileManager.default.fileExists(atPath: weekThumb.path) {
                if !FileManager.default.fileExists(atPath: bmThumb.path) {
                    try? FileManager.default.copyItem(at: weekThumb, to: bmThumb)
                }
            } else if let thumbURL = reel.thumbnailUrl {
                let thumbTaskKey = "thumb_\(reel.id)"
                tasks[thumbTaskKey]?.cancel()
                tasks[thumbTaskKey] = Task {
                    defer { tasks[thumbTaskKey] = nil }
                    if let (data, _) = try? await URLSession.shared.data(from: thumbURL) {
                        try? data.write(to: bmThumb, options: .atomic)
                    }
                }
            }

            // 2. Preserve downloaded video in bookmarks directory so it survives rollover
            let weekVideo = resolver.localFileURL(for: weekID, reelID: reel.id)
            let bmVideo = resolver.bookmarkFileURL(for: reel.id)
            if FileManager.default.fileExists(atPath: weekVideo.path) && !FileManager.default.fileExists(atPath: bmVideo.path) {
                try? FileManager.default.copyItem(at: weekVideo, to: bmVideo)
            }

            // AUDIT-024: Verify bmVideo exists before claiming bookmark-cached
            let bookmarkHasVideo = FileManager.default.fileExists(atPath: bmVideo.path)
            let isLocallyAvailable = bookmarkHasVideo

            if let existing = existingItem {
                existing.weekID = weekID
                existing.creatorHandle = reel.creatorHandle
                existing.caption = reel.caption
                existing.rank = reel.rank
                existing.videoUrlString = reel.videoUrl.absoluteString
                existing.thumbnailUrlString = reel.thumbnailUrl?.absoluteString
                existing.canonicalUrlString = reel.canonicalUrl?.absoluteString
                existing.localStatus = isLocallyAvailable ? .cached : .evicted
                if let sb = reel.sizeBytes, sb > 0 {
                    existing.sizeBytes = sb
                }
                existing.bookmarkedAt = Date()
                existing.lastAccessedAt = Date()
            } else {
                let item = BookmarkItem(
                    reelID: reel.id,
                    weekID: weekID,
                    creatorHandle: reel.creatorHandle,
                    caption: reel.caption,
                    rank: reel.rank,
                    videoUrl: reel.videoUrl,
                    thumbnailUrl: reel.thumbnailUrl,
                    canonicalUrl: reel.canonicalUrl,
                    bookmarkedAt: Date(),
                    localStatus: isLocallyAvailable ? .cached : .evicted,
                    sizeBytes: reel.sizeBytes ?? 0,
                    lastAccessedAt: Date()
                )
                context.insert(item)
            }
            do {
                try context.save()
            } catch {
                context.rollback()
            }
            // AUDIT-025: Distinguish unset (defaults to 50) from explicit 0 (keep all/unlimited)
            let cap = UserDefaults.standard.object(forKey: "max_cached_bookmark_videos") as? Int ?? 50
            enforceCachedVideosCap(limit: cap, context: context)
            return
        }

        // Legacy Cloudflare Worker add lifecycle
        MediaCacheManager.clearUnbookmarkedTombstone(reel.id)
        if let existing = existingItem {
            existing.weekID = weekID
            existing.creatorHandle = reel.creatorHandle
            existing.caption = reel.caption
            existing.rank = reel.rank
            existing.videoUrlString = reel.videoUrl.absoluteString
            existing.thumbnailUrlString = reel.thumbnailUrl?.absoluteString
            existing.canonicalUrlString = reel.canonicalUrl?.absoluteString
            if let sb = reel.sizeBytes, sb > 0 {
                existing.sizeBytes = sb
            }
            existing.bookmarkedAt = Date()
            existing.lastAccessedAt = Date()
        } else {
            let item = BookmarkItem(
                reelID: reel.id,
                weekID: weekID,
                creatorHandle: reel.creatorHandle,
                caption: reel.caption,
                rank: reel.rank,
                videoUrl: reel.videoUrl,
                thumbnailUrl: reel.thumbnailUrl,
                canonicalUrl: reel.canonicalUrl,
                bookmarkedAt: Date(),
                localStatus: .evicted,
                sizeBytes: reel.sizeBytes ?? 0,
                lastAccessedAt: Date()
            )
            context.insert(item)
        }
        do {
            try context.save()
        } catch {
            context.rollback()
            return
        }
        let rID = reel.id
        let wID = weekID
        let sBytes = reel.sizeBytes ?? 0
        tasks[rID]?.cancel()
        tasks[rID] = Task {
            defer { tasks[rID] = nil }
            do {
                try await MediaCacheManager.shared.keepBookmarkOffline(
                    weekID: wID,
                    reelID: rID,
                    fallbackSizeBytes: sBytes
                )
            } catch {}
            guard !Task.isCancelled else { return }
            MediaCacheManager.addPendingRemoteBookmark(rID)
            if ProcessInfo.processInfo.arguments.contains("-ui-testing") { return }
            if (try? await DigestDataService.shared.saveRemoteBookmark(reel: reel)) == true {
                MediaCacheManager.removePendingRemoteBookmark(rID)
            }
        }
    }

    /// Cancels all active in-flight bookmark controller tasks (AUDIT-068)
    public static func cancelAll() {
        for task in tasks.values {
            task.cancel()
        }
        tasks.removeAll()
    }

    /// Enforces the maximum number of offline cached bookmark videos.
    /// When total cached videos exceed `limit`, older video files are deleted from disk
    /// while keeping their SwiftData record, metadata, and thumbnail completely intact.
    public static func enforceCachedVideosCap(limit: Int, context: ModelContext) {
        guard limit > 0 else { return } // 0 means keep all / unlimited
        // AUDIT-025: Evict by lastAccessedAt (LRU) instead of bookmarkedAt
        let descriptor = FetchDescriptor<BookmarkItem>(
            sortBy: [SortDescriptor(\.lastAccessedAt, order: .reverse)]
        )
        guard let allBookmarks = try? context.fetch(descriptor) else { return }

        let resolver = LibraryPathResolver.shared
        let fm = FileManager.default
        let boundIDs = AVPlayerPool.shared.boundReelIDs
        var cachedCount = 0

        for bm in allBookmarks {
            let videoURL = resolver.bookmarkFileURL(for: bm.reelID)
            let hasVideo = fm.fileExists(atPath: videoURL.path)
            if hasVideo {
                cachedCount += 1
                if cachedCount > limit {
                    // AUDIT-025: Never evict videos currently bound or playing in AVPlayerPool
                    if boundIDs.contains(bm.reelID) {
                        continue
                    }
                    // Attempt eviction
                    guard (try? fm.removeItem(at: videoURL)) != nil else {
                        // Unlink failed; keep counted as cached
                        continue
                    }
                    if !fm.fileExists(atPath: videoURL.path) {
                        bm.localStatus = .evicted
                    }
                } else {
                    bm.localStatus = .cached
                }
            }
        }
        try? context.save()
    }
}
