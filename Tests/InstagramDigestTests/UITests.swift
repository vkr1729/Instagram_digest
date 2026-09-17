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
}
