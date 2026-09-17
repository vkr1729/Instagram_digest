import XCTest
@testable import InstagramDigest

final class ResolverTests: XCTestCase {

    func testComponentSanitization() {
        XCTAssertEqual(LibraryPathResolver.sanitizeComponent("normal_reel"), "normal_reel")
        XCTAssertEqual(LibraryPathResolver.sanitizeComponent("../escape/path"), "escapepath")
        XCTAssertEqual(LibraryPathResolver.sanitizeComponent("/root/path"), "unknown")
        XCTAssertEqual(LibraryPathResolver.sanitizeComponent("~home"), "unknown")
        XCTAssertEqual(LibraryPathResolver.sanitizeComponent(""), "unknown")
    }

    func testPathStructure() {
        let resolver = LibraryPathResolver.shared
        let weekURL = resolver.weekDirectoryURL(for: "2026-09-17")
        XCTAssertTrue(weekURL.path.contains("MediaCache/2026-09-17"))

        let fileURL = resolver.localFileURL(for: "2026-09-17", reelID: "reel_01")
        XCTAssertTrue(fileURL.path.hasSuffix("MediaCache/2026-09-17/reel_01.mp4"))

        let bookmarkURL = resolver.bookmarkFileURL(for: "reel_01")
        XCTAssertTrue(bookmarkURL.path.hasSuffix("MediaCache/Bookmarks/reel_01.mp4"))

        let resumeURL = resolver.resumeDataFileURL(for: "reel_01")
        XCTAssertTrue(resumeURL.path.hasSuffix("MediaCache/ResumeData/reel_01.dat"))
    }
}
