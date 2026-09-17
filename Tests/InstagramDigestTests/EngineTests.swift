import XCTest
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
}
