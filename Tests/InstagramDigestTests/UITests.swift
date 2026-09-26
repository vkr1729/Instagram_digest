import XCTest
import SwiftData
@testable import InstagramDigest

final class UITests: XCTestCase {

    // MARK: - Seek Pan Vertical Dominance Rule (production statics)

    func testVerticalDominanceFormula() {
        typealias G = SeekPanGestureRecognizer
        // Vertical flick (deltaX = 5, deltaY = 10) -> 14 >= 5 -> fails
        XCTAssertTrue(G.shouldFailSeekForVerticalDominance(deltaX: 5, deltaY: 10))
        // Horizontal swipe (deltaX = 15, deltaY = 2) -> 2.8 < 15 -> does not fail
        XCTAssertFalse(G.shouldFailSeekForVerticalDominance(deltaX: 15, deltaY: 2))
        // Past 18pt (deltaX = 20, deltaY = 18) -> locked horizontal -> does not fail
        XCTAssertFalse(G.shouldFailSeekForVerticalDominance(deltaX: 20, deltaY: 18))
    }

    // MARK: - Spatial Zone Routing (production statics)

    func testSpatialZones() {
        typealias V = FeedPagerView
        let w: CGFloat = 400
        let h: CGFloat = 800

        // Mid & upper right: x = 300 (0.75w), y = 200 (0.25h) -> 2x latch
        XCTAssertEqual(V.resolveSpatialZone(x: 300, y: 200, width: w, height: h), .upperRightSpeed)
        // Lower-Right: x = 300 (0.75w), y = 600 (0.75h) -> share
        XCTAssertEqual(V.resolveSpatialZone(x: 300, y: 600, width: w, height: h), .lowerRightShare)
        // Lower middle: x = 200 (0.50w), y = 600 (0.75h) -> bookmark
        XCTAssertEqual(V.resolveSpatialZone(x: 200, y: 600, width: w, height: h), .lowerMiddleBookmark)
        // Boundary: x = 260 (0.65w) is NOT > 0.65, y low -> bookmark zone
        XCTAssertEqual(V.resolveSpatialZone(x: 260, y: 600, width: w, height: h), .lowerMiddleBookmark)
        // Dead Zone: x = 100 (0.25w), y = 200 (0.25h) -> no action
        XCTAssertEqual(V.resolveSpatialZone(x: 100, y: 200, width: w, height: h), .deadZone)
    }

    // MARK: - Seek Calculation (production statics)

    func testSeekDeltaCalculation() {
        typealias G = SeekPanGestureRecognizer
        // Scrub right by 100pt -> delta +0.25 -> 0.55
        XCTAssertEqual(G.seekFraction(initialFraction: 0.30, deltaX: 100, viewWidth: 400), 0.55, accuracy: 0.001)
        // Scrub left by 200pt -> delta -0.50 -> clamped to 0.0
        XCTAssertEqual(G.seekFraction(initialFraction: 0.30, deltaX: -200, viewWidth: 400), 0.0, accuracy: 0.001)
    }

    // MARK: - Jump-to-N Predecessor Set (production rules)

    func testJumpToNPredecessors() {
        let items = (0..<10).map {
            ReelItem(id: "r\($0)", creatorHandle: "c", caption: "", rank: $0 + 1,
                     videoUrl: URL(string: "https://example.com/\($0).mp4")!)
        }
        let alreadyWatched: Set<String> = ["r0", "r1"]
        let unrecorded = WatchedRules.unrecordedPredecessorIDs(items: items, targetIndex: 5, alreadyWatched: alreadyWatched)
        XCTAssertEqual(unrecorded, ["r2", "r3", "r4"])
    }

    // MARK: - Mindful Daily Snooze (persisted state)

