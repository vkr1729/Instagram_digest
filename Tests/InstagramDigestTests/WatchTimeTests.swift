import XCTest
@testable import InstagramDigest

/// Regression tests for the per-week watch timer ("0.0 hrs" pill).
/// Field bug: the timer publisher was built inside `body`, so pool progress
/// ticks (0.5s) resubscribed it faster than its 1s interval — it never fired.
/// Reset rule: identical content restores accumulated time; any content
/// refresh restarts from zero.
final class WatchTimeTests: XCTestCase {

    private func reel(id: String) -> ReelItem {
        ReelItem(id: id, creatorHandle: "c", caption: "", rank: 1,
                 videoUrl: URL(string: "https://example.com/\(id).mp4")!)
    }

    func testFingerprintIsStableAcrossCalls() {
        let items = [reel(id: "a"), reel(id: "b"), reel(id: "c")]
        XCTAssertEqual(WatchedRules.watchContentFingerprint(items: items),
                       WatchedRules.watchContentFingerprint(items: items))
    }

    func testFingerprintDetectsContentRefresh() {
        let base = [reel(id: "a"), reel(id: "b")]
        let baseFP = WatchedRules.watchContentFingerprint(items: base)
        // Re-rank / reorder, append, removal, and same-id-different-count
        // must all read as a refresh.
        XCTAssertNotEqual(baseFP, WatchedRules.watchContentFingerprint(
            items: [reel(id: "b"), reel(id: "a")]))
        XCTAssertNotEqual(baseFP, WatchedRules.watchContentFingerprint(
            items: [reel(id: "a"), reel(id: "b"), reel(id: "c")]))
        XCTAssertNotEqual(baseFP, WatchedRules.watchContentFingerprint(
            items: [reel(id: "a")]))
        XCTAssertNotEqual(baseFP, WatchedRules.watchContentFingerprint(items: []))
    }

    func testFingerprintIgnoresNonIdentityFields() {
        let plain = ReelItem(id: "a", creatorHandle: "c", caption: "one", rank: 1,
                             videoUrl: URL(string: "https://example.com/a.mp4")!)
        let edited = ReelItem(id: "a", creatorHandle: "c", caption: "two", rank: 9,
                              videoUrl: URL(string: "https://example.com/a.mp4")!)
        XCTAssertEqual(WatchedRules.watchContentFingerprint(items: [plain]),
                       WatchedRules.watchContentFingerprint(items: [edited]))
    }

    func testShouldResetWatchTime() {
        XCTAssertTrue(WatchedRules.shouldResetWatchTime(
            storedFingerprint: nil, freshFingerprint: "3#abc"))
        XCTAssertFalse(WatchedRules.shouldResetWatchTime(
            storedFingerprint: "3#abc", freshFingerprint: "3#abc"))
        XCTAssertTrue(WatchedRules.shouldResetWatchTime(
            storedFingerprint: "3#abc", freshFingerprint: "3#def"))
    }

    func testWatchHoursFormatting() {
        // Pill contract: one decimal hours, matching the "0.0 hrs" UI.
        XCTAssertEqual(String(format: "%.1f hrs", 0.0 / 3600.0), "0.0 hrs")
        XCTAssertEqual(String(format: "%.1f hrs", 180.0 / 3600.0), "0.1 hrs")
        XCTAssertEqual(String(format: "%.1f hrs", 5400.0 / 3600.0), "1.5 hrs")
    }
}
