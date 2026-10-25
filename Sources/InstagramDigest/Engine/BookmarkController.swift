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
            tasks["add_save_\(reelID)"]?.cancel()
            tasks["thumb_\(reelID)"]?.cancel()
            MediaCacheManager.addUnbookmarkedTombstone(reelID)
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
            Task {
                await GitHubBookmarkSyncService.shared.syncIfNeeded(context: context)
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
            MediaCacheManager.clearUnbookmarkedTombstone(reel.id)
            let resolver = LibraryPathResolver.shared
            try? resolver.ensureDirectoryExists(at: resolver.bookmarksDirectoryURL)

            // AUDIT-011: Preflight initial status without blocking on copy:
            // if bmVideo is already present on disk (e.g. re-bookmarking), mark .cached, else .evicted
            let bmVideo = resolver.bookmarkFileURL(for: reel.id)
            let initialCached = FileManager.default.fileExists(atPath: bmVideo.path)

            // Fast MainActor insert/update of SwiftData record so UI updates instantaneously
            if let existing = existingItem {
                existing.weekID = weekID
                existing.creatorHandle = reel.creatorHandle
                existing.caption = reel.caption
                existing.rank = reel.rank
                existing.videoUrlString = reel.videoUrl.absoluteString
                existing.thumbnailUrlString = reel.thumbnailUrl?.absoluteString
                existing.canonicalUrlString = reel.canonicalUrl?.absoluteString
                existing.localStatus = initialCached ? .cached : .evicted
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
                    localStatus: initialCached ? .cached : .evicted,
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

            let reelID = reel.id
            let thumbURL = reel.thumbnailUrl
            let cap = UserDefaults.standard.object(forKey: "max_cached_bookmark_videos") as? Int ?? 50

            let addSaveTaskKey = "add_save_\(reel.id)"
            tasks[addSaveTaskKey]?.cancel()
            tasks[addSaveTaskKey] = Task {
                defer { tasks[addSaveTaskKey] = nil }

                // AUDIT-011: Heavy file copy executed asynchronously off MainActor
                let hasVideo = await Task.detached(priority: .utility) { () -> Bool in
                    let res = LibraryPathResolver.shared
                    let bThumb = res.bookmarkThumbnailFileURL(for: reelID)
                    let wThumb = res.thumbnailFileURL(for: weekID, reelID: reelID)
                    if FileManager.default.fileExists(atPath: wThumb.path) && !FileManager.default.fileExists(atPath: bThumb.path) {
                        try? FileManager.default.copyItem(at: wThumb, to: bThumb)
                    }

                    let wVideo = res.localFileURL(for: weekID, reelID: reelID)
                    let bVideo = res.bookmarkFileURL(for: reelID)
                    if FileManager.default.fileExists(atPath: wVideo.path) && !FileManager.default.fileExists(atPath: bVideo.path) {
                        try? FileManager.default.copyItem(at: wVideo, to: bVideo)
                    }

                    return FileManager.default.fileExists(atPath: bVideo.path)
                }.value

                guard !Task.isCancelled else { return }

                // Download thumbnail from URL if still not present locally
                let bThumb = resolver.bookmarkThumbnailFileURL(for: reelID)
                if !FileManager.default.fileExists(atPath: bThumb.path), let thumbURL {
                    if let (data, _) = try? await URLSession.shared.data(from: thumbURL) {
                        try? data.write(to: bThumb, options: .atomic)
                    }
                }

                guard !Task.isCancelled else { return }

                // AUDIT-024 / AUDIT-011: Update localStatus on MainActor once background copy confirms existence
                if hasVideo {
                    let descriptor = FetchDescriptor<BookmarkItem>(
                        predicate: #Predicate { $0.reelID == reelID }
                    )
                    if let target = (try? context.fetch(descriptor))?.first {
                        target.localStatus = .cached
                        try? context.save()
                    }
                }

                // AUDIT-025: Distinguish unset (defaults to 50) from explicit 0 (keep all/unlimited)
                enforceCachedVideosCap(limit: cap, context: context)
                await GitHubBookmarkSyncService.shared.syncIfNeeded(context: context)
            }
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
    /// Prioritizes evicting items with `videoAssetName != nil` (already uploaded to GitHub)
    /// before evicting items that have not yet uploaded.
    public static func enforceCachedVideosCap(limit: Int, context: ModelContext, resolver: LibraryPathResolver = .shared) {
        guard limit > 0 else { return } // 0 means keep all / unlimited
        // Evict by lastAccessedAt (LRU)
        let descriptor = FetchDescriptor<BookmarkItem>(
            sortBy: [SortDescriptor(\.lastAccessedAt, order: .reverse)]
        )
        guard let allBookmarks = try? context.fetch(descriptor) else { return }

        let fm = FileManager.default
        let boundIDs = AVPlayerPool.shared.boundReelIDs

        // Gather all bookmarks that actually have a cached video file on disk
        var cachedBookmarks: [BookmarkItem] = []
        for bm in allBookmarks {
            let videoURL = resolver.bookmarkFileURL(for: bm.reelID)
            if fm.fileExists(atPath: videoURL.path) {
                cachedBookmarks.append(bm)
            }
        }

        let excess = cachedBookmarks.count - limit
        guard excess > 0 else {
            // All cached bookmarks are within limit
            for bm in cachedBookmarks {
                bm.localStatus = .cached
            }
            try? context.save()
            return
        }

        // Candidates for eviction: those that are NOT currently bound/playing in AVPlayerPool
        let evictable = cachedBookmarks.filter { !boundIDs.contains($0.reelID) }

        // Sort candidates so that:
        // 1. Items with videoAssetName != nil (already backed up to GitHub) are evicted first
        // 2. Within each tier, oldest lastAccessedAt is evicted first (reverse of MRU order)
        let sortedForEviction = evictable.sorted { (a, b) -> Bool in
            let aHasAsset = a.videoAssetName != nil
            let bHasAsset = b.videoAssetName != nil
            if aHasAsset != bHasAsset {
                return aHasAsset && !bHasAsset
            }
            return a.lastAccessedAt < b.lastAccessedAt
        }

        var evictedCount = 0
        for bm in sortedForEviction {
            guard evictedCount < excess else { break }
            let videoURL = resolver.bookmarkFileURL(for: bm.reelID)
            do {
                try fm.removeItem(at: videoURL)
                if !fm.fileExists(atPath: videoURL.path) {
                    bm.localStatus = .evicted
                    evictedCount += 1
                }
            } catch {
                // If unlink failed, continue
            }
        }

        // Mark remaining items that still exist on disk as .cached
        for bm in cachedBookmarks {
            let videoURL = resolver.bookmarkFileURL(for: bm.reelID)
            if fm.fileExists(atPath: videoURL.path) {
                bm.localStatus = .cached
            }
        }

        try? context.save()
    }
}
