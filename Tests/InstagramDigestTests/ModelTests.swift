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

    func testDigestManifestDedupesDuplicateIDs() throws {
        // IOS-P0-3: duplicate reel IDs crash ForEach(id:) in GridView.
        let json = """
        {
            "run_date": "2026-09-17",
            "items": [
                {"id": "dup", "creator_handle": "alice", "rank": 1,
                 "video_url": "https://pub-r2.dev/dup.mp4"},
                {"id": "ok", "creator_handle": "bob", "rank": 2,
                 "video_url": "https://pub-r2.dev/ok.mp4"},
                {"id": "dup", "creator_handle": "mallory", "rank": 3,
                 "video_url": "https://pub-r2.dev/dup2.mp4"}
            ]
        }
        """.data(using: .utf8)!

        let manifest = try JSONDecoder().decode(DigestManifest.self, from: json)
        XCTAssertEqual(manifest.items.map { $0.id }, ["dup", "ok"])
        XCTAssertEqual(manifest.items[0].creatorHandle, "alice")
    }

    func testReelItemRejectsRelativeVideoURL() throws {
        // IOS-P2-9: relative video URLs must fail decode (lossy-skipped),
        // never publish an unplayable card.
        let json = """
        {"id": "rel", "creator_handle": "alice", "rank": 1, "video_url": "foo.mp4"}
        """.data(using: .utf8)!
        XCTAssertThrowsError(try JSONDecoder().decode(ReelItem.self, from: json))
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

    func testJumpToReelPredecessorMarking() {
        let items = [
            ReelItem(id: "r0", creatorHandle: "a", caption: "", rank: 1, videoUrl: URL(string: "https://example.com/0.mp4")!),
            ReelItem(id: "r1", creatorHandle: "b", caption: "", rank: 2, videoUrl: URL(string: "https://example.com/1.mp4")!),
            ReelItem(id: "r2", creatorHandle: "c", caption: "", rank: 3, videoUrl: URL(string: "https://example.com/2.mp4")!),
            ReelItem(id: "r3", creatorHandle: "d", caption: "", rank: 4, videoUrl: URL(string: "https://example.com/3.mp4")!)
        ]

        // 1. Target 0: no predecessors
        let atZero = WatchedRules.unrecordedPredecessorIDs(items: items, targetIndex: 0, alreadyWatched: [])
        XCTAssertTrue(atZero.isEmpty)

        // 2. Target 2 with none already watched: marks r0, r1
        let atTwo = WatchedRules.unrecordedPredecessorIDs(items: items, targetIndex: 2, alreadyWatched: [])
        XCTAssertEqual(atTwo, ["r0", "r1"])

        // 3. Partial overlap: r0 is already watched, target is 3: marks r1, r2
        let partial = WatchedRules.unrecordedPredecessorIDs(items: items, targetIndex: 3, alreadyWatched: ["r0"])
        XCTAssertEqual(partial, ["r1", "r2"])

        // 4. All predecessors already watched: returns empty
        let allWatched = WatchedRules.unrecordedPredecessorIDs(items: items, targetIndex: 2, alreadyWatched: ["r0", "r1"])
        XCTAssertTrue(allWatched.isEmpty)

        // 5. Clamped beyond count
        let beyond = WatchedRules.unrecordedPredecessorIDs(items: items, targetIndex: 10, alreadyWatched: [])
        XCTAssertEqual(beyond, ["r0", "r1", "r2", "r3"])
    }

    func testUnwatchedOnlyBatchFilter() {
        let items = [
            ReelItem(id: "r0", creatorHandle: "a", caption: "", rank: 1, videoUrl: URL(string: "https://example.com/0.mp4")!),
            ReelItem(id: "r1", creatorHandle: "b", caption: "", rank: 2, videoUrl: URL(string: "https://example.com/1.mp4")!),
            ReelItem(id: "r2", creatorHandle: "c", caption: "", rank: 3, videoUrl: URL(string: "https://example.com/2.mp4")!)
        ]
        XCTAssertEqual(WatchedRules.unwatchedItems(items: items, alreadyWatched: []).map(\.id), ["r0", "r1", "r2"])
        XCTAssertEqual(WatchedRules.unwatchedItems(items: items, alreadyWatched: ["r0", "r2"]).map(\.id), ["r1"])
        XCTAssertTrue(WatchedRules.unwatchedItems(items: items, alreadyWatched: ["r0", "r1", "r2"]).isEmpty)
    }

    func testBookmarkRemoteDTODecoding() throws {
        let json = """
        [
            {
                "id": "DdE1YCxskjU",
                "creator_handle": "foundmyfitness",
                "caption": "Sauna post-resistance training",
                "category": "health",
                "thumbnail_url": "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/DdE1YCxskjU_portrait.jpg",
                "video_url": "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/DdE1YCxskjU.mp4",
                "size_bytes": 5408339,
                "bookmarked_at": "2026-09-14T13:12:42.719Z",
                "telegram_message_id": 10
            }
        ]
        """.data(using: .utf8)!

        let dtos = try JSONDecoder().decode([BookmarkRemoteDTO].self, from: json)
        XCTAssertEqual(dtos.count, 1)
        XCTAssertEqual(dtos[0].id, "DdE1YCxskjU")
        XCTAssertEqual(dtos[0].creatorHandle, "foundmyfitness")
        XCTAssertEqual(dtos[0].category, "health")
        XCTAssertEqual(dtos[0].sizeBytes, 5408339)
    }

    func test300ReelsBundledManifestDecoding() throws {
        // Anchor to this file's compile-time path: unit-test CWD is derived-data, not the repo root,
        // so a relative "Resources/data.json" never resolves and the test would vacuously pass.
        let thisFile = URL(fileURLWithPath: #filePath)
        let repoRoot = thisFile.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let fileURL = repoRoot.appendingPathComponent("Resources/data.json")
        guard let data = try? Data(contentsOf: fileURL) else {
            throw XCTSkip("Bundled data.json not found at \(fileURL.path)")
        }
        let manifest = try JSONDecoder().decode(DigestManifest.self, from: data)
        XCTAssertGreaterThanOrEqual(manifest.items.count, 100, "Bundled manifest must decode a healthy volume of reels")
        XCTAssertEqual(manifest.items.count, manifest.count, "Every bundled reel must decode")
    }

    // MARK: - Resume at Last Active Reel & Weekly Rollover Rules

    func testResolveResumeIndexRule() {
        let items = [
            ReelItem(id: "r0", creatorHandle: "a", caption: "", rank: 1, videoUrl: URL(string: "https://example.com/0.mp4")!),
            ReelItem(id: "r1", creatorHandle: "b", caption: "", rank: 2, videoUrl: URL(string: "https://example.com/1.mp4")!),
            ReelItem(id: "r2", creatorHandle: "c", caption: "", rank: 3, videoUrl: URL(string: "https://example.com/2.mp4")!),
            ReelItem(id: "r3", creatorHandle: "d", caption: "", rank: 4, videoUrl: URL(string: "https://example.com/3.mp4")!)
        ]

        // 1. Weekly rollover: previous week ("2026-09-07") != current week ("2026-09-14") -> must reset to 0 (Reel 1)
        let rolloverIndex = WatchedRules.resolveResumeIndex(
            currentWeekID: "2026-09-14",
            previousWeekID: "2026-09-07",
            savedReelID: "r2",
            savedIndex: 2,
            items: items
        )
        XCTAssertEqual(rolloverIndex, 0, "Weekly rollover must start with Reel 1 (index 0)")

        // 2. Same week: matching savedReelID -> resolves to matching reel index
        let sameWeekMatchID = WatchedRules.resolveResumeIndex(
            currentWeekID: "2026-09-14",
            previousWeekID: "2026-09-14",
            savedReelID: "r2",
            savedIndex: nil,
            items: items
        )
        XCTAssertEqual(sameWeekMatchID, 2, "Same-week launch must resume at saved reel index")

        // 3. Same week: savedReelID not found or nil, valid savedIndex fallback
        let sameWeekFallbackIndex = WatchedRules.resolveResumeIndex(
            currentWeekID: "2026-09-14",
            previousWeekID: "2026-09-14",
            savedReelID: nil,
            savedIndex: 3,
            items: items
        )
        XCTAssertEqual(sameWeekFallbackIndex, 3, "Same-week launch must fallback to savedIndex")

        // 4. Out-of-bounds savedIndex falls back to 0
        let outOfBoundsIndex = WatchedRules.resolveResumeIndex(
            currentWeekID: "2026-09-14",
            previousWeekID: "2026-09-14",
            savedReelID: nil,
            savedIndex: 99,
            items: items
        )
        XCTAssertEqual(outOfBoundsIndex, 0, "Out-of-bounds savedIndex must fallback to 0")

        // 5. Empty items array returns 0 safely
        let emptyItemsIndex = WatchedRules.resolveResumeIndex(
            currentWeekID: "2026-09-14",
            previousWeekID: "2026-09-14",
            savedReelID: "r2",
            savedIndex: 2,
            items: []
        )
        XCTAssertEqual(emptyItemsIndex, 0, "Empty items array must return 0")

        // 6. Saved reel ID wins over a conflicting saved index (reordered list)
        let idPrecedence = WatchedRules.resolveResumeIndex(
            currentWeekID: "2026-09-14",
            previousWeekID: "2026-09-14",
            savedReelID: "r1",
            savedIndex: 3,
            items: items
        )
        XCTAssertEqual(idPrecedence, 1, "savedReelID match must take precedence over savedIndex")

        // 7. Negative saved index falls back to 0
        let negativeIndex = WatchedRules.resolveResumeIndex(
            currentWeekID: "2026-09-14",
            previousWeekID: "2026-09-14",
            savedReelID: nil,
            savedIndex: -1,
            items: items
        )
        XCTAssertEqual(negativeIndex, 0, "Negative savedIndex must fallback to 0")

        // 8. First launch (no previous week, no saved state) starts at Reel 1
        let firstLaunch = WatchedRules.resolveResumeIndex(
            currentWeekID: "2026-09-14",
            previousWeekID: nil,
            savedReelID: nil,
            savedIndex: nil,
            items: items
        )
        XCTAssertEqual(firstLaunch, 0, "First launch must start with Reel 1 (index 0)")
    }
}
