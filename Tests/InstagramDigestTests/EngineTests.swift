import XCTest
import SwiftUI
import AVFoundation
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
        // Contract (UAT-4): real sizes summed, no artificial headroom. 10 MB + 20 MB.
        XCTAssertEqual(required, 30_000_000)
    }

    @MainActor
    func testPreflightStorageCountsOnlyPendingReels() {
        let coordinator = DownloadAllCoordinator.shared
        // Unknown sizes fall back to the ~7.5 MB average reel size each.
        let reels = [
            ReelItem(id: "pending_u1", creatorHandle: "c", caption: "", rank: 1, videoUrl: URL(string: "https://example.com/u1.mp4")!),
            ReelItem(id: "pending_u2", creatorHandle: "c", caption: "", rank: 2, videoUrl: URL(string: "https://example.com/u2.mp4")!)
        ]

        let (_, required, _) = coordinator.preflightStorage(reels: reels, weekID: "test_week_no_files_xyz")
        XCTAssertEqual(required, 15_000_000)

        // Fully cached batch must require zero bytes, never a full re-measure.
        let empty = coordinator.preflightStorage(reels: [], weekID: "test_week_no_files_xyz")
        XCTAssertEqual(empty.requiredBytes, 0)
        XCTAssertTrue(empty.isSufficient)
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

    // MARK: - Resume pending-index lifecycle (launch-at-last-reel fix)

    @MainActor
    func testPendingInitialScrollRefreshesOnListChange() {
        let layout = UICollectionViewFlowLayout()
        let vc = FeedCollectionViewController(collectionViewLayout: layout)
        vc.loadViewIfNeeded()

        let full = (0..<5).map {
            ReelItem(id: "r\($0)", creatorHandle: "c", caption: "", rank: $0 + 1, videoUrl: URL(string: "https://example.com/\($0).mp4")!)
        }
        let filtered = (0..<2).map {
            ReelItem(id: "f\($0)", creatorHandle: "c", caption: "", rank: $0 + 1, videoUrl: URL(string: "https://example.com/f\($0).mp4")!)
        }

        // Launch parks pending at the resume target…
        vc.updateReelsIfNeeded(full, targetIndex: 4)
        XCTAssertEqual(vc.pendingInitialScrollIndex, 4)

        // …but a category switch to target 0 must not leave the stale 4 behind.
        vc.updateReelsIfNeeded(filtered, targetIndex: 0)
        XCTAssertEqual(vc.pendingInitialScrollIndex, 0, "Stale pending index must be refreshed on list change")
    }

    @MainActor
    func testPendingInitialScrollClampsOutOfRangeIndex() {
        let reels = (0..<3).map {
            ReelItem(id: "r\($0)", creatorHandle: "c", caption: "", rank: $0 + 1, videoUrl: URL(string: "https://example.com/\($0).mp4")!)
        }
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

        let layout = UICollectionViewFlowLayout()
        layout.scrollDirection = .vertical
        layout.minimumLineSpacing = 0
        let vc = FeedCollectionViewController(collectionViewLayout: layout)
        vc.coordinator = coordinator
        coordinator.viewController = vc
        vc.loadViewIfNeeded()
        vc.collectionView.dataSource = coordinator
        vc.collectionView.delegate = coordinator
        vc.collectionView.frame = CGRect(x: 0, y: 0, width: 390, height: 844)
        vc.collectionView.layoutIfNeeded()

        vc.updateReelsIfNeeded(reels, targetIndex: 0)
        vc.collectionView.layoutIfNeeded()
        // Simulate a stale oversized pending (e.g. resume 15 vs a 3-item filter).
        vc.pendingInitialScrollIndex = 99
        vc.viewDidLayoutSubviews()

        XCTAssertNil(vc.pendingInitialScrollIndex, "Pending marker must be consumed by layout")
        XCTAssertEqual(vc.collectionView.contentOffset.y, CGFloat(2 * 844), accuracy: 1.0,
                       "Out-of-range pending must clamp to the last page, never strand beyond content")
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

    @MainActor
    func testSlotRotationForwardContinuous() async throws {
        let pool = AVPlayerPool.shared
        let reels = [
            ReelItem(id: "r0", creatorHandle: "c0", caption: "Caption 0", rank: 1, videoUrl: URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/videos/2026-09-14/01_test_r0.mp4")!),
            ReelItem(id: "r1", creatorHandle: "c1", caption: "Caption 1", rank: 2, videoUrl: URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/videos/2026-09-14/02_test_r1.mp4")!),
            ReelItem(id: "r2", creatorHandle: "c2", caption: "Caption 2", rank: 3, videoUrl: URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/videos/2026-09-14/03_test_r2.mp4")!)
        ]

        pool.setReels(reels, weekID: "test_week_rot", startIndex: 0)
        XCTAssertEqual(pool.currentIndex, 0)
        XCTAssertEqual(pool.slotCurrent.slotItem?.reel.id, "r0")

        // Wait brief moment for async load of next slot
        try await Task.sleep(nanoseconds: 50_000_000)

        // Advance to 1
        let prevNextSlot = pool.slotNext
        pool.setCurrentIndex(1)

        XCTAssertEqual(pool.currentIndex, 1)
        // If slotNext was populated, it rotated into slotCurrent
        if prevNextSlot.slotItem?.reel.id == "r1" {
            XCTAssertTrue(pool.slotCurrent === prevNextSlot, "slotNext must be promoted to slotCurrent without teardown")
            XCTAssertEqual(pool.slotCurrent.slotItem?.reel.id, "r1")
        }
    }

    func testRemoteBookmarkPayloadEncoding() throws {
        let reel = ReelItem(
            id: "test1234",
            creatorHandle: "apple_creator",
            caption: "Amazing reel #test",
            rank: 1,
            videoUrl: URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/videos/2026-09-14/01_creator_test1234.mp4")!,
            thumbnailUrl: URL(string: "https://vkr1729.github.io/Instagram_digest/thumbnails/test1234.jpg"),
            category: "ai_tech"
        )

        let payload = DigestDataService.BookmarkPayload(reel: reel)
        let data = try JSONEncoder().encode(payload)
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]

        XCTAssertEqual(json?["id"] as? String, "test1234")
        XCTAssertEqual(json?["video_url"] as? String, "https://instagram-digest-media.kedarvreddy.workers.dev/videos/2026-09-14/01_creator_test1234.mp4")
        XCTAssertEqual(json?["thumbnail_url"] as? String, "https://vkr1729.github.io/Instagram_digest/thumbnails/test1234.jpg")
        XCTAssertEqual(json?["creator_handle"] as? String, "apple_creator")
        XCTAssertEqual(json?["caption"] as? String, "Amazing reel #test")
        XCTAssertEqual(json?["category"] as? String, "ai_tech")
    }

    @MainActor
    func testAttachAppropriateSlotDirectly() {
        let layout = UICollectionViewFlowLayout()
        let vc = FeedCollectionViewController(collectionViewLayout: layout)
        let reels = [
            ReelItem(id: "r0", creatorHandle: "c", caption: "", rank: 1, videoUrl: URL(string: "https://example.com/0.mp4")!),
            ReelItem(id: "r1", creatorHandle: "c", caption: "", rank: 2, videoUrl: URL(string: "https://example.com/1.mp4")!)
        ]
        vc.updateReelsIfNeeded(reels, targetIndex: 0)

        let cell = FeedCell(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        XCTAssertFalse(cell.playerContainerView?.isHidden ?? true, "playerContainerView must not be hidden initially")

        vc.attachAppropriateSlot(to: cell, at: 0)
        XCTAssertFalse(cell.playerContainerView?.isHidden ?? true, "playerContainerView must remain visible")
        XCTAssertTrue(cell.playerContainerView?.playerLayer.player === AVPlayerPool.shared.slotCurrent.player)
    }

    @MainActor
    func testLayerDetachmentEnforcesExclusiveOwnership() {
        let pool = AVPlayerPool.shared
        let layer1 = AVPlayerLayer()
        let layer2 = AVPlayerLayer()

        pool.attachLayer(layer1, to: pool.slotCurrent)
        XCTAssertTrue(pool.slotCurrent.playerLayer === layer1)
        XCTAssertTrue(layer1.player === pool.slotCurrent.player)

        // Attaching layer2 to slotCurrent must detach layer1
        pool.attachLayer(layer2, to: pool.slotCurrent)
        XCTAssertTrue(pool.slotCurrent.playerLayer === layer2)
        XCTAssertNil(layer1.player, "Previous layer must have player set to nil")

        // Detaching layer2
        pool.detachLayer(layer2)
        XCTAssertNil(pool.slotCurrent.playerLayer)
        XCTAssertNil(layer2.player)
    }

    @MainActor
    func testSlotRotationBackwardContinuous() async throws {
        let pool = AVPlayerPool.shared
        let reels = [
            ReelItem(id: "b0", creatorHandle: "c0", caption: "Caption 0", rank: 1, videoUrl: URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/videos/2026-09-14/01_test_b0.mp4")!),
            ReelItem(id: "b1", creatorHandle: "c1", caption: "Caption 1", rank: 2, videoUrl: URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/videos/2026-09-14/02_test_b1.mp4")!)
        ]

        pool.setReels(reels, weekID: "test_week_back", startIndex: 1)
        XCTAssertEqual(pool.currentIndex, 1)

        try await Task.sleep(nanoseconds: 50_000_000)

        let prevSlot = pool.slotPrev
        pool.setCurrentIndex(0)
        XCTAssertEqual(pool.currentIndex, 0)

        if prevSlot.slotItem?.reel.id == "b0" {
            XCTAssertTrue(pool.slotCurrent === prevSlot, "slotPrev must be promoted to slotCurrent without teardown")
            XCTAssertEqual(pool.slotCurrent.slotItem?.reel.id, "b0")
        }
    }
}