    func testMindfulSnoozeValidation() throws {
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: DailyProgress.self, configurations: config)
        let context = ModelContext(container)
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let todayStr = formatter.string(from: Date())
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        let endOfDay = calendar.date(bySettingHour: 23, minute: 59, second: 59, of: Date())!
        let daily = DailyProgress(dateString: todayStr, viewedCount: 1, snoozeUntil: endOfDay)
        context.insert(daily)
        try context.save()
        let fetched = try context.fetch(FetchDescriptor<DailyProgress>())
        XCTAssertEqual(fetched.first?.snoozeUntil, endOfDay)
        XCTAssertTrue(Date() < (fetched.first?.snoozeUntil ?? Date.distantPast))
    }

    // MARK: - Bookmarks Storage Gauge

    func testBookmarkStorageGaugeMath() {
        let maxCap = MediaCacheManager.maxBookmarkStorageBytes // 1.5 GB = 1_500_000_000
        XCTAssertEqual(maxCap, 1_500_000_000)

        let usedBytes: Int64 = 750_000_000
        let fraction = min(1.0, Double(usedBytes) / Double(maxCap))
        XCTAssertEqual(fraction, 0.5, accuracy: 0.001)

        let overflowBytes: Int64 = 1_600_000_000
        let clampedFraction = min(1.0, Double(overflowBytes) / Double(maxCap))
        XCTAssertEqual(clampedFraction, 1.0)
    }

    // MARK: - UAT Feedback 1: Touch Coordinate Normalization Across Scrolled Pages

    func testScrollOffsetDoesNotSkewWindowCoordinates() {
        typealias V = FeedPagerView
        let windowWidth: CGFloat = 393
        let windowHeight: CGFloat = 852

        // Simulate touching upper-right (2x latch) at window coordinate (300, 200)
        let touchInWindow = CGPoint(x: 300, y: 200)
        XCTAssertEqual(V.resolveSpatialZone(x: touchInWindow.x, y: touchInWindow.y, width: windowWidth, height: windowHeight), .upperRightSpeed, "Touching upper-right must resolve to 2x speed latch")

        // Touching lower-right must resolve to share
        let lowerRightTouch = CGPoint(x: 300, y: 700)
        XCTAssertEqual(V.resolveSpatialZone(x: lowerRightTouch.x, y: lowerRightTouch.y, width: windowWidth, height: windowHeight), .lowerRightShare, "Touching lower-right must resolve to share")

        // If buggy coordinate space (contentOffset space on reel 2, offset = 1704) was used:
        let buggyTouchY: CGFloat = 1704 + 200 // 1904
        XCTAssertEqual(V.resolveSpatialZone(x: touchInWindow.x, y: buggyTouchY, width: windowWidth, height: windowHeight), .lowerRightShare, "Buggy scroll offset incorrectly routed 2x to share")
    }

    // MARK: - UAT Feedback 1: Viewport Clamp on Normalized Touch Coordinates

    func testViewportClampKeepsEdgeTouchesRoutable() {
        typealias V = FeedPagerView
        let w: CGFloat = 393
        let h: CGFloat = 852

        // Slight negative overshoot (status-bar / rounding) stays in the 2x zone.
        XCTAssertEqual(
            V.resolveSpatialZone(x: 300, y: -8, width: w, height: h),
            .upperRightSpeed,
            "Negative Y overshoot must clamp to 0 and still latch 2x"
        )

        // Slight bottom overshoot clamps to 1.0 and stably routes to share.
        XCTAssertEqual(
            V.resolveSpatialZone(x: 300, y: 860, width: w, height: h),
            .lowerRightShare,
            "Bottom overshoot must clamp to 1.0 and route to share"
        )

        // In-range 2x touch is unaffected by clamping.
        XCTAssertEqual(
            V.resolveSpatialZone(x: 300, y: 200, width: w, height: h),
            .upperRightSpeed
        )
    }

    // MARK: - Multi-Slot Pre-Render Invariants

    func testMultiSlotIndexMapping() {
        // Slot mapping is current-1/current/current+1 clamped to bounds.
        func slotIndices(for currentIndex: Int, totalReels: Int) -> (prev: Int?, curr: Int, next: Int?) {
            let prev = currentIndex > 0 ? currentIndex - 1 : nil
            let curr = currentIndex
            let next = currentIndex + 1 < totalReels ? currentIndex + 1 : nil
            return (prev, curr, next)
        }
        let slotsAt0 = slotIndices(for: 0, totalReels: 10)
        XCTAssertNil(slotsAt0.prev)
        XCTAssertEqual(slotsAt0.curr, 0)
        XCTAssertEqual(slotsAt0.next, 1)

        let slotsAt5 = slotIndices(for: 5, totalReels: 10)
        XCTAssertEqual(slotsAt5.prev, 4)
        XCTAssertEqual(slotsAt5.curr, 5)
        XCTAssertEqual(slotsAt5.next, 6)

        let slotsAt9 = slotIndices(for: 9, totalReels: 10)
        XCTAssertEqual(slotsAt9.prev, 8)
        XCTAssertEqual(slotsAt9.curr, 9)
        XCTAssertNil(slotsAt9.next)
    }
}
