import XCTest
@testable import InstagramDigest

final class ModelTests: XCTestCase {

    func testReelItemDecoding() throws {
        let json = """
        {
            "id": "reel_101",
            "creator_handle": "mkbhd",
            "caption": "New phone impressions",
            "rank": 1,
            "video_url": "https://pub-r2.dev/videos/2026-09-06/reel_101.mp4",
            "thumbnail": "https://pub-r2.dev/thumbnails/reel_101.jpg",
            "size_bytes": 15400000
        }
        """.data(using: .utf8)!

        let item = try JSONDecoder().decode(ReelItem.self, from: json)
        XCTAssertEqual(item.id, "reel_101")
        XCTAssertEqual(item.creatorHandle, "mkbhd")
        XCTAssertEqual(item.rank, 1)
        XCTAssertEqual(item.rankDisplay, "#01")
        XCTAssertEqual(item.sizeBytes, 15400000)
    }

    func testDigestManifestLossyDecoding() throws {
        let json = """
        {
            "run_date": "2026-09-17",
            "items": [
                {
                    "id": "reel_01",
                    "creator_handle": "alice",
                    "caption": "Valid reel",
                    "rank": 1,
                    "video_url": "https://pub-r2.dev/reel_01.mp4"
                },
                {
                    "id": "corrupt_reel",
                    "invalid_missing_video": 123
                },
                {
                    "id": "reel_02",
                    "creator_handle": "bob",
                    "caption": "Another valid reel",
                    "rank": 2,
                    "video_url": "https://pub-r2.dev/reel_02.mp4"
                }
            ],
            "generated_at": "2026-09-17T08:30:00.123Z"
        }
        """.data(using: .utf8)!

        let manifest = try JSONDecoder().decode(DigestManifest.self, from: json)
        XCTAssertEqual(manifest.weekId, "2026-09-17")
        // Lossy decoding must preserve reel_01 and reel_02 despite corrupt_reel
        XCTAssertEqual(manifest.items.count, 2)
        XCTAssertEqual(manifest.items[0].id, "reel_01")
        XCTAssertEqual(manifest.items[1].id, "reel_02")
        XCTAssertNotNil(manifest.generatedAt)
    }

    func testBookmarkItemStatusAndURL() {
        let item = BookmarkItem(
            reelID: "reel_01",
            weekID: "2026-09-17",
            creatorHandle: "alice",
            caption: "Hello",
            rank: 1,
            videoUrl: URL(string: "https://example.com/reel.mp4")!,
            localStatus: .cached,
            sizeBytes: 1000
        )

        XCTAssertEqual(item.localStatus, .cached)
        item.localStatus = .evicted
        XCTAssertEqual(item.localStatus, .evicted)
        XCTAssertEqual(item.videoUrl?.absoluteString, "https://example.com/reel.mp4")
    }

    func testWatchedEventCompoundKey() {
        let event = WatchedEvent(reelID: "reel_01", weekID: "2026-09-17")
        XCTAssertEqual(event.compoundKey, "2026-09-17\u{1F}reel_01")
    }
}
