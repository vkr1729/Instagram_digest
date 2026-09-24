import XCTest
@testable import InstagramDigest

/// Regression tests for the replay-from-end field bug: watching a reel to
/// completion auto-advances, and swiping back must restart it from zero —
/// never strand it on the last frame. AVPlayer ignores play()/rate at
/// end-of-item; only a seek to .zero restarts it.
final class PlaybackRestartTests: XCTestCase {

    func testIsAtEndDetectsCompletedReel() {
        XCTAssertTrue(AVPlayerPool.isAtEnd(currentTime: 10.0, duration: 10.0))
        XCTAssertTrue(AVPlayerPool.isAtEnd(currentTime: 9.8, duration: 10.0))
        XCTAssertTrue(AVPlayerPool.isAtEnd(currentTime: 12.0, duration: 10.0))
    }

    func testIsAtEndIgnoresMidPlayback() {
        XCTAssertFalse(AVPlayerPool.isAtEnd(currentTime: 0.0, duration: 10.0))
        XCTAssertFalse(AVPlayerPool.isAtEnd(currentTime: 5.0, duration: 10.0))
        XCTAssertFalse(AVPlayerPool.isAtEnd(currentTime: 9.5, duration: 10.0))
    }

    func testIsAtEndRejectsInvalidInputs() {
        XCTAssertFalse(AVPlayerPool.isAtEnd(currentTime: .nan, duration: 10.0))
        XCTAssertFalse(AVPlayerPool.isAtEnd(currentTime: 5.0, duration: .infinity))
        XCTAssertFalse(AVPlayerPool.isAtEnd(currentTime: 5.0, duration: 0.0))
        XCTAssertFalse(AVPlayerPool.isAtEnd(currentTime: 5.0, duration: -3.0))
    }

    func testIsAtEndRespectsCustomThreshold() {
        XCTAssertTrue(AVPlayerPool.isAtEnd(currentTime: 9.0, duration: 10.0, threshold: 1.5))
        XCTAssertFalse(AVPlayerPool.isAtEnd(currentTime: 8.0, duration: 10.0, threshold: 1.5))
    }

    func testEndRestartThresholdIsSmallPositive() {
        XCTAssertGreaterThan(AVPlayerPool.endRestartThreshold, 0.0)
        XCTAssertLessThanOrEqual(AVPlayerPool.endRestartThreshold, 1.0)
    }
}
