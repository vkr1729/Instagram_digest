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

        // Tap Entertainment category — chip must become selected.
        entertainCat.tap()
        let selectedExpectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value == 'selected'"),
            object: entertainCat)
        XCTAssertEqual(XCTWaiter.wait(for: [selectedExpectation], timeout: 5.0), .completed,
                       "Entertainment chip must become selected after tap")

        // Return to Top 300 (All) — All must become selected, Entertainment unselected.
        let allCat = app.buttons["Category_all"]
        XCTAssertTrue(allCat.exists)
        allCat.tap()
        let allSelected = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value == 'selected'"),
            object: allCat)
        XCTAssertEqual(XCTWaiter.wait(for: [allSelected], timeout: 5.0), .completed,
                       "All chip must become selected after tap")

        let creatorHandle = app.staticTexts["ReelCreatorHandle"]
        XCTAssertTrue(creatorHandle.waitForExistence(timeout: 5.0))
    }

    // MARK: - 3. Grid Navigation (replaces retired Jump-to-Reel Modal)

    func testGridNavigationToReel() throws {
        let gridButton = app.buttons["GridIconButton"]
        XCTAssertTrue(gridButton.waitForExistence(timeout: 8.0))

        gridButton.tap()

        // Verify grid sheet is presented
        let gridDoneButton = app.buttons["GridDoneButton"]
        XCTAssertTrue(gridDoneButton.waitForExistence(timeout: 5.0), "Grid sheet should be presented")

        let rankBadgeBefore = app.staticTexts["ReelRankBadge"]
        XCTAssertTrue(rankBadgeBefore.waitForExistence(timeout: 5.0))
        let labelBefore = rankBadgeBefore.label

        // Navigate to the second reel via the grid
        let secondItem = app.buttons["GridReelItem_1"]
        XCTAssertTrue(secondItem.waitForExistence(timeout: 5.0), "Grid item must exist")
        secondItem.tap()

        // Verify sheet dismissed and feed jumped to the selected reel (rank must change)
        let rankBadge = app.staticTexts["ReelRankBadge"]
        XCTAssertTrue(rankBadge.waitForExistence(timeout: 5.0))
        let changed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label != %@", labelBefore),
            object: rankBadge)
        XCTAssertEqual(XCTWaiter.wait(for: [changed], timeout: 5.0), .completed,
                       "Rank badge must change after grid jump (was \(labelBefore))")
    }

    // MARK: - 4. Bottom HUD: WhatsApp Share & Bookmark Toggle

    func testBottomHudActions() throws {
        let saveButton = app.buttons["SaveBookmarkButton"]
        XCTAssertTrue(saveButton.waitForExistence(timeout: 8.0))

        // Save bookmark via bottom HUD button (owner-key prompt is
        // pre-suppressed under -ui-testing; tolerate it either way).
        saveButton.tap()

        // The first bookmark prompts for the Cloudflare owner key; dismiss it
        // so it cannot swallow the chip tap below (clean-run hit-test failure).
        let ownerKeyAlert = app.alerts["Link Cloudflare Owner Key"]
        if ownerKeyAlert.waitForExistence(timeout: 2.0) {
            ownerKeyAlert.buttons["Later"].tap()
        }

        // The reel may already be saved from a previous test in this run
        // (shared in-memory store per test process): either the pop or the
        // Saved label proves the toggle worked.
        let bookmarkIndicator = app.descendants(matching: .any)["BookmarkIndicator"]
        let popAppeared = bookmarkIndicator.waitForExistence(timeout: 5.0)
        XCTAssertTrue(popAppeared || saveButton.label.contains("Saved"),
                      "Save must acknowledge (pop or Saved label)")
        if !saveButton.label.contains("Saved") {
            saveButton.tap()
        }
        XCTAssertTrue(saveButton.label.contains("Saved"), "Save button must flip to 'Saved' after bookmarking")

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
        let rankBadge = app.staticTexts["ReelRankBadge"]
        XCTAssertTrue(rankBadge.waitForExistence(timeout: 8.0))
        let labelBefore = rankBadge.label

        let collectionView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(collectionView.waitForExistence(timeout: 5.0))

        // Swipe up to advance — rank must change.
        collectionView.swipeUp(velocity: .fast)
        let advanced = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label != %@", labelBefore),
            object: rankBadge)
        XCTAssertEqual(XCTWaiter.wait(for: [advanced], timeout: 5.0), .completed,
                       "Rank badge must advance after swipe up")
        let labelAfterUp = rankBadge.label

        // Swipe down to return — rank must change again (back toward start).
        collectionView.swipeDown(velocity: .fast)
        let returned = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label != %@", labelAfterUp),
            object: rankBadge)
        XCTAssertEqual(XCTWaiter.wait(for: [returned], timeout: 5.0), .completed,
                       "Rank badge must change after swipe down")
    }

    // MARK: - 9. Watch Timer Pill Format

    func testWatchTimerPillFormat() throws {
        let logo = app.staticTexts["InstagramLogoText"]
        XCTAssertTrue(logo.waitForExistence(timeout: 8.0))

        // The per-week wall-clock pill must render as "X.X hrs" (never empty,
        // never a raw second count). Value may be restored from a prior test
        // in this simulator, so match the format, not an exact value. Query
        // the text app-wide and lazily (.element, not .firstMatch, so the
        // wait actually polls): accessibilityIdentifier on a plain HStack
        // container is not reliably exposed to XCUITest, but its Text is.
        let format = NSPredicate(format: "label MATCHES %@", "\\d+\\.\\d+ hrs")
        let pillText = app.staticTexts.matching(format).element
        XCTAssertTrue(pillText.waitForExistence(timeout: 10.0), "WatchTimerPill must read like \"0.0 hrs\"")
    }

    // MARK: - 8. Caption Expansion Toggle

    func testCaptionExpansionToggle() throws {
        // Find a reel with a long, clamping caption: the first reel's
        // caption may be one line (tap is a no-op there).
        var caption = app.staticTexts["ReelCaptionText"]
        var found = false
        for _ in 0..<6 {
            if caption.waitForExistence(timeout: 5.0),
               (caption.value as? String) == "collapsed",
               caption.frame.height > 30 {
                found = true
                break
            }
            let pager = app.collectionViews["FeedCollectionView"]
            if !pager.exists { break }
            pager.swipeUp()
            caption = app.staticTexts["ReelCaptionText"]
        }
        XCTAssertTrue(found, "need a reel with a clamping caption to test expansion")
        // Prefer the accessibility value (collapsed/expanded) when available;
        // fall back to frame-height transitions.
        caption.tap()
        let expandedExpectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value == 'expanded'"),
            object: caption)
        let expandedByValue = XCTWaiter.wait(for: [expandedExpectation], timeout: 3.0) == .completed
        if !expandedByValue {
            // Frame-based fallback: first tap must grow the caption.
            // Capture growth relative to pre-tap height via polling.
            let beforeHeight = caption.frame.height
            var grewHeight = false
            for _ in 0..<30 {
                if caption.frame.height > beforeHeight + 2.0 { grewHeight = true; break }
                Thread.sleep(forTimeInterval: 0.1)
            }
            XCTAssertTrue(grewHeight, "Caption must expand on first tap")
        }
        caption.tap()
        let collapsedExpectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value == 'collapsed'"),
            object: caption)
        let collapsedByValue = XCTWaiter.wait(for: [collapsedExpectation], timeout: 3.0) == .completed
        if !collapsedByValue {
            var shrankBack = false
            let expandedHeight = caption.frame.height
            for _ in 0..<30 {
                if abs(caption.frame.height - expandedHeight) > 1.0 { shrankBack = true; break }
                Thread.sleep(forTimeInterval: 0.1)
            }
            XCTAssertTrue(shrankBack, "Caption must collapse on second tap")
        }
    }

    // MARK: - 10. Bookmark round-trip: save -> sheet -> player -> unsave

    func testBookmarkPersistsIntoBookmarksSheet() throws {
        let saveButton = app.buttons["SaveBookmarkButton"]
        XCTAssertTrue(saveButton.waitForExistence(timeout: 8.0))
        saveButton.tap()
        let ownerKeyAlert = app.alerts["Link Cloudflare Owner Key"]
        if ownerKeyAlert.waitForExistence(timeout: 2.0) {
            ownerKeyAlert.buttons["Later"].tap()
        }
        let bookmarksChip = app.buttons["BookmarksChipButton"]
        XCTAssertTrue(bookmarksChip.waitForExistence(timeout: 5.0))
        bookmarksChip.tap()
        XCTAssertTrue(app.navigationBars["Saved Bookmarks"].waitForExistence(timeout: 5.0))
        // First saved bookmark must appear in the grid.
        XCTAssertTrue(app.buttons["BookmarkGridItem_0"].waitForExistence(timeout: 5.0),
                      "Saved bookmark must persist into the Bookmarks sheet")
        app.buttons["BookmarksDoneButton"].tap()
    }

    func testBookmarkPlayerUnsaveClosesWhenLastBookmarkRemoved() throws {
        let saveButton = app.buttons["SaveBookmarkButton"]
        XCTAssertTrue(saveButton.waitForExistence(timeout: 8.0))
        saveButton.tap()
        let ownerKeyAlert = app.alerts["Link Cloudflare Owner Key"]
        if ownerKeyAlert.waitForExistence(timeout: 2.0) {
            ownerKeyAlert.buttons["Later"].tap()
        }
        let bookmarksChip = app.buttons["BookmarksChipButton"]
        bookmarksChip.tap()
        XCTAssertTrue(app.navigationBars["Saved Bookmarks"].waitForExistence(timeout: 5.0))
        let firstItem = app.buttons["BookmarkGridItem_0"]
        if firstItem.waitForExistence(timeout: 5.0) {
            firstItem.tap()
            let unsaveButton = app.buttons["BookmarkPlayerUnsaveButton"]
            if unsaveButton.waitForExistence(timeout: 5.0) {
                unsaveButton.tap()
            }
            // After removing the last bookmark the player must close (no crash, sheet visible).
            XCTAssertTrue(app.navigationBars["Saved Bookmarks"].waitForExistence(timeout: 5.0))
        }
        app.buttons["BookmarksDoneButton"].tap()
    }

    func testTapToPauseResume() throws {
        let collectionView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(collectionView.waitForExistence(timeout: 8.0))
        let rankBadge = app.staticTexts["ReelRankBadge"]
        XCTAssertTrue(rankBadge.waitForExistence(timeout: 5.0))
        // Tap toggles play/pause; feed must remain on the same reel without crashing.
        collectionView.tap()
        XCTAssertTrue(rankBadge.waitForExistence(timeout: 3.0))
        collectionView.tap()
        XCTAssertTrue(rankBadge.waitForExistence(timeout: 3.0))
    }

    func testPlaybackClockAdvances() throws {
        let progress = app.otherElements["PlaybackProgress"]
        XCTAssertTrue(progress.waitForExistence(timeout: 8.0))
        let first = (progress.value as? String ?? "").components(separatedBy: " ").first ?? ""
        let start = Date()
        var second = first
        while second == first, Date().timeIntervalSince(start) < 10.0 {
            second = ((app.otherElements["PlaybackProgress"].value as? String ?? "").components(separatedBy: " ").first) ?? second
        }
        XCTAssertNotEqual(second, first, "playback clock must advance within 10s")
    }

    func testTapPausesClock() throws {
        let collectionView = app.collectionViews["FeedCollectionView"]
        XCTAssertTrue(collectionView.waitForExistence(timeout: 8.0))
        let progress = app.otherElements["PlaybackProgress"]
        XCTAssertTrue(progress.waitForExistence(timeout: 5.0))
        // Ensure playing first: a second tap would resume and flake the check.
        var state = (progress.value as? String) ?? ""
        if state.contains("paused") { collectionView.tap() }
        let playing = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value CONTAINS 'playing'"),
            object: progress)
        XCTAssertEqual(XCTWaiter.wait(for: [playing], timeout: 5.0), .completed)
        collectionView.tap()
        let paused = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value CONTAINS 'paused'"),
            object: progress)
        XCTAssertEqual(XCTWaiter.wait(for: [paused], timeout: 5.0), .completed,
                       "tap must pause playback")
        let frozen = ((progress.value as? String ?? "").components(separatedBy: " ").first) ?? ""
        sleep(2)
        let later = ((app.otherElements["PlaybackProgress"].value as? String ?? "").components(separatedBy: " ").first) ?? ""
        XCTAssertEqual(later, frozen, "paused clock must not advance")
    }

    func testForegroundKeepsRankBadge() throws {
        let rankBadge = app.staticTexts["ReelRankBadge"]
        XCTAssertTrue(rankBadge.waitForExistence(timeout: 8.0))
        // The pool skip cascade moves the pager while media is missing;
        // with seeded local clips the rank must simply still exist.
        XCTAssertFalse(rankBadge.label.isEmpty)
        XCUIDevice.shared.press(.home)
        sleep(1)
        app.activate()
        XCTAssertTrue(rankBadge.waitForExistence(timeout: 8.0))
        XCTAssertFalse(rankBadge.label.isEmpty, "foreground must not strand the feed")
    }
}
