import XCTest
import SwiftData
@testable import InstagramDigest

/// Regression collateral for the deep-audit fixes: lossy remote-bookmark
/// decoding, duplicate-safe bookmark sync, and the mindful product constant.
final class AuditFixesTests: XCTestCase {

    // MARK: - Lossy remote bookmarks manifest

    func testLossyBookmarkListSkipsMalformedEntry() throws {
        let json = """
        [
            {
                "id": "good_1",
                "creator_handle": "alice",
                "caption": "Valid bookmark",
                "video_url": "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/good_1.mp4",
                "size_bytes": 5408339
            },
            {
                "id": "broken_no_url",
                "creator_handle": "mallory",
                "caption": "Missing video_url entirely"
            },
            {
                "id": "good_2",
                "creator_handle": "bob",
                "caption": "Also valid",
                "video_url": "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/good_2.mp4"
            }
        ]
        """.data(using: .utf8)!

        let dtos = try LossyBookmarkList.decode(from: json)
        XCTAssertEqual(dtos.map { $0.id }, ["good_1", "good_2"])
    }

    func testLossyBookmarkListToleratesMalformedFields() throws {
        // Wrong-typed size_bytes and camelCase keys must not kill the entry.
        let json = """
        [
            {
                "id": "flex_1",
                "creatorHandle": "carol",
                "videoUrl": "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/flex_1.mp4",
                "sizeBytes": "not-a-number"
            }
        ]
        """.data(using: .utf8)!

        let dtos = try LossyBookmarkList.decode(from: json)
        XCTAssertEqual(dtos.count, 1)
        XCTAssertEqual(dtos[0].creatorHandle, "carol")
        XCTAssertNil(dtos[0].sizeBytes)
    }

    func testLossyBookmarkListAcceptsWrappedObjectShape() throws {
        let json = """
        {
            "bookmarks": [
                {
                    "id": "wrapped_1",
                    "creator_handle": "dave",
                    "caption": "Wrapped shape",
                    "video_url": "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/wrapped_1.mp4"
                }
            ]
        }
        """.data(using: .utf8)!

        let dtos = try LossyBookmarkList.decode(from: json)
        XCTAssertEqual(dtos.map { $0.id }, ["wrapped_1"])
    }

    func testLossyBookmarkListSkipsBadEntryInWrappedShape() throws {
        // IOS-P1-9: one malformed entry in the wrapped shape must not wipe the list.
        let json = """
        {
            "bookmarks": [
                {
                    "id": "w_good",
                    "creator_handle": "erin",
                    "video_url": "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/w_good.mp4"
                },
                {"id": "w_bad", "creator_handle": "mallory"},
                {
                    "id": "w_good2",
                    "creator_handle": "frank",
                    "video_url": "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/w_good2.mp4"
                }
            ]
        }
        """.data(using: .utf8)!

        let dtos = try LossyBookmarkList.decode(from: json)
        XCTAssertEqual(dtos.map { $0.id }, ["w_good", "w_good2"])
    }

    func testBookmarkDTORejectsRelativeVideoURL() throws {
        // IOS-P2-9: a relative bookmark video URL invalidates the entry; the
        // lossy list skips it (empty result, no throw for array shapes).
        let json = """
        [{"id": "rel_bm", "creator_handle": "alice", "video_url": "foo.mp4"}]
        """.data(using: .utf8)!
        let dtos = try LossyBookmarkList.decode(from: json)
        XCTAssertTrue(dtos.isEmpty)
    }

    // MARK: - Duplicate-safe bookmark sync

    func testSyncRemoteBookmarksDedupesRepeatedIDs() async throws {
        let schema = Schema([BookmarkItem.self])
        let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])

        let manager = MediaCacheManager()
        await manager.setModelContainer(container)

        let dtos = [
            BookmarkRemoteDTO(
                id: "dup_reel",
                creatorHandle: "first",
                caption: "first seen",
                videoUrl: URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/dup_reel.mp4")!
            ),
            BookmarkRemoteDTO(
                id: "dup_reel",
                creatorHandle: "second",
                caption: "second seen",
                videoUrl: URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/dup_reel.mp4")!
            )
        ]
        await manager.syncRemoteBookmarks(dtos: dtos)

        let context = ModelContext(container)
        let fetched = try context.fetch(FetchDescriptor<BookmarkItem>())
        let matches = fetched.filter { $0.reelID == "dup_reel" }
        XCTAssertEqual(matches.count, 1, "Duplicate incoming IDs must collapse to a single row, never crash")
        XCTAssertEqual(matches.first?.creatorHandle, "second")
    }

    // MARK: - Mindful product constant

    func testMindfulDailyLimitIsSingleConstant() {
        XCTAssertEqual(MindfulDailyModalView.dailyLimit, 50)
        XCTAssertEqual(MindfulDailyModalView(viewedCount: MindfulDailyModalView.dailyLimit,
                                             onTakeABreak: {}, onSnoozeForToday: {}, onDismiss: {}).viewedCount, 50)
    }

    // MARK: - Disk size helper

    func testDiskFileSizeHelper() {
        XCTAssertNil(MediaCacheManager.diskFileSize(atPath: "/nonexistent/path/reel.mp4"))
    }
}
