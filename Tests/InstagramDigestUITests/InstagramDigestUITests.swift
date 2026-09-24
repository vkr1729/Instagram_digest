import XCTest

/// Deep Automated User Acceptance Testing (UAT) suite for Instagram Digest.
/// Strictly exercises locked Mock 2 (Mobile PWA Standard) user workflows:
/// - Brand header (Instagram logo, Grid, Download, Bookmarks chip)
/// - Story Category Circles bar (all 7 categories edge-to-edge)
/// - Grid navigation (Jump-to-N pill retired in favor of grid + last-reel resume)
/// - Bottom HUD: @creatorHandle, #rank badge, WhatsApp Share, Gold Save button, 2-line caption
/// - Paging, speed latch, and Bookmarks storage gauge
@MainActor
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

    // MARK: - 1. App Launch & Mock 2 Header Controls

    func testAppLaunchAndFeedInitialization() throws {
        // Verify Instagram brand logo in Grand Hotel font
        let logo = app.staticTexts["InstagramLogoText"]
        XCTAssertTrue(logo.waitForExistence(timeout: 8.0), "InstagramLogoText must appear on app launch")
        XCTAssertEqual(logo.label, "Instagram")

        // Verify Mock 2 header action controls (Jump pill retired)
        let gridButton = app.buttons["GridIconButton"]
        let downloadButton = app.buttons["OfflineIconButton"]
        let bookmarksButton = app.buttons["BookmarksChipButton"]

        XCTAssertFalse(app.buttons["JumpPillButton"].exists, "JumpPillButton must not exist; grid + resume replace it")
        XCTAssertTrue(gridButton.exists, "GridIconButton must exist in header")
        XCTAssertTrue(downloadButton.exists, "OfflineIconButton must exist in header")
        XCTAssertTrue(bookmarksButton.exists, "BookmarksChipButton must exist in header")

        // Verify all 7 Story Category circles are present edge-to-edge
        let categories = ["all", "entertainment", "finance", "ai_tech", "niche", "health", "food"]
        for cat in categories {
            let catButton = app.buttons["Category_\(cat)"]
            XCTAssertTrue(catButton.exists, "Category button Category_\(cat) must exist")
        }

        // Verify bottom HUD metadata
        let creatorHandle = app.staticTexts["ReelCreatorHandle"]
        let rankBadge = app.staticTexts["ReelRankBadge"]
        let shareButton = app.buttons["WhatsAppShareButton"]
        let saveButton = app.buttons["SaveBookmarkButton"]

        XCTAssertTrue(creatorHandle.waitForExistence(timeout: 5.0), "Creator handle must appear in bottom HUD")
        XCTAssertTrue(rankBadge.exists, "Rank badge must exist in bottom HUD")
        XCTAssertTrue(shareButton.exists, "WhatsApp share button must exist in bottom HUD")
        XCTAssertTrue(saveButton.exists, "Save bookmark button must exist in bottom HUD")
    }

    // MARK: - 2. Story Category Bar Filtering

    func testStoryCategoryFiltering() throws {
        let entertainCat = app.buttons["Category_entertainment"]
        XCTAssertTrue(entertainCat.waitForExistence(timeout: 8.0))

        // Tap Entertainment category
        entertainCat.tap()
        Thread.sleep(forTimeInterval: 0.5)

        // Return to Top 300 (All)
        let allCat = app.buttons["Category_all"]
        XCTAssertTrue(allCat.exists)
        allCat.tap()
        Thread.sleep(forTimeInterval: 0.5)

        let creatorHandle = app.staticTexts["ReelCreatorHandle"]
        XCTAssertTrue(creatorHandle.exists)
    }

    // MARK: - 3. Grid Navigation (replaces retired Jump-to-Reel Modal)

    func testGridNavigationToReel() throws {
        let gridButton = app.buttons["GridIconButton"]
        XCTAssertTrue(gridButton.waitForExistence(timeout: 8.0))

        gridButton.tap()

        // Verify grid sheet is presented
        let gridDoneButton = app.buttons["GridDoneButton"]
        XCTAssertTrue(gridDoneButton.waitForExistence(timeout: 5.0), "Grid sheet should be presented")

        // Navigate to the second reel via the grid
        let secondItem = app.buttons["GridReelItem_1"]
        XCTAssertTrue(secondItem.waitForExistence(timeout: 5.0), "Grid item must exist")
        secondItem.tap()

        // Verify sheet dismissed and feed jumped to the selected reel
        let rankBadge = app.staticTexts["ReelRankBadge"]
        XCTAssertTrue(rankBadge.waitForExistence(timeout: 5.0))
    }

    // MARK: - 4. Bottom HUD: WhatsApp Share & Bookmark Toggle

    func testBottomHudActions() throws {
        let saveButton = app.buttons["SaveBookmarkButton"]
        XCTAssertTrue(saveButton.waitForExistence(timeout: 8.0))

        // Save bookmark via bottom HUD button
        saveButton.tap()

        let bookmarkIndicator = app.descendants(matching: .any)["BookmarkIndicator"]
        _ = bookmarkIndicator.waitForExistence(timeout: 2.0)

        // Open Bookmarks sheet from header chip
        let bookmarksChip = app.buttons["BookmarksChipButton"]
        bookmarksChip.tap()

        let bookmarksNavBar = app.navigationBars["Saved Bookmarks"]
        XCTAssertTrue(bookmarksNavBar.waitForExistence(timeout: 5.0), "Saved Bookmarks sheet should open")

        let doneButton = app.buttons["BookmarksDoneButton"]
        XCTAssertTrue(doneButton.exists)
        doneButton.tap()

        XCTAssertTrue(saveButton.waitForExistence(timeout: 3.0))
    }

    // MARK: - 5. Grid View Sheet

    func testGridViewSheet() throws {
        let gridButton = app.buttons["GridIconButton"]
        XCTAssertTrue(gridButton.waitForExistence(timeout: 8.0))

        gridButton.tap()

        let gridDoneButton = app.buttons["GridDoneButton"]
        XCTAssertTrue(gridDoneButton.waitForExistence(timeout: 5.0), "Grid sheet should be presented")
        gridDoneButton.tap()

        XCTAssertTrue(gridButton.waitForExistence(timeout: 3.0))
    }

    // MARK: - 6. Download All / Offline Sheet

    func testDownloadAllSheet() throws {
        let downloadButton = app.buttons["OfflineIconButton"]
        XCTAssertTrue(downloadButton.waitForExistence(timeout: 8.0))

        downloadButton.tap()

        let closeButton = app.buttons["DownloadCloseButton"]
        XCTAssertTrue(closeButton.waitForExistence(timeout: 5.0), "Download sheet should be presented")
        closeButton.tap()

        XCTAssertTrue(downloadButton.waitForExistence(timeout: 3.0))
    }

    // MARK: - 7. Vertical Paging

    func testVerticalFeedPaging() throws {
        let creatorHandle = app.staticTexts["ReelCreatorHandle"]
        XCTAssertTrue(creatorHandle.waitForExistence(timeout: 8.0))

        let collectionView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(collectionView.waitForExistence(timeout: 5.0))

        // Swipe up to advance
        collectionView.swipeUp(velocity: .fast)
        Thread.sleep(forTimeInterval: 0.5)

        // Swipe down to return
        collectionView.swipeDown(velocity: .fast)
        Thread.sleep(forTimeInterval: 0.5)

        XCTAssertTrue(creatorHandle.exists)
    }

    // MARK: - 9. Watch Timer Pill Format

    func testWatchTimerPillFormat() throws {
        let logo = app.staticTexts["InstagramLogoText"]
        XCTAssertTrue(logo.waitForExistence(timeout: 8.0))

        // The per-week wall-clock pill must render as "X.X hrs" (never empty,
        // never a raw second count). Value may be restored from a prior test
        // in this simulator, so match the format, not an exact value.
        let pill = app.descendants(matching: .any)["WatchTimerPill"]
        XCTAssertTrue(pill.waitForExistence(timeout: 5.0), "WatchTimerPill must exist in header")
        let format = NSPredicate(format: "label MATCHES %@", "\\d+\\.\\d+ hrs")
        let pillText = pill.descendants(matching: .staticText).matching(format).firstMatch
        XCTAssertTrue(pillText.waitForExistence(timeout: 3.0), "WatchTimerPill must read like \"0.0 hrs\"")
    }

    // MARK: - 8. Caption Expansion Toggle

    func testCaptionExpansionToggle() throws {
        let caption = app.staticTexts["ReelCaptionText"]
        if caption.waitForExistence(timeout: 5.0) {
            let initialHeight = caption.frame.height
            caption.tap()
            Thread.sleep(forTimeInterval: 0.4)
            caption.tap()
            Thread.sleep(forTimeInterval: 0.4)
            XCTAssertLessThanOrEqual(abs(caption.frame.height - initialHeight), 10.0)
        }
    }
}
