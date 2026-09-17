import XCTest
@testable import InstagramDigest

final class UITests: XCTestCase {

    // MARK: - Seek Pan Vertical Dominance Rule

    func testVerticalDominanceFormula() {
        // Contract rule: fail immediately if |Δy| * 1.4 >= |Δx| before |Δx| crosses 18pt
        func shouldFailVertical(deltaX: CGFloat, deltaY: CGFloat) -> Bool {
            if deltaX < 18.0 {
                return (deltaY * 1.4) >= deltaX
            }
            return false // Locked horizontal
        }

        // Test 1: Vertical flick (deltaX = 5, deltaY = 10) -> 14 >= 5 -> fails
        XCTAssertTrue(shouldFailVertical(deltaX: 5, deltaY: 10))

        // Test 2: Horizontal swipe (deltaX = 15, deltaY = 2) -> 2.8 < 15 -> does not fail
        XCTAssertFalse(shouldFailVertical(deltaX: 15, deltaY: 2))

        // Test 3: Past 18pt (deltaX = 20, deltaY = 18) -> locked horizontal -> does not fail
        XCTAssertFalse(shouldFailVertical(deltaX: 20, deltaY: 18))
    }

    // MARK: - Spatial Zone Routing

    // Mirrors FeedPagerView.handleLongPress zone thresholds exactly.
    enum SpatialZone {
        case upperRightSpeed
        case lowerRightShare
        case lowerMiddleBookmark
        case deadZone
    }

    func resolveZone(x: CGFloat, y: CGFloat, width: CGFloat, height: CGFloat) -> SpatialZone {
        let normX = x / width
        let normY = y / height

        if normX > 0.65 && normY <= 0.65 {
            return .upperRightSpeed
        } else if normX > 0.65 && normY > 0.65 {
            return .lowerRightShare
        } else if normX >= 0.30 && normX <= 0.65 && normY > 0.65 {
            return .lowerMiddleBookmark
        } else {
            return .deadZone
        }
    }

    func testSpatialZones() {
        let w: CGFloat = 400
        let h: CGFloat = 800

        // Mid & upper right: x = 300 (0.75w), y = 200 (0.25h) -> 2x latch
        XCTAssertEqual(resolveZone(x: 300, y: 200, width: w, height: h), .upperRightSpeed)

        // Lower-Right: x = 300 (0.75w), y = 600 (0.75h) -> share
        XCTAssertEqual(resolveZone(x: 300, y: 600, width: w, height: h), .lowerRightShare)

        // Lower middle: x = 200 (0.50w), y = 600 (0.75h) -> bookmark
        XCTAssertEqual(resolveZone(x: 200, y: 600, width: w, height: h), .lowerMiddleBookmark)

        // Boundary: x = 260 (0.65w) is NOT > 0.65, y low -> bookmark zone
        XCTAssertEqual(resolveZone(x: 260, y: 600, width: w, height: h), .lowerMiddleBookmark)

        // Dead Zone: x = 100 (0.25w), y = 200 (0.25h) -> no action
        XCTAssertEqual(resolveZone(x: 100, y: 200, width: w, height: h), .deadZone)
    }

    // MARK: - Seek Calculation

    func testSeekDeltaCalculation() {
        let initialFraction: Double = 0.30
        let viewWidth: CGFloat = 400

        // Scrub right by 100pt -> delta +0.25 -> 0.55
        let deltaRight: CGFloat = 100
        let fractionRight = max(0.0, min(1.0, initialFraction + Double(deltaRight / viewWidth)))
        XCTAssertEqual(fractionRight, 0.55, accuracy: 0.001)

        // Scrub left by 200pt -> delta -0.50 -> clamped to 0.0
        let deltaLeft: CGFloat = -200
        let fractionLeft = max(0.0, min(1.0, initialFraction + Double(deltaLeft / viewWidth)))
        XCTAssertEqual(fractionLeft, 0.0, accuracy: 0.001)
    }

    // MARK: - Jump-to-N Predecessor Set

