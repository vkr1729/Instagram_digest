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
        // B13: tombstone before the fire-and-forget remote delete — a failed
        // delete must not resurrect the row on the next syncRemoteBookmarks.
        MediaCacheManager.addUnbookmarkedTombstone(reelID)
        tasks[reelID]?.cancel()
        tasks[reelID] = Task {
            await MediaCacheManager.shared.deleteBookmarkFile(reelID: reelID)
            await MediaCacheManager.shared.reconcileBookmarkStorageLedger()
            guard !Task.isCancelled else { return }
            _ = try? await DigestDataService.shared.deleteRemoteBookmark(reelID: reelID)
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

    /// Full add lifecycle: lift tombstone, insert `.evicted` row, isolated
    /// copy via `MediaCacheManager` (flips to `.cached` on success), remote sync.
    public static func add(reel: ReelItem, weekID: String, context: ModelContext) {
        // B13: re-bookmarking lifts any earlier tombstone.
        MediaCacheManager.clearUnbookmarkedTombstone(reel.id)
        let item = BookmarkItem(
            reelID: reel.id,
            weekID: weekID,
            creatorHandle: reel.creatorHandle,
            caption: reel.caption,
            rank: reel.rank,
            videoUrl: reel.videoUrl,
            thumbnailUrl: reel.thumbnailUrl,
            // B3: keepBookmarkOffline flips the row to .cached after the
            // isolated copy actually lands. Claiming .cached here from a
            // transient check lies when the copy never happens.
            localStatus: .evicted,
            sizeBytes: reel.sizeBytes ?? 0
        )
        context.insert(item)
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
            // B3: always attempt the isolated copy — it has a remote
            // download branch for reels with no local file. A failed
            // copy (1.5 GB cap, disk full) honestly leaves .evicted.
            do {
                try await MediaCacheManager.shared.keepBookmarkOffline(
                    weekID: wID,
                    reelID: rID,
                    fallbackSizeBytes: sBytes
                )
            } catch {
                // Copy failed: row honestly stays .evicted.
            }
            guard !Task.isCancelled else { return }
            _ = try? await DigestDataService.shared.saveRemoteBookmark(reel: reel)
        }
    }
}
