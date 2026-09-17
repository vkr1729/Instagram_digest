import XCTest
import SwiftUI
@testable import InstagramDigest

final class EngineTests: XCTestCase {

    func testLivePinSetIncludesActivePoolReels() async {
        let manager = MediaCacheManager.shared
        await manager.setActiveVideoPoolReelIDs(["reel_active_1", "reel_active_2"])

        let pinSet = await manager.computeLivePinSet()
        XCTAssertTrue(pinSet.contains("reel_active_1"))
        XCTAssertTrue(pinSet.contains("reel_active_2"))
    }

    func testOversizedBookmarkAdmissionRejected() async {
        let manager = MediaCacheManager.shared
        // An incoming file larger than 1.5 GB must be pre-rejected without evicting existing items
        do {
            try await manager.ensureSpaceForBookmark(incomingBytes: 2_000_000_000)
            XCTFail("Should have thrown insufficientStorage")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("1.5 GB"))
        }
    }

    @MainActor
    func testPreflightStorageCalculation() {
        let coordinator = DownloadAllCoordinator.shared
        let reels = [
            ReelItem(id: "r1", creatorHandle: "c", caption: "", rank: 1, videoUrl: URL(string: "https://example.com/1.mp4")!, sizeBytes: 10_000_000),
            ReelItem(id: "r2", creatorHandle: "c", caption: "", rank: 2, videoUrl: URL(string: "https://example.com/2.mp4")!, sizeBytes: 20_000_000)
        ]

        let (_, required, _) = coordinator.preflightStorage(reels: reels)
        // Expected: 30 MB + 1.0 GB headroom = 1_030_000_000 bytes
        XCTAssertEqual(required, 1_030_000_000)
    }

    func testLivePinSetGenerationFencing() async {
        let manager = MediaCacheManager.shared
        await manager.setActiveVideoPoolReelIDs(["new_reel"], generation: 2)
        // Stale task with generation 1 finishes late: should be rejected
        await manager.setActiveVideoPoolReelIDs(["stale_reel"], generation: 1)

        let pinSet = await manager.computeLivePinSet()
        XCTAssertTrue(pinSet.contains("new_reel"))
        XCTAssertFalse(pinSet.contains("stale_reel"))
    }

    @MainActor
    func testDownloadAllCoordinatorCancelResetsSuspension() {
        let coordinator = DownloadAllCoordinator.shared
        coordinator.suspendQueue()
        XCTAssertTrue(coordinator.isSuspended)

        coordinator.cancelAll()
        XCTAssertFalse(coordinator.isSuspended)
        XCTAssertEqual(coordinator.state, .idle)
    }

    @MainActor
    func testCellRegistrationDequeueReuse() {
        let reels = [
            ReelItem(id: "test_reel_1", creatorHandle: "mkbhd", caption: "Test 1", rank: 1, videoUrl: URL(string: "https://example.com/1.mp4")!),
            ReelItem(id: "test_reel_2", creatorHandle: "veritasium", caption: "Test 2", rank: 2, videoUrl: URL(string: "https://example.com/2.mp4")!)
        ]
        var currentIndex = 0
        let binding = Binding<Int>(get: { currentIndex }, set: { currentIndex = $0 })
        let pagerView = FeedPagerView(
            reels: reels,
            currentIndex: binding,
            onPageChanged: { _ in },
            onScrollEnded: { _ in },
            onForwardScrollPast: { _ in }
        )
        let coordinator = FeedPagerView.Coordinator(pagerView)
        XCTAssertNotNil(coordinator.cellRegistration, "Cell registration must be eagerly instantiated up-front")

        let layout = UICollectionViewFlowLayout()
        let collectionView = UICollectionView(frame: CGRect(x: 0, y: 0, width: 390, height: 844), collectionViewLayout: layout)
        collectionView.dataSource = coordinator

        // Dequeue item 0: must succeed and not throw NSInternalInconsistencyException
        let cell0 = coordinator.collectionView(collectionView, cellForItemAt: IndexPath(item: 0, section: 0))
        XCTAssertNotNil(cell0)
        XCTAssertTrue(cell0 is FeedCell)

        // Dequeue item 1: demonstrates registration is shared across dequeues without recreation
        let cell1 = coordinator.collectionView(collectionView, cellForItemAt: IndexPath(item: 1, section: 0))
        XCTAssertNotNil(cell1)
        XCTAssertTrue(cell1 is FeedCell)
    }

    @MainActor
    func testIdleCoordinatorSurvivesPlaybackToggleWithoutLeavingIdle() async throws {
        let coordinator = DownloadAllCoordinator.shared
        let pool = AVPlayerPool.shared

        // Reset to known clean state
        coordinator.cancelAll()
        pool.isPlaying = false

        XCTAssertEqual(coordinator.state, .idle)
        XCTAssertEqual(coordinator.overallProgress, 0.0)

        // Toggle playback: playing = true
        pool.isPlaying = true
        try await Task.sleep(nanoseconds: 100_000_000) // 100ms for Combine delivery on main queue

        XCTAssertEqual(coordinator.state, .idle, "Coordinator must stay .idle when playback starts")
        XCTAssertEqual(coordinator.overallProgress, 0.0)

        // Toggle playback: playing = false
        pool.isPlaying = false
        try await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertEqual(coordinator.state, .idle, "Coordinator must stay .idle when playback stops")
        XCTAssertEqual(coordinator.overallProgress, 0.0)
    }
}