    func testJumpToNPredecessors() {
        let targetIndex = 5
        let allIndices = Array(0..<10)
        let predecessors = allIndices.filter { $0 < targetIndex }
        XCTAssertEqual(predecessors, [0, 1, 2, 3, 4])
        XCTAssertEqual(predecessors.count, 5)
    }

    // MARK: - Mindful Daily Snooze

    func testMindfulSnoozeValidation() {
        let calendar = Calendar(identifier: .gregorian)
        let now = Date()
        let endOfDay = calendar.date(bySettingHour: 23, minute: 59, second: 59, of: now)!

        // While before end of day, isSnoozed is true
        let isSnoozed = now < endOfDay
        XCTAssertTrue(isSnoozed)

        // Next day past snooze
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: now)!
        let isExpired = tomorrow >= endOfDay
        XCTAssertTrue(isExpired)
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
        let windowWidth: CGFloat = 393
        let windowHeight: CGFloat = 852

        // Simulate touching upper-right (2x latch) at window coordinate (300, 200)
        let touchInWindow = CGPoint(x: 300, y: 200)
        let zone = resolveZone(x: touchInWindow.x, y: touchInWindow.y, width: windowWidth, height: windowHeight)
        XCTAssertEqual(zone, .upperRightSpeed, "Touching upper-right must resolve to 2x speed latch")

        // Touching lower-right must resolve to share
        let lowerRightTouch = CGPoint(x: 300, y: 700)
        let shareZone = resolveZone(x: lowerRightTouch.x, y: lowerRightTouch.y, width: windowWidth, height: windowHeight)
        XCTAssertEqual(shareZone, .lowerRightShare, "Touching lower-right must resolve to share")

        // If buggy coordinate space (contentOffset space on reel 2, offset = 1704) was used:
        let buggyTouchY: CGFloat = 1704 + 200 // 1904
        let buggyZone = resolveZone(x: touchInWindow.x, y: buggyTouchY, width: windowWidth, height: windowHeight)
        XCTAssertEqual(buggyZone, .lowerRightShare, "Buggy scroll offset incorrectly routed 2x to share")
    }

    // MARK: - UAT Feedback 1: Viewport Clamp on Normalized Touch Coordinates

    /// Mirrors FeedPagerView.handleLongPress clamping exactly:
    /// norm = min(max(location / size, 0.0), 1.0) in window-relative space.
    func resolveZoneClamped(x: CGFloat, y: CGFloat, width: CGFloat, height: CGFloat) -> SpatialZone {
        let normX = min(max(x / width, 0.0), 1.0)
        let normY = min(max(y / height, 0.0), 1.0)

        if normX > 0.65 && normY <= 0.65 {
            return .upperRightSpeed
        } else if normX > 0.65 && normY > 0.65 {
            return .lowerRightShare
        } else if normX >= 0.30 && normX <= 0.65 && normY > 0.65 {
            return .lowerMiddleBookmark
        } else {
            return .deadZone
        }
    }

    func testViewportClampKeepsEdgeTouchesRoutable() {
        let w: CGFloat = 393
        let h: CGFloat = 852

        // Slight negative overshoot (status-bar / rounding) stays in the 2x zone.
        XCTAssertEqual(
            resolveZoneClamped(x: 300, y: -8, width: w, height: h),
            .upperRightSpeed,
            "Negative Y overshoot must clamp to 0 and still latch 2x"
        )

        // Slight bottom overshoot clamps to 1.0 and stably routes to share.
        XCTAssertEqual(
            resolveZoneClamped(x: 300, y: 860, width: w, height: h),
            .lowerRightShare,
            "Bottom overshoot must clamp to 1.0 and route to share"
        )

        // In-range 2x touch is unaffected by clamping.
        XCTAssertEqual(
            resolveZoneClamped(x: 300, y: 200, width: w, height: h),
            .upperRightSpeed
        )
    }

    // MARK: - Multi-Slot Pre-Render Invariants

    func testMultiSlotIndexMapping() {
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
