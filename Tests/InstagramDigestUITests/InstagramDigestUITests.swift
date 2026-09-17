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
        app?.terminate()
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

        // Verify opening Grid and dismissing via Done button without selection
        gridButton.tap()
        XCTAssertTrue(gridTitle.waitForExistence(timeout: 5.0))
        let gridDoneButton = app.buttons["GridDoneButton"]
        XCTAssertTrue(gridDoneButton.exists)
        gridDoneButton.tap()
        XCTAssertTrue(gridButton.waitForExistence(timeout: 3.0))
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

    // MARK: - 7. Spatial Gestures: Long Press Bookmark Zone & Unbookmark Toggle

    func testBookmarkSpatialZoneToggle() throws {
        let feedView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(feedView.waitForExistence(timeout: 8.0))

        // Center-Lower Bookmark zone: normX in [0.35, 0.65], normY > 0.65
        let centerLowerCoord = feedView.coordinate(withNormalizedOffset: CGVector(dx: 0.50, dy: 0.72))
        centerLowerCoord.press(forDuration: 0.7)

        // Verify bookmark indicator appears
        let bookmarkIndicator = app.descendants(matching: .any)["BookmarkIndicator"]
        XCTAssertTrue(bookmarkIndicator.waitForExistence(timeout: 3.0), "BookmarkIndicator should appear after bookmarking")

        // Open Bookmarks sheet to verify persistence
        let bookmarksButton = app.buttons["BookmarksButton"]
        bookmarksButton.tap()

        let bookmarksCountLabel = app.staticTexts["BookmarksCountLabel"]
        XCTAssertTrue(bookmarksCountLabel.waitForExistence(timeout: 5.0), "BookmarksCountLabel should appear in sheet")
        XCTAssertTrue(bookmarksCountLabel.label.contains("1 saved reels"), "Bookmarks count should show 1 saved reels, got: \(bookmarksCountLabel.label)")

        let doneButton = app.buttons["BookmarksDoneButton"]
        doneButton.tap()

        // Unbookmark via second long press in same zone
        Thread.sleep(forTimeInterval: 0.5)
        centerLowerCoord.press(forDuration: 0.7)

        // Verify bookmark indicator dismisses
        let indicatorGonePredicate = NSPredicate(format: "exists == false")
        let indicatorExpectation = XCTNSPredicateExpectation(predicate: indicatorGonePredicate, object: bookmarkIndicator)
        let indicatorResult = XCTWaiter.wait(for: [indicatorExpectation], timeout: 4.0)
        XCTAssertEqual(indicatorResult, .completed, "BookmarkIndicator should dismiss upon unbookmarking")
    }

    // MARK: - 8. Spatial Gestures: Long Press Latched 2.0x Zone & Speed Cycler Clearing

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

        // Latch 2.0x again
        Thread.sleep(forTimeInterval: 0.5)
        upperRightCoord.press(forDuration: 0.7)
        XCTAssertTrue(latchedBadge.waitForExistence(timeout: 3.0))

        // Tap speed button: must clear latch and advance speed
        let speedButton = app.buttons["SpeedButton"]
        speedButton.tap()
        XCTAssertFalse(latchedBadge.exists, "Tapping speed button must clear 2.0x latch")

        // Latch 2.0x again and verify swipe to next reel resets latch (Contract §1.5)
        Thread.sleep(forTimeInterval: 0.5)
        upperRightCoord.press(forDuration: 0.7)
        XCTAssertTrue(latchedBadge.waitForExistence(timeout: 3.0))

        feedView.swipeUp(velocity: .fast)
        let rankBadge = app.staticTexts["ReelRankBadge"]
        let rankPredicate = NSPredicate(format: "label CONTAINS '#02'")
        let rankExpectation = XCTNSPredicateExpectation(predicate: rankPredicate, object: rankBadge)
        let rankResult = XCTWaiter.wait(for: [rankExpectation], timeout: 5.0)
        XCTAssertEqual(rankResult, .completed, "Rank should advance to #02")

        let badgeGoneExpectation = XCTNSPredicateExpectation(predicate: badgePredicate, object: latchedBadge)
        let badgeGoneResult = XCTWaiter.wait(for: [badgeGoneExpectation], timeout: 4.0)
        XCTAssertEqual(badgeGoneResult, .completed, "Latched2xBadge should dismiss upon changing reels")
    }

    // MARK: - 9. Vertical Feed Swipe Paging (Forward, Backward)

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

        // Swipe down to return to reel 1
        collectionView.swipeDown(velocity: .fast)
        let prevCreatorPredicate = NSPredicate(format: "label CONTAINS 'mkbhd'")
        let prevExpectation = XCTNSPredicateExpectation(predicate: prevCreatorPredicate, object: creatorHandle)
        let prevResult = XCTWaiter.wait(for: [prevExpectation], timeout: 5.0)
        XCTAssertEqual(prevResult, .completed, "Feed should page back to mkbhd on swipe down")
        XCTAssertTrue(rankBadge.label.contains("#01"))
    }

    // MARK: - 10. Spatial Gestures: Long Press Share Zone

    func testShareSheetSpatialTrigger() throws {
        let feedView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(feedView.waitForExistence(timeout: 8.0))

        // Lower-Right Share zone: normX > 0.65, normY > 0.65
        let lowerRightCoord = feedView.coordinate(withNormalizedOffset: CGVector(dx: 0.80, dy: 0.80))
        lowerRightCoord.press(forDuration: 0.7)

        // Wait for sheet presentation animation
        Thread.sleep(forTimeInterval: 1.0)

        // Dismiss share sheet by tapping outside
        feedView.coordinate(withNormalizedOffset: CGVector(dx: 0.10, dy: 0.10)).tap()

        let speedButton = app.buttons["SpeedButton"]
        XCTAssertTrue(speedButton.waitForExistence(timeout: 5.0), "Feed controls should be active after share dismiss")
    }

    // MARK: - 11. Caption Expansion Toggle (P0 Hit-Testing Fix)

    func testCaptionExpansionToggle() throws {
        let caption = app.staticTexts["ReelCaptionText"]
        XCTAssertTrue(caption.waitForExistence(timeout: 8.0), "ReelCaptionText should exist on feed")

        let initialHeight = caption.frame.height
        XCTAssertGreaterThan(initialHeight, 0, "Initial caption height should be non-zero")

        // Tap caption to expand from 2 lines to 8 lines
        caption.tap()
        Thread.sleep(forTimeInterval: 0.5) // Wait for 0.2s animation to complete

        let expandedHeight = caption.frame.height
        XCTAssertGreaterThan(expandedHeight, initialHeight + 10.0, "Caption height should expand by at least 10pt upon tap")

        // Tap caption again to collapse back
        caption.tap()
        Thread.sleep(forTimeInterval: 0.5)

        let collapsedHeight = caption.frame.height
        XCTAssertLessThanOrEqual(abs(collapsedHeight - initialHeight), 5.0, "Caption should collapse back to initial height")

        // Verify feed gestures pass through around the caption by swiping to next reel
        let feedView = app.collectionViews["FeedCollectionView"]
        feedView.swipeUp(velocity: .fast)
        let rankBadge = app.staticTexts["ReelRankBadge"]
        let rankPredicate = NSPredicate(format: "label CONTAINS '#02'")
        let rankExpectation = XCTNSPredicateExpectation(predicate: rankPredicate, object: rankBadge)
        let rankResult = XCTWaiter.wait(for: [rankExpectation], timeout: 5.0)
        XCTAssertEqual(rankResult, .completed, "Feed should page to reel #02 after caption interaction")

        // Assert feed controls remain alive and responsive
        let speedButton = app.buttons["SpeedButton"]
        XCTAssertTrue(speedButton.exists, "SpeedButton should remain visible and functional")
    }

    // MARK: - 12. Bookmarks Sheet Row Swipe-to-Delete (Candidate E1)

    func testBookmarkRowSwipeToDelete() throws {
        let feedView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(feedView.waitForExistence(timeout: 8.0))

        // Bookmark reel 1 using Center-Lower long-press
        let centerLowerCoord = feedView.coordinate(withNormalizedOffset: CGVector(dx: 0.50, dy: 0.72))
        centerLowerCoord.press(forDuration: 0.7)

        let bookmarkIndicator = app.descendants(matching: .any)["BookmarkIndicator"]
        XCTAssertTrue(bookmarkIndicator.waitForExistence(timeout: 3.0), "BookmarkIndicator should appear")

        // Open Bookmarks sheet
        let bookmarksButton = app.buttons["BookmarksButton"]
        XCTAssertTrue(bookmarksButton.exists)
        bookmarksButton.tap()

        let bookmarksTitle = app.staticTexts["Saved Bookmarks"]
        XCTAssertTrue(bookmarksTitle.waitForExistence(timeout: 5.0))

        // Query row via descendants
        let row = app.descendants(matching: .any)["BookmarkRow_reel_01"]
        XCTAssertTrue(row.waitForExistence(timeout: 3.0), "BookmarkRow_reel_01 should exist in list")

        // Swipe left to delete
        row.swipeLeft()
        let deleteButton = app.buttons["Delete"]
        XCTAssertTrue(deleteButton.waitForExistence(timeout: 3.0))
        deleteButton.tap()

        // Verify empty state text reappears
        let emptyStateText = app.staticTexts["BookmarksEmptyStateText"]
        XCTAssertTrue(emptyStateText.waitForExistence(timeout: 5.0), "Empty state should appear after deleting bookmark")

        // Predicate wait for count label update to 0
        let countLabel = app.staticTexts["BookmarksCountLabel"]
        let countPredicate = NSPredicate(format: "label CONTAINS '0 saved reels'")
        let countExpectation = XCTNSPredicateExpectation(predicate: countPredicate, object: countLabel)
        let countResult = XCTWaiter.wait(for: [countExpectation], timeout: 5.0)
        XCTAssertEqual(countResult, .completed, "Bookmarks count should update to 0 saved reels")

        let doneButton = app.buttons["BookmarksDoneButton"]
        doneButton.tap()
        XCTAssertTrue(bookmarksButton.waitForExistence(timeout: 3.0))
    }

    // MARK: - 13. Mindful Daily Modal (Candidate D)

    func testMindfulDailyModalInteraction() throws {
        // Relaunch app with seeded 50-reels viewed milestone
        app?.terminate()
        let seededApp = XCUIApplication()
        seededApp.launchArguments = ["-ui-testing", "-ui-testing-seed-mindful"]
        seededApp.launch()
        self.app = seededApp

        let modal = seededApp.descendants(matching: .any)["MindfulDailyModal"]
        XCTAssertTrue(modal.waitForExistence(timeout: 8.0), "MindfulDailyModal should appear when 50 reels reached")

        let snoozeButton = seededApp.buttons["MindfulSnoozeButton"]
        XCTAssertTrue(snoozeButton.waitForExistence(timeout: 3.0), "MindfulSnoozeButton should exist")

        let takeBreakButton = seededApp.buttons["MindfulTakeBreakButton"]
        XCTAssertTrue(takeBreakButton.waitForExistence(timeout: 3.0), "MindfulTakeBreakButton should exist")

        let continueButton = seededApp.buttons["MindfulContinueButton"]
        XCTAssertTrue(continueButton.waitForExistence(timeout: 3.0), "MindfulContinueButton should exist")

        // Verify modal message displays seeded count
        let milestoneText = modal.staticTexts.containing(NSPredicate(format: "label CONTAINS '50 reels'")).firstMatch
        XCTAssertTrue(milestoneText.waitForExistence(timeout: 3.0), "Modal should display 50 reels milestone text")

        // Tap Snooze for Today to dismiss modal
        snoozeButton.tap()

        // Verify modal dismisses
        let modalGonePredicate = NSPredicate(format: "exists == false")
        let modalExpectation = XCTNSPredicateExpectation(predicate: modalGonePredicate, object: modal)
        let modalResult = XCTWaiter.wait(for: [modalExpectation], timeout: 5.0)
        XCTAssertEqual(modalResult, .completed, "MindfulDailyModal should dismiss after tapping Snooze")

        let speedButton = seededApp.buttons["SpeedButton"]
        XCTAssertTrue(speedButton.waitForExistence(timeout: 5.0), "Feed controls should return after dismissing mindful modal")
    }
}
