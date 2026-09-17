import XCTest

/// Deep Automated User Acceptance Testing (UAT) suite for Instagram Digest.
/// Exercises critical user workflows: Feed playback, Speed cycler, Jump Grid,
/// Bulk download preflight, Bookmarks storage gauge, Spatial gestures, and Feed paging.
final class InstagramDigestUITests: XCTestCase {

    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-ui-testing"]
        app.launch()
    }

    override func tearDownWithError() throws {
        app = nil
    }

    // MARK: - 1. App Launch & Feed Initialization

    func testAppLaunchAndFeedInitialization() throws {
        // Verify primary header controls are present
        let speedButton = app.buttons["SpeedButton"]
        let gridButton = app.buttons["GridButton"]
        let downloadButton = app.buttons["DownloadAllButton"]
        let bookmarksButton = app.buttons["BookmarksButton"]

        XCTAssertTrue(speedButton.waitForExistence(timeout: 8.0), "SpeedButton must appear on app launch")
        XCTAssertTrue(gridButton.exists, "GridButton must exist")
        XCTAssertTrue(downloadButton.exists, "DownloadAllButton must exist")
        XCTAssertTrue(bookmarksButton.exists, "BookmarksButton must exist")

        // Verify initial playback speed default is 1.25x per architecture spec
        XCTAssertTrue(speedButton.label.contains("1.25x"), "Default speed must be 1.25x, got: \(speedButton.label)")

        // Verify first reel metadata loaded from bundle
        let creatorHandle = app.staticTexts["ReelCreatorHandle"]
        let rankBadge = app.staticTexts["ReelRankBadge"]

        XCTAssertTrue(creatorHandle.waitForExistence(timeout: 5.0), "Creator handle must appear")
        XCTAssertTrue(creatorHandle.label.contains("mkbhd"), "First creator should be mkbhd, got: \(creatorHandle.label)")
        XCTAssertTrue(rankBadge.label.contains("#01"), "First reel should be rank #01, got: \(rankBadge.label)")
    }

    // MARK: - 2. Playback Speed Cycler Pill

    func testPlaybackSpeedCyclerPill() throws {
        let speedButton = app.buttons["SpeedButton"]
        XCTAssertTrue(speedButton.waitForExistence(timeout: 8.0))
        XCTAssertTrue(speedButton.label.contains("1.25x"))

        // Tap 1: 1.25x -> 1.50x
        speedButton.tap()
        XCTAssertTrue(speedButton.label.contains("1.50x"), "Speed should advance to 1.50x, got: \(speedButton.label)")

        // Tap 2: 1.50x -> 2.00x
        speedButton.tap()
        XCTAssertTrue(speedButton.label.contains("2.00x"), "Speed should advance to 2.00x, got: \(speedButton.label)")

        // Tap 3: 2.00x -> 1.00x (wrap-around)
        speedButton.tap()
        XCTAssertTrue(speedButton.label.contains("1.00x"), "Speed should cycle back to 1.00x, got: \(speedButton.label)")

        // Tap 4: 1.00x -> 1.25x
        speedButton.tap()
        XCTAssertTrue(speedButton.label.contains("1.25x"), "Speed should advance to 1.25x, got: \(speedButton.label)")
    }

    // MARK: - 3. Jump Grid Navigation

    func testJumpGridNavigationAndSelection() throws {
        let gridButton = app.buttons["GridButton"]
        XCTAssertTrue(gridButton.waitForExistence(timeout: 8.0))

        gridButton.tap()

        // Verify Grid sheet is presented
        let gridTitle = app.navigationBars["All Reels (4)"]
        XCTAssertTrue(gridTitle.waitForExistence(timeout: 5.0), "Grid navigation title 'All Reels (4)' should appear")

        // Select reel index 1 (@hubermanlab)
        let reelItem1 = app.buttons["GridReelItem_1"]
        XCTAssertTrue(reelItem1.waitForExistence(timeout: 3.0), "Grid item 1 should be selectable")
        reelItem1.tap()

        // Verify sheet dismissed and current reel jumped to #02
        let creatorHandle = app.staticTexts["ReelCreatorHandle"]
        XCTAssertTrue(creatorHandle.waitForExistence(timeout: 5.0))
        XCTAssertTrue(creatorHandle.label.contains("hubermanlab"), "Current reel should be hubermanlab, got: \(creatorHandle.label)")

        let rankBadge = app.staticTexts["ReelRankBadge"]
        XCTAssertTrue(rankBadge.label.contains("#02"), "Rank should be #02, got: \(rankBadge.label)")
    }

    // MARK: - 4. Download All Sheet & Preflight

    func testDownloadAllSheetAndPreflight() throws {
        let downloadButton = app.buttons["DownloadAllButton"]
        XCTAssertTrue(downloadButton.waitForExistence(timeout: 8.0))

        downloadButton.tap()

        // Verify Download All sheet title
        let sheetTitle = app.navigationBars["Download All"]
        XCTAssertTrue(sheetTitle.waitForExistence(timeout: 5.0), "Download All navigation title should appear")

        // Verify storage preflight details
        let startButton = app.buttons["StartDownloadButton"]
        XCTAssertTrue(startButton.waitForExistence(timeout: 3.0), "Start Download button should exist")
        XCTAssertTrue(startButton.isEnabled, "Start Download button should be enabled")

        // Close sheet
        let closeButton = app.buttons["DownloadCloseButton"]
        XCTAssertTrue(closeButton.exists, "Close button should exist in toolbar")
        closeButton.tap()

        // Verify back on main feed
        XCTAssertTrue(downloadButton.waitForExistence(timeout: 3.0), "Main feed controls should be active after dismiss")
    }

    // MARK: - 5. Bookmarks Sheet & Storage Gauge

    func testBookmarksSheetEmptyStateAndDismiss() throws {
        let bookmarksButton = app.buttons["BookmarksButton"]
        XCTAssertTrue(bookmarksButton.waitForExistence(timeout: 8.0))

        bookmarksButton.tap()

        // Verify Bookmarks navigation bar
        let bookmarksTitle = app.navigationBars["Saved Bookmarks"]
        XCTAssertTrue(bookmarksTitle.waitForExistence(timeout: 5.0), "Saved Bookmarks should appear in navigation")

        // Verify empty state text
        let emptyText = app.staticTexts["BookmarksEmptyStateText"]
        XCTAssertTrue(emptyText.waitForExistence(timeout: 3.0), "BookmarksEmptyStateText should appear")

        // Verify 0.0 MB / 1.5 GB gauge label
        let storageGauge = app.staticTexts["StorageGaugeLabel"]
        XCTAssertTrue(storageGauge.waitForExistence(timeout: 3.0), "StorageGaugeLabel should appear")
        XCTAssertTrue(storageGauge.label.contains("0.0 MB / 1.5 GB"), "Storage gauge should read 0.0 MB / 1.5 GB, got: \(storageGauge.label)")

        // Verify Free Storage button is disabled when empty
        let freeStorageButton = app.buttons["FreeStorageButton"]
        XCTAssertTrue(freeStorageButton.exists, "Free Storage button should exist")
        XCTAssertFalse(freeStorageButton.isEnabled, "Free Storage button should be disabled when storage is 0")

        // Dismiss Bookmarks sheet
        let doneButton = app.buttons["BookmarksDoneButton"]
        XCTAssertTrue(doneButton.exists, "BookmarksDoneButton should exist")
        doneButton.tap()

        XCTAssertTrue(bookmarksButton.waitForExistence(timeout: 3.0), "Main feed controls should be active after dismiss")
    }

    // MARK: - 6. Feed Single Tap Play/Pause

    func testFeedTapPlayPauseToggle() throws {
        let feedView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(feedView.waitForExistence(timeout: 8.0))

        // First tap: pauses playback
        feedView.tap()

        // Wait past the 500ms scroll / 350ms swipe debounce guard
        Thread.sleep(forTimeInterval: 0.6)

        // Second tap: resumes playback
        feedView.tap()

        // Verify feed controls remain visible and functional
        let speedButton = app.buttons["SpeedButton"]
        XCTAssertTrue(speedButton.exists, "SpeedButton should remain visible and functional")
    }

    // MARK: - 7. Spatial Gestures: Long Press Bookmark Zone

    func testBookmarkSpatialZoneToggle() throws {
        let feedView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(feedView.waitForExistence(timeout: 8.0))

        // Center-Lower Bookmark zone: normX in [0.35, 0.65], normY > 0.65
        let centerLowerCoord = feedView.coordinate(withNormalizedOffset: CGVector(dx: 0.50, dy: 0.75))
        centerLowerCoord.press(forDuration: 0.7)

        // Verify bookmark indicator appears
        let bookmarkIndicator = app.images["BookmarkIndicator"]
        XCTAssertTrue(bookmarkIndicator.waitForExistence(timeout: 3.0), "BookmarkIndicator should appear after bookmarking")

        // Open Bookmarks sheet to verify persistence
        let bookmarksButton = app.buttons["BookmarksButton"]
        bookmarksButton.tap()

        let bookmarksCountLabel = app.staticTexts["BookmarksCountLabel"]
        XCTAssertTrue(bookmarksCountLabel.waitForExistence(timeout: 5.0), "BookmarksCountLabel should appear in sheet")
        XCTAssertTrue(bookmarksCountLabel.label.contains("1 saved reels"), "Bookmarks count should show 1 saved reels, got: \(bookmarksCountLabel.label)")

        let doneButton = app.buttons["BookmarksDoneButton"]
        doneButton.tap()
    }

    // MARK: - 8. Spatial Gestures: Long Press Latched 2.0x Zone

    func testLatched2xSpatialZoneToggle() throws {
        let feedView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(feedView.waitForExistence(timeout: 8.0))

        // Upper-Right Latched 2.0x zone: normX > 0.65, normY <= 0.65
        let upperRightCoord = feedView.coordinate(withNormalizedOffset: CGVector(dx: 0.80, dy: 0.35))
        upperRightCoord.press(forDuration: 0.7)

        // Verify latched 2.0x badge appears
        let latchedBadge = app.descendants(matching: .any)["Latched2xBadge"]
        XCTAssertTrue(latchedBadge.waitForExistence(timeout: 3.0), "Latched2xBadge should appear")

        // Per contract: single tap resets latched 2.0x speed
        Thread.sleep(forTimeInterval: 0.6)
        feedView.tap()

        // Verify latched badge dismisses
        let badgePredicate = NSPredicate(format: "exists == false")
        let expectation = XCTNSPredicateExpectation(predicate: badgePredicate, object: latchedBadge)
        let result = XCTWaiter.wait(for: [expectation], timeout: 4.0)
        XCTAssertEqual(result, .completed, "Latched2xBadge should dismiss upon single tap")
    }

    // MARK: - 9. Vertical Feed Swipe Paging

    func testVerticalFeedPaging() throws {
        let creatorHandle = app.staticTexts["ReelCreatorHandle"]
        XCTAssertTrue(creatorHandle.waitForExistence(timeout: 8.0))
        XCTAssertTrue(creatorHandle.label.contains("mkbhd"))

        let collectionView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(collectionView.waitForExistence(timeout: 5.0))

        // Swipe up to advance to reel 2
        collectionView.swipeUp(velocity: .fast)

        // Verify current reel advances to hubermanlab
        let nextCreatorPredicate = NSPredicate(format: "label CONTAINS 'hubermanlab'")
        let expectation = XCTNSPredicateExpectation(predicate: nextCreatorPredicate, object: creatorHandle)
        let result = XCTWaiter.wait(for: [expectation], timeout: 5.0)
        XCTAssertEqual(result, .completed, "Feed should page to hubermanlab on swipe up")

        let rankBadge = app.staticTexts["ReelRankBadge"]
        XCTAssertTrue(rankBadge.label.contains("#02"), "Rank should update to #02 on swipe up")
    }
}
