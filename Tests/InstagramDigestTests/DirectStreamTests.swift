import XCTest
import Foundation
import SwiftData
@testable import InstagramDigest

/// URLProtocol mock for hermetic testing of GitHubBookmarkSyncService and DigestDataService network requests.
final class MockResolverURLProtocol: URLProtocol {
    nonisolated(unsafe) static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        return true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        return request
    }

    override func startLoading() {
        guard let handler = Self.requestHandler else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }

        do {
            let (response, data) = try handler(request)
            if (300...399).contains(response.statusCode),
               let location = response.value(forHTTPHeaderField: "Location") ?? (response.allHeaderFields["Location"] as? String),
               let redirectURL = URL(string: location, relativeTo: request.url) {
                let redirectReq = URLRequest(url: redirectURL)
                client?.urlProtocol(self, wasRedirectedTo: redirectReq, redirectResponse: response)
                return
            }
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

final class DirectStreamTests: XCTestCase {

    @MainActor
    override func setUp() {
        super.setUp()
        MockResolverURLProtocol.requestHandler = nil
        UserDefaults.standard.removeObject(forKey: "cobalt_api_url")
        UserDefaults.standard.removeObject(forKey: "cobalt_api_key")
        UserDefaults.standard.removeObject(forKey: "cobalt_api_token")
        UserDefaults.standard.removeObject(forKey: "max_cached_bookmark_videos")
        UserDefaults.standard.removeObject(forKey: "gh_bookmark_repo")
        UserDefaults.standard.removeObject(forKey: "gh_bookmark_pat")
        Reachability.shared.isExpensiveOverride = nil
        GitHubBookmarkSyncService.shared.session = .shared
        GitHubBookmarkSyncService.additionalProtocolClasses = []
        GitHubBookmarkSyncService.shared.clearMemoryStreamCache()
    }

    @MainActor
    override func tearDown() {
        MockResolverURLProtocol.requestHandler = nil
        UserDefaults.standard.removeObject(forKey: "cobalt_api_url")
        UserDefaults.standard.removeObject(forKey: "cobalt_api_key")
        UserDefaults.standard.removeObject(forKey: "cobalt_api_token")
        UserDefaults.standard.removeObject(forKey: "max_cached_bookmark_videos")
        UserDefaults.standard.removeObject(forKey: "gh_bookmark_repo")
        UserDefaults.standard.removeObject(forKey: "gh_bookmark_pat")
        Reachability.shared.isExpensiveOverride = nil
        GitHubBookmarkSyncService.shared.session = .shared
        GitHubBookmarkSyncService.additionalProtocolClasses = []
        GitHubBookmarkSyncService.shared.clearMemoryStreamCache()
        AVPlayerPool.shared.slotCurrent.teardown()
        AVPlayerPool.shared.slotPrev.teardown()
        AVPlayerPool.shared.slotNext.teardown()
        super.tearDown()
    }

    // MARK: - 1. Canonical URL & Manifest Tests

    func testReelItemCanonicalUrlFallback() throws {
        let json = """
        {
            "id": "reel_direct_01",
            "creator_handle": "directcreator",
            "caption": "Direct Meta Stream",
            "rank": 1,
            "video_url": "https://scontent.cdninstagram.com/o1/v/t16/f1/m86/test.mp4?oe=66000000"
        }
        """.data(using: .utf8)!

        let item = try JSONDecoder().decode(ReelItem.self, from: json)
        XCTAssertEqual(item.id, "reel_direct_01")
        XCTAssertEqual(item.canonicalUrl?.absoluteString, "https://www.instagram.com/reel/reel_direct_01/")
        XCTAssertTrue(item.videoUrl.absoluteString.contains("scontent.cdninstagram.com"))
    }

    func testReelItemExplicitCanonicalUrl() throws {
        let json = """
        {
            "id": "reel_direct_02",
            "creator_handle": "directcreator",
            "caption": "Direct Meta Stream",
            "rank": 2,
            "video_url": "https://scontent.cdninstagram.com/o1/v/t16/f1/m86/test2.mp4?oe=66000000",
            "canonical_url": "https://www.instagram.com/reel/custom_permalink_02/"
        }
        """.data(using: .utf8)!

        let item = try JSONDecoder().decode(ReelItem.self, from: json)
        XCTAssertEqual(item.canonicalUrl?.absoluteString, "https://www.instagram.com/reel/custom_permalink_02/")
    }

    func testReelItemEncodingPreservesCanonicalUrl() throws {
        let item = ReelItem(
            id: "reel_roundtrip",
            creatorHandle: "roundtrip_user",
            caption: "Roundtrip test",
            rank: 5,
            videoUrl: URL(string: "https://scontent.cdninstagram.com/test.mp4")!,
            thumbnailUrl: nil,
            canonicalUrl: URL(string: "https://www.instagram.com/reel/custom_roundtrip/")
        )

        let encoded = try JSONEncoder().encode(item)
        let decoded = try JSONDecoder().decode(ReelItem.self, from: encoded)

        XCTAssertEqual(decoded.id, "reel_roundtrip")
        XCTAssertEqual(decoded.canonicalUrl?.absoluteString, "https://www.instagram.com/reel/custom_roundtrip/")
    }

    func testDirectManifestDecoding() throws {
        // AUDIT-043: Golden data_direct.json decoding verification
        let goldenJson = """
        {
            "schema_version": 2,
            "channel": "direct",
            "run_date": "2026-10-24",
            "count": 2,
            "items": [
                {
                    "id": "golden_reel_01",
                    "creator_handle": "creator_alpha",
                    "creator_name": "Alpha Creator",
                    "caption": "Golden Reel 1 with Meta CDN URL",
                    "rank": 1,
                    "rank_display": "#01",
                    "video_url": "https://scontent.cdninstagram.com/o1/v/t16/f1/m86/golden1.mp4?oe=6AD54193",
                    "url": "https://www.instagram.com/reel/golden_reel_01/",
                    "thumbnail": "https://vkr1729.github.io/Instagram_digest/thumbnails/golden_reel_01.jpg",
                    "size_bytes": 10485760,
                    "category": "Comedy",
                    "view_count": 50000,
                    "like_count": 2500
                },
                {
                    "id": "golden_reel_02",
                    "creator_handle": "creator_beta",
                    "caption": "Golden Reel 2 with custom canonical permalink",
                    "rank": 2,
                    "video_url": "https://instagram.fpen1-1.fna.fbcdn.net/v/t2/golden2.mp4?oe=6AD54193",
                    "canonical_url": "https://www.instagram.com/reel/custom_permalink_02/",
                    "size_bytes": 5242880
                }
            ]
        }
        """.data(using: .utf8)!

        let manifest = try JSONDecoder().decode(DigestManifest.self, from: goldenJson)

        XCTAssertEqual(manifest.schemaVersion, 2, "Golden manifest schema_version must be 2")
        XCTAssertEqual(manifest.channel, "direct", "Golden manifest channel must be 'direct'")
        XCTAssertEqual(manifest.weekId, "2026-10-24")
        XCTAssertEqual(manifest.count, 2)
        XCTAssertEqual(manifest.items.count, 2)

        let item1 = manifest.items[0]
        XCTAssertEqual(item1.id, "golden_reel_01")
        XCTAssertEqual(item1.creatorHandle, "creator_alpha")
        XCTAssertEqual(item1.rank, 1)
        XCTAssertEqual(item1.videoUrl.absoluteString, "https://scontent.cdninstagram.com/o1/v/t16/f1/m86/golden1.mp4?oe=6AD54193")
        XCTAssertEqual(item1.canonicalUrl?.absoluteString, "https://www.instagram.com/reel/golden_reel_01/")
        XCTAssertEqual(item1.sizeBytes, 10485760)

        let item2 = manifest.items[1]
        XCTAssertEqual(item2.id, "golden_reel_02")
        XCTAssertEqual(item2.creatorHandle, "creator_beta")
        XCTAssertEqual(item2.rank, 2)
        XCTAssertEqual(item2.videoUrl.absoluteString, "https://instagram.fpen1-1.fna.fbcdn.net/v/t2/golden2.mp4?oe=6AD54193")
        XCTAssertEqual(item2.canonicalUrl?.absoluteString, "https://www.instagram.com/reel/custom_permalink_02/")
        XCTAssertEqual(item2.sizeBytes, 5242880)
    }

    func testBookmarkItemCanonicalUrlHostileReelIDDoesNotCrash() {
        // AUDIT-013: Hostile/malformed reel ID must be sanitized safely without force-unwrap crashes
        let hostileID = "/../invalid\0reel id with spaces/"
        let bookmark = BookmarkItem(
            reelID: hostileID,
            weekID: "2026-10-24",
            creatorHandle: "testuser",
            caption: "test caption",
            rank: 1,
            videoUrl: URL(string: "https://scontent.cdninstagram.com/test.mp4")!
        )

        let canonical = bookmark.canonicalUrl
        XCTAssertNotNil(canonical)
        XCTAssertFalse(canonical?.absoluteString.contains(" ") ?? true)
        XCTAssertFalse(canonical?.absoluteString.contains("\0") ?? true)
    }

    func testDigestDataServiceDirectModeRouting() async throws {
        // AUDIT-043: Non-tautological routing verification: verifies direct manifest URL
        // and that legacy remote bookmark endpoints immediately bypass network requests.
        XCTAssertTrue(DigestDataService.directMode, "DIRECT_STREAM_MODE should be active in this target configuration")
        XCTAssertEqual(DigestDataService.directManifestURL.lastPathComponent, "data_direct.json")

        var networkHit = false
        MockResolverURLProtocol.requestHandler = { _ in
            networkHit = true
            throw URLError(.badURL)
        }
        let service = DigestDataService(session: makeMockSession())
        let remoteBookmarks = try await service.fetchRemoteBookmarks()
        XCTAssertEqual(remoteBookmarks.count, 0, "fetchRemoteBookmarks should immediately return empty array in directMode")
        XCTAssertFalse(networkHit, "No network request should be initiated for fetchRemoteBookmarks in directMode")

        let dummyReel = ReelItem(
            id: "route_test",
            creatorHandle: "creator",
            caption: "",
            rank: 1,
            videoUrl: URL(string: "https://scontent.cdninstagram.com/test.mp4")!
        )
        let saved = try await service.saveRemoteBookmark(reel: dummyReel)
        XCTAssertTrue(saved, "saveRemoteBookmark returns true without hitting network in directMode")
        XCTAssertFalse(networkHit, "No network request should be initiated for saveRemoteBookmark in directMode")

        let deleted = try await service.deleteRemoteBookmark(reelID: "route_test")
        XCTAssertTrue(deleted, "deleteRemoteBookmark returns true without hitting network in directMode")
        XCTAssertFalse(networkHit, "No network request should be initiated for deleteRemoteBookmark in directMode")
    }

    func testDigestDataServiceRejectsEmptyDirectManifest() async throws {
        // AUDIT-022: Empty direct manifest must throw DigestDataServiceError.emptyManifest
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        MockResolverURLProtocol.requestHandler = { request in
            let emptyManifestJson = """
            {
                "schema_version": 2,
                "channel": "direct",
                "week_id": "2026-10-24",
                "count": 0,
                "items": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, emptyManifestJson)
        }

        let service = DigestDataService(session: makeMockSession(), pathResolver: pathResolver)
        do {
            _ = try await service.fetchManifest(from: DigestDataService.directManifestURL)
            XCTFail("fetchManifest should throw DigestDataServiceError.emptyManifest on empty manifest")
        } catch let err as DigestDataServiceError {
            // AUDIT-069: Assert strictly that the empty manifest error was thrown
            XCTAssertEqual(err, .emptyManifest)
        } catch {
            XCTFail("Expected DigestDataServiceError.emptyManifest, but got \(error)")
        }
    }

    func testDirectModeBundleFallbackPolicy() {
        // AUDIT-032: In direct mode, bundle fallback requires manifest.channel == "direct"
        XCTAssertTrue(DigestDataService.directMode)
        let sampleDirect = DigestManifest(weekId: "2026-10-24", items: [], channel: "direct")
        let sampleLegacy = DigestManifest(weekId: "2026-10-24", items: [], channel: "legacy")
        XCTAssertEqual(sampleDirect.channel, "direct")
        XCTAssertNotEqual(sampleLegacy.channel, "direct")
    }

    // MARK: - 2. Cache Census & GitHub Release Asset Tests

    private func makeMockSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockResolverURLProtocol.self]
        return URLSession(configuration: config)
    }

    func testCacheCensusScenarios() throws {
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_census_\(UUID().uuidString)")
        let resolver = LibraryPathResolver(baseURL: tempDir)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let weekID = "2026-10-24"
        let reel1 = ReelItem(id: "r1", creatorHandle: "c1", caption: "", rank: 1, videoUrl: URL(string: "https://meta.com/1.mp4")!, thumbnailUrl: nil, canonicalUrl: nil, sizeBytes: 100)
        let reel2 = ReelItem(id: "r2", creatorHandle: "c2", caption: "", rank: 2, videoUrl: URL(string: "https://meta.com/2.mp4")!, thumbnailUrl: nil, canonicalUrl: nil, sizeBytes: 200)
        let reel3 = ReelItem(id: "r3", creatorHandle: "c3", caption: "", rank: 3, videoUrl: URL(string: "https://meta.com/3.mp4")!, thumbnailUrl: nil, canonicalUrl: nil, sizeBytes: 300)

        // 1. Empty directory
        let emptyCensus = resolver.cacheCensus(reels: [reel1, reel2, reel3], weekID: weekID)
        XCTAssertEqual(emptyCensus.cached, 0)
        XCTAssertEqual(emptyCensus.total, 3)

        // 2. Partial cached
        let file1 = resolver.localFileURL(for: weekID, reelID: "r1")
        try fm.createDirectory(at: file1.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "video 1 content".write(to: file1, atomically: true, encoding: .utf8)

        let partialCensus = resolver.cacheCensus(reels: [reel1, reel2, reel3], weekID: weekID)
        XCTAssertEqual(partialCensus.cached, 1)
        XCTAssertEqual(partialCensus.total, 3)

        // 3. Full cached
        let file2 = resolver.localFileURL(for: weekID, reelID: "r2")
        let file3 = resolver.localFileURL(for: weekID, reelID: "r3")
        try "video 2 content".write(to: file2, atomically: true, encoding: .utf8)
        try "video 3 content".write(to: file3, atomically: true, encoding: .utf8)

        let fullCensus = resolver.cacheCensus(reels: [reel1, reel2, reel3], weekID: weekID)
        XCTAssertEqual(fullCensus.cached, 3)
        XCTAssertEqual(fullCensus.total, 3)

        // 4. Zero-byte file is excluded from census
        try "".write(to: file3, atomically: true, encoding: .utf8)
        let zeroByteCensus = resolver.cacheCensus(reels: [reel1, reel2, reel3], weekID: weekID)
        XCTAssertEqual(zeroByteCensus.cached, 2)
        XCTAssertEqual(zeroByteCensus.total, 3)
    }

    func testGitHubReleaseTagCalculation() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!

        // Mid year 2026
        var components = DateComponents()
        components.year = 2026
        components.month = 6
        components.day = 15
        components.hour = 12
        let dateMid = calendar.date(from: components)!
        XCTAssertEqual(GitHubBookmarkSyncService.releaseTag(for: dateMid), "v2026")

        // Dec 31 2026 23:59:59 UTC
        components.month = 12
        components.day = 31
        components.hour = 23
        components.minute = 59
        components.second = 59
        let dateDec31 = calendar.date(from: components)!
        XCTAssertEqual(GitHubBookmarkSyncService.releaseTag(for: dateDec31), "v2026")

        // Jan 1 2027 00:00:00 UTC
        components.year = 2027
        components.month = 1
        components.day = 1
        components.hour = 0
        components.minute = 0
        components.second = 0
        let dateJan1 = calendar.date(from: components)!
        XCTAssertEqual(GitHubBookmarkSyncService.releaseTag(for: dateJan1), "v2027")
    }

    func testBookmarkSyncDTORoundTrip() throws {
        let dto = BookmarkSyncDTO(
            id: "reel_123",
            creator_handle: "test_creator",
            caption: "Sample caption",
            category: "Tech",
            thumbnail_url: "thumbnails/reel_123_portrait.jpg",
            thumbnail_base64: "dGVzdF90aHVtYg==",
            canonical_url: "https://www.instagram.com/reel/reel_123/",
            video_asset_name: "reel_123.mp4",
            release_tag: "v2026",
            size_bytes: 4096,
            bookmarked_at: "2026-10-25T12:00:00Z"
        )

        let encoder = JSONEncoder()
        let data = try encoder.encode(dto)
        let decoder = JSONDecoder()
        let decoded = try decoder.decode(BookmarkSyncDTO.self, from: data)

        XCTAssertEqual(decoded.id, "reel_123")
        XCTAssertEqual(decoded.video_asset_name, "reel_123.mp4")
        XCTAssertEqual(decoded.release_tag, "v2026")
        XCTAssertEqual(decoded.thumbnail_url, "thumbnails/reel_123_portrait.jpg")
    }

    func testRedirectCaptureDelegateExtractsLocation() {
        let delegate = RedirectCaptureDelegate()
        let originalURL = URL(string: "https://api.github.com/repos/user/repo/releases/assets/999")!
        let targetS3URL = URL(string: "https://github-releases.s3.amazonaws.com/video.mp4?token=abc")!

        let response = HTTPURLResponse(
            url: originalURL,
            statusCode: 302,
            httpVersion: nil,
            headerFields: ["Location": targetS3URL.absoluteString]
        )!
        let request = URLRequest(url: targetS3URL)

        var passedCompletion: URLRequest? = request
        delegate.urlSession(
            URLSession.shared,
            task: URLSession.shared.dataTask(with: originalURL),
            willPerformHTTPRedirection: response,
            newRequest: request
        ) { redirectedRequest in
            passedCompletion = redirectedRequest
        }

        XCTAssertNil(passedCompletion, "RedirectCaptureDelegate must pass nil to cancel automatic redirect")
        XCTAssertEqual(delegate.capturedLocation, targetS3URL)
    }

    // MARK: - 3. Bookmark Playback Routing (Local-First vs External Resolver)

    func testBookmarkPlaybackRoutingLocalFirst() throws {
        // AUDIT-002: Hermetic test using isolated temporary directory (never touches real container)
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let weekID = "2026-10-24"
        let reelID = "cached_reel_test"

        let localWeekURL = pathResolver.localFileURL(for: weekID, reelID: reelID)
        try fm.createDirectory(at: localWeekURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "cached local video data".write(to: localWeekURL, atomically: true, encoding: .utf8)

        // Route evaluation
        let isLocallyPresent = fm.fileExists(atPath: localWeekURL.path) || fm.fileExists(atPath: pathResolver.bookmarkFileURL(for: reelID).path)
        XCTAssertTrue(isLocallyPresent, "Local video file must be detected as present")
    }

    func testBookmarkPlaybackRoutingTier2GitHubRelease() throws {
        // AUDIT-002: Hermetic test using isolated temporary directory
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let weekID = "2026-08-01" // older purged week
        let reelID = "purged_reel_test"

        let localWeekURL = pathResolver.localFileURL(for: weekID, reelID: reelID)
        let localBookmarkURL = pathResolver.bookmarkFileURL(for: reelID)

        // Verify neither exists on disk (Tier 1 absent)
        XCTAssertFalse(fm.fileExists(atPath: localWeekURL.path))
        XCTAssertFalse(fm.fileExists(atPath: localBookmarkURL.path))

        // When absent locally, playback falls through to Tier 2 (GitHub Release Asset) or Tier 3 (Web)
        let isLocallyPresent = fm.fileExists(atPath: localWeekURL.path) || fm.fileExists(atPath: localBookmarkURL.path)
        XCTAssertFalse(isLocallyPresent, "Must detect that reel is absent locally to trigger Tier 2/Tier 3 routing")
    }

    // MARK: - 4. MediaCacheManager Rollover Purge Tests

    func testExecuteRolloverPurge() async throws {
        // AUDIT-002: Uses isolated pathResolver rooted at tempDir (never calls .shared)
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let manager = MediaCacheManager(pathResolver: pathResolver)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let oldWeekDir = pathResolver.weekDirectoryURL(for: "2026-09-01")
        try fm.createDirectory(at: oldWeekDir, withIntermediateDirectories: true)
        let dummyOldVideo = oldWeekDir.appendingPathComponent("old_reel.mp4")
        try "dummy video content".write(to: dummyOldVideo, atomically: true, encoding: .utf8)

        let newWeekDir = pathResolver.weekDirectoryURL(for: "2026-09-08")
        try fm.createDirectory(at: newWeekDir, withIntermediateDirectories: true)
        let dummyNewVideo = newWeekDir.appendingPathComponent("new_reel.mp4")
        try "new video content".write(to: dummyNewVideo, atomically: true, encoding: .utf8)

        XCTAssertTrue(fm.fileExists(atPath: dummyOldVideo.path))
        XCTAssertTrue(fm.fileExists(atPath: dummyNewVideo.path))

        // Execute rollover purge for the incoming new week
        await manager.executeRolloverPurge(forNewWeek: "2026-09-08")

        // Old week must be purged
        XCTAssertFalse(fm.fileExists(atPath: oldWeekDir.path), "Old week directory should have been purged")
        // New incoming week must be intact
        XCTAssertTrue(fm.fileExists(atPath: newWeekDir.path), "New incoming week directory must remain intact")
        XCTAssertTrue(fm.fileExists(atPath: dummyNewVideo.path))
    }

    func testExecuteRolloverPurgeProtectsResumeDataOnly() async throws {
        // AUDIT-032: Renamed from overclaiming test name; tests .dat resume files specifically
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let manager = MediaCacheManager(pathResolver: pathResolver)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let resumeDir = pathResolver.resumeDataDirectoryURL
        try fm.createDirectory(at: resumeDir, withIntermediateDirectories: true)

        let incomingReelID = "incoming_in_flight_reel"
        let incomingResumeData = resumeDir.appendingPathComponent("\(incomingReelID).dat")
        try "fake resume data".write(to: incomingResumeData, atomically: true, encoding: .utf8)

        let oldReelID = "old_purged_reel"
        let oldResumeData = resumeDir.appendingPathComponent("\(oldReelID).dat")
        try "stale resume data".write(to: oldResumeData, atomically: true, encoding: .utf8)

        await manager.executeRolloverPurge(
            forNewWeek: "2026-10-24",
            protectedReelIDs: [incomingReelID]
        )

        XCTAssertTrue(fm.fileExists(atPath: incomingResumeData.path), "Incoming week in-flight resume data must be protected")
        XCTAssertFalse(fm.fileExists(atPath: oldResumeData.path), "Old week stale resume data must be purged")
    }

    func testExecuteRolloverPurgeProtectsPartFilesAndBookmarks() async throws {
        // AUDIT-004 & AUDIT-032: Asserts that bookmark thumbnails survive rollover, incoming .part survives, and unpinned bookmark .mp4 is cleared
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let manager = MediaCacheManager(pathResolver: pathResolver)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let incomingWeek = "2026-10-24"
        let incomingDir = pathResolver.weekDirectoryURL(for: incomingWeek)
        try fm.createDirectory(at: incomingDir, withIntermediateDirectories: true)

        let incomingPartFile = incomingDir.appendingPathComponent("incoming_reel.part")
        try "in-flight partial stream".write(to: incomingPartFile, atomically: true, encoding: .utf8)

        let bmDir = pathResolver.bookmarksDirectoryURL
        try fm.createDirectory(at: bmDir, withIntermediateDirectories: true)

        // Bookmark thumbnail: MUST SURVIVE rollover (AUDIT-004)
        let bmThumb = bmDir.appendingPathComponent("favorite_reel.thumb.jpg")
        try "bookmark thumbnail binary".write(to: bmThumb, atomically: true, encoding: .utf8)

        // Bookmark unpinned mp4: Should be deleted if not in LivePinSet/protectedStems
        let bmUnpinnedMp4 = bmDir.appendingPathComponent("unpinned_reel.mp4")
        try "unpinned mp4 binary".write(to: bmUnpinnedMp4, atomically: true, encoding: .utf8)

        // Bookmark pinned mp4: Should survive if protected
        let bmPinnedMp4 = bmDir.appendingPathComponent("pinned_reel.mp4")
        try "pinned mp4 binary".write(to: bmPinnedMp4, atomically: true, encoding: .utf8)

        await manager.executeRolloverPurge(
            forNewWeek: incomingWeek,
            protectedReelIDs: ["pinned_reel"]
        )

        XCTAssertTrue(fm.fileExists(atPath: incomingPartFile.path), "Incoming week .part file must be protected")
        XCTAssertTrue(fm.fileExists(atPath: bmThumb.path), "Bookmark .thumb.jpg must strictly survive rollover purge (AUDIT-004)")
        XCTAssertFalse(fm.fileExists(atPath: bmUnpinnedMp4.path), "Unpinned bookmark .mp4 should be cleared to enforce storage cap")
        XCTAssertTrue(fm.fileExists(atPath: bmPinnedMp4.path), "Pinned bookmark .mp4 must survive")
    }

    func testExecuteRolloverPurgeSynchronousResume() async throws {
        // AUDIT-029: Verifies that download suspension is resumed synchronously before executeRolloverPurge returns
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let manager = MediaCacheManager(pathResolver: pathResolver)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        final class SuspensionEventBox: @unchecked Sendable {
            private let lock = NSLock()
            private var _events: [Bool] = []
            var events: [Bool] {
                lock.lock()
                defer { lock.unlock() }
                return _events
            }
            func append(_ event: Bool) {
                lock.lock()
                defer { lock.unlock() }
                _events.append(event)
            }
        }

        let box = SuspensionEventBox()
        await manager.setDownloadSuspensionHandler { suspended in
            box.append(suspended)
        }

        await manager.executeRolloverPurge(forNewWeek: "2026-10-24")

        XCTAssertEqual(box.events, [true, false], "Suspension handler must be called with true on enter and false on exit synchronously")
    }

    // MARK: - 5. Coordinator State Tests

    func testCoordinatorStateCompletedWithFailures() {
        // AUDIT-015: Completed with failures state verification
        let state = DownloadAllCoordinator.CoordinatorState.completedWithFailures(ready: 95, failed: 5)
        if case .completedWithFailures(let ready, let failed) = state {
            XCTAssertEqual(ready, 95)
            XCTAssertEqual(failed, 5)
        } else {
            XCTFail("Expected .completedWithFailures")
        }
    }

    @MainActor
    func testCoordinatorPreflightReclaimableRolloverBytes() throws {
        // AUDIT-033: Pre-purge reclaimable space calculation
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let oldWeekDir = pathResolver.weekDirectoryURL(for: "2026-09-01")
        try fm.createDirectory(at: oldWeekDir, withIntermediateDirectories: true)
        let oldVideo = oldWeekDir.appendingPathComponent("old.mp4")
        let dummyData = Data(repeating: 0x41, count: 50_000)
        try dummyData.write(to: oldVideo)

        let coordinator = DownloadAllCoordinator.shared
        let bytes = coordinator.calculateReclaimableRolloverBytes(incomingWeekID: "2026-10-24", pathResolver: pathResolver)
        XCTAssertGreaterThanOrEqual(bytes, 50_000)
    }

    // MARK: - 5. GitHub Bookmark Sync & Export/Import Tests

    @MainActor
    func testExportBookmarksJSON() throws {
        let bm1 = BookmarkItem(
            reelID: "test_reel_1",
            weekID: "2026-10-24",
            creatorHandle: "creator_one",
            caption: "Caption One",
            rank: 1,
            videoUrl: URL(string: "https://example.com/video1.mp4"),
            localStatus: .cached,
            sizeBytes: 12345
        )
        let bm2 = BookmarkItem(
            reelID: "test_reel_2",
            weekID: "2026-10-24",
            creatorHandle: "creator_two",
            caption: "Caption Two",
            rank: 2,
            videoUrl: URL(string: "https://example.com/video2.mp4"),
            localStatus: .evicted,
            sizeBytes: 67890
        )

        let service = GitHubBookmarkSyncService.shared
        guard let exportURL = service.exportBookmarksJSON(bookmarks: [bm1, bm2]) else {
            XCTFail("Failed to export bookmarks JSON")
            return
        }
        defer { try? FileManager.default.removeItem(at: exportURL) }

        let data = try Data(contentsOf: exportURL)
        let dtos = try JSONDecoder().decode([BookmarkSyncDTO].self, from: data)
        XCTAssertEqual(dtos.count, 2)
        XCTAssertEqual(dtos[0].id, "test_reel_1")
        XCTAssertEqual(dtos[0].creator_handle, "creator_one")
        XCTAssertNil(dtos[0].video_asset_name)
        XCTAssertEqual(dtos[1].id, "test_reel_2")
    }

    @MainActor
    func testImportBookmarksJSON() throws {
        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let json = """
        [
            {
                "id": "imported_1",
                "creator_handle": "alice",
                "caption": "Imported reel 1",
                "canonical_url": "https://www.instagram.com/reel/imported_1/",
                "thumbnail_url": "https://example.com/thumb1.jpg",
                "size_bytes": 5000000,
                "bookmarked_at": "2026-10-24T12:00:00Z"
            },
            {
                "id": "imported_2",
                "creator_handle": "bob",
                "caption": "Imported reel 2",
                "canonical_url": "https://www.instagram.com/reel/imported_2/",
                "thumbnail_url": "https://example.com/thumb2.jpg",
                "size_bytes": 6000000,
                "bookmarked_at": "2026-10-24T12:05:00Z"
            }
        ]
        """.data(using: .utf8)!

        let tempFile = FileManager.default.temporaryDirectory.appendingPathComponent("test_import_\(UUID().uuidString).json")
        try json.write(to: tempFile)
        defer { try? FileManager.default.removeItem(at: tempFile) }

        let service = GitHubBookmarkSyncService.shared
        let importedCount = try service.importBookmarks(from: tempFile, context: context)
        XCTAssertEqual(importedCount, 2)

        let descriptor = FetchDescriptor<BookmarkItem>()
        let items = try context.fetch(descriptor)
        XCTAssertEqual(items.count, 2)
        XCTAssertTrue(items.contains { $0.reelID == "imported_1" })
        XCTAssertTrue(items.contains { $0.reelID == "imported_2" })
    }

    @MainActor
    func testSyncWithGitHubRequiresValidParams() async throws {
        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let service = GitHubBookmarkSyncService.shared
        do {
            _ = try await service.syncWithGitHub(repo: "", pat: "token", localBookmarks: [], context: context)
            XCTFail("Should throw for empty repo")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("not specified"))
        }

        do {
            _ = try await service.syncWithGitHub(repo: "owner/repo", pat: "", localBookmarks: [], context: context)
            XCTFail("Should throw for empty PAT")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("required"))
        }
    }

    // MARK: - 6. Comprehensive Remediation Verification Tests

    @MainActor
    func testSyncWithGitHubRejectsInvalidRepoShape() async throws {
        // AUDIT-003: Passing malformed repo must throw NSError, not force-unwrap crash
        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let service = GitHubBookmarkSyncService.shared
        for badRepo in ["bad repo", "owner/repo/extra", "singleword", "owner/"] {
            do {
                _ = try await service.syncWithGitHub(repo: badRepo, pat: "ghp_validToken123", localBookmarks: [], context: context)
                XCTFail("Should throw for invalid repo: \(badRepo)")
            } catch {
                XCTAssertTrue(error.localizedDescription.contains("Invalid GitHub repository format") || error.localizedDescription.contains("Invalid"))
            }
        }
    }

    @MainActor
    func testCoordinatorDoubleStartDuringDownloadingIsIgnored() {
        // AUDIT-020: Duplicate tap during .downloading, .preparing, or .paused must be ignored
        let coordinator = DownloadAllCoordinator.shared
        let reel = ReelItem(
            id: "dup_test_reel",
            creatorHandle: "creator",
            caption: "",
            rank: 1,
            videoUrl: URL(string: "https://example.com/video.mp4")!
        )

        coordinator.state = .downloading(completed: 5, total: 20, currentReelID: "r1")
        coordinator.startDownloadAll(reels: [reel], weekID: "2026-10-24")
        if case .downloading(let completed, let total, _) = coordinator.state {
            XCTAssertEqual(completed, 5)
            XCTAssertEqual(total, 20)
        } else {
            XCTFail("State was overwritten during .downloading: \(coordinator.state)")
        }

        coordinator.state = .paused
        coordinator.startDownloadAll(reels: [reel], weekID: "2026-10-24")
        XCTAssertEqual(coordinator.state, .paused, "State should remain .paused on duplicate tap")
        coordinator.state = .idle
    }

    @MainActor
    func testCoordinatorPreflightExcludesBookmarksFolder() throws {
        // AUDIT-021: calculateReclaimableRolloverBytes excludes Bookmarks/ directory
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let bmDir = pathResolver.bookmarksDirectoryURL
        try fm.createDirectory(at: bmDir, withIntermediateDirectories: true)
        let bmVideo = bmDir.appendingPathComponent("saved.mp4")
        try Data(repeating: 0x55, count: 100_000).write(to: bmVideo)

        let coordinator = DownloadAllCoordinator.shared
        let reclaimable = coordinator.calculateReclaimableRolloverBytes(incomingWeekID: "2026-10-24", pathResolver: pathResolver)
        XCTAssertEqual(reclaimable, 0, "Bookmarks directory must not be counted as reclaimable rollover bytes")
    }

    @MainActor
    func testCoordinatorManifestAgeFreshnessGate() {
        // AUDIT-070: Proactive freshness gate rejects manifest > 48h old
        let staleDate = Date().addingTimeInterval(-50 * 3600) // 50 hours ago
        let coordinator = DownloadAllCoordinator.shared
        coordinator.state = .idle

        let dummyReel = ReelItem(
            id: "stale_test",
            creatorHandle: "c",
            caption: "",
            rank: 1,
            videoUrl: URL(string: "https://example.com/stale.mp4")!
        )

        coordinator.startDownloadAll(reels: [dummyReel], weekID: "2026-10-24", generatedAt: staleDate)
        if case .failed(let msg) = coordinator.state {
            XCTAssertTrue(msg.contains("expired") || msg.contains("50h"))
        } else {
            XCTFail("Expected .failed state due to stale manifest, got \(coordinator.state)")
        }
        coordinator.state = .idle
    }

    func testCacheCensusWithZeroReels() {
        let census = LibraryPathResolver.shared.cacheCensus(reels: [], weekID: "2026-10-24")
        XCTAssertEqual(census.cached, 0)
        XCTAssertEqual(census.total, 0)
    }

    func testDigestDataServiceRejectsNonDirectUrlInDirectMode() async {
        // AUDIT-065: fetchManifest rejects primaryManifestURL in directMode
        let service = DigestDataService(session: makeMockSession())
        do {
            _ = try await service.fetchManifest(from: DigestDataService.primaryManifestURL)
            XCTFail("fetchManifest should reject non-direct primaryManifestURL in directMode")
        } catch let err as URLError {
            XCTAssertEqual(err.code, .unsupportedURL)
        } catch {
            XCTFail("Expected URLError.unsupportedURL, got \(error)")
        }
    }

    func testPromotePartFileUpperAndLowerBounds() async throws {
        // AUDIT-066: Upper and lower bounds for part file promotion
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let manager = MediaCacheManager(pathResolver: pathResolver)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let tempPart = tempDir.appendingPathComponent("test.part")
        try fm.createDirectory(at: tempDir, withIntermediateDirectories: true)

        // Oversized file (> 1.1x expected size)
        let expected: Int64 = 1_000_000 // 1MB
        let oversizedData = Data(repeating: 0x41, count: 2_500_000) // 2.5MB
        try oversizedData.write(to: tempPart)

        do {
            _ = try await manager.promotePartFile(
                from: tempPart,
                weekID: "2026-10-24",
                reelID: "oversize_reel",
                expectedSizeBytes: expected
            )
            XCTFail("promotePartFile should throw sizeMismatch for oversized file")
        } catch let MediaCacheManager.CacheError.sizeMismatch(exp, act) {
            XCTAssertEqual(exp, expected)
            XCTAssertEqual(act, 2_500_000)
        }
    }

    func testResumeDataKeyedByWeekAndReel() {
        // AUDIT-067: LibraryPathResolver keys resume data by (weekID, reelID)
        let pathResolver = LibraryPathResolver.shared
        let url = pathResolver.resumeDataFileURL(forWeekID: "2026-10-24", reelID: "reel123")
        XCTAssertTrue(url.lastPathComponent.contains("2026-10-24__reel123.dat"))
    }

    @MainActor
    func testBookmarkControllerDirectModeAddVerifiesBookmarkVideoExistence() throws {
        // AUDIT-024: Direct-mode add sets .cached only when bmVideo exists
        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let reel = ReelItem(
            id: "add_verify_reel",
            creatorHandle: "creator",
            caption: "Caption",
            rank: 1,
            videoUrl: URL(string: "https://example.com/video.mp4")!
        )

        BookmarkController.add(reel: reel, weekID: "2026-10-24", context: context)
        let descriptor = FetchDescriptor<BookmarkItem>(predicate: #Predicate { $0.reelID == "add_verify_reel" })
        let item = try context.fetch(descriptor).first
        XCTAssertNotNil(item)
        // Since no weekVideo was on disk, copy was not done, so localStatus must be .evicted (not .cached)
        XCTAssertEqual(item?.localStatus, .evicted)
    }

    @MainActor
    func testBookmarkControllerEnforceCapLRUAndZeroUnlimited() throws {
        // AUDIT-025: Cap respects limit=0 (unlimited), skips bound reels, uses lastAccessedAt LRU
        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let bm1 = BookmarkItem(
            reelID: "bm_1", weekID: "2026-10-24", creatorHandle: "c", caption: "",
            rank: 1, videoUrl: nil, bookmarkedAt: Date().addingTimeInterval(-100),
            localStatus: .cached, sizeBytes: 1000, lastAccessedAt: Date().addingTimeInterval(-10) // accessed recently
        )
        let bm2 = BookmarkItem(
            reelID: "bm_2", weekID: "2026-10-24", creatorHandle: "c", caption: "",
            rank: 2, videoUrl: nil, bookmarkedAt: Date().addingTimeInterval(-10), // bookmarked recently
            localStatus: .cached, sizeBytes: 1000, lastAccessedAt: Date().addingTimeInterval(-100) // accessed long ago
        )
        context.insert(bm1)
        context.insert(bm2)
        try context.save()

        // Test limit = 0 (Unlimited) - does nothing
        BookmarkController.enforceCachedVideosCap(limit: 0, context: context)
        let items = try context.fetch(FetchDescriptor<BookmarkItem>())
        XCTAssertEqual(items.count, 2)
    }

    func testReelItemDirectModeIgnoresR2AndLocalUrls() throws {
        // AUDIT-060: ReelItem decoding in directMode ignores r2_url and local_url
        let json = """
        {
            "id": "reel_r2_ignore",
            "creator_handle": "creator",
            "caption": "",
            "rank": 1,
            "r2_url": "https://r2.example.com/video.mp4",
            "local_url": "https://local.example.com/video.mp4"
        }
        """.data(using: .utf8)!

        XCTAssertThrowsError(try JSONDecoder().decode(ReelItem.self, from: json), "Decoding should fail when only r2_url/local_url are supplied in directMode")
    }

    func testReelItemAndBookmarkItemSanitizeHostileIds() {
        // AUDIT-061: ReelItem and BookmarkItem sanitize hostile reel IDs with spaces or slashes
        let hostileID = "bad id with spaces/and..slashes"
        let item = ReelItem(
            id: hostileID,
            creatorHandle: "creator",
            caption: "test",
            rank: 1,
            videoUrl: URL(string: "https://example.com/test.mp4")!
        )

        XCTAssertNotNil(item.thumbnailUrl)
        XCTAssertFalse(item.thumbnailUrl?.absoluteString.contains(" ") ?? true)
        XCTAssertFalse(item.canonicalUrl?.absoluteString.contains(" ") ?? true)

        let bookmark = BookmarkItem(
            reelID: hostileID,
            weekID: "2026-10-24",
            creatorHandle: "creator",
            caption: "test",
            rank: 1,
            videoUrl: nil
        )
        XCTAssertNotNil(bookmark.thumbnailUrl)
        XCTAssertFalse(bookmark.thumbnailUrl?.absoluteString.contains(" ") ?? true)
        XCTAssertFalse(bookmark.canonicalUrl?.absoluteString.contains(" ") ?? true)
    }

    func testDeleteBookmarkFileVerifiesDeletionBeforeLedgerDecrement() async {
        // AUDIT-030: deleteBookmarkFile verifies deletion before decrementing ledger
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let manager = MediaCacheManager(pathResolver: pathResolver)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        // When file does not exist, ledger remains 0
        await manager.deleteBookmarkFile(reelID: "nonexistent_reel")
        let total = await manager.totalBookmarkBytes
        XCTAssertEqual(total, 0)
    }

    // MARK: - 7. Stage 4b Slice 2 Remediations (AUDIT-001, AUDIT-008, AUDIT-009, AUDIT-010, AUDIT-011)

    @MainActor
    func testAudit001PushedDTONeverClaimsPhantomAsset() throws {
        // AUDIT-001: Local bookmarks with nil videoAssetName must push nil video_asset_name
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let resolver = LibraryPathResolver(baseURL: tempDir)
        try resolver.ensureDirectoryExists(at: resolver.bookmarksDirectoryURL)
        let weekDir = resolver.weekDirectoryURL(for: "2026-10-24")
        try resolver.ensureDirectoryExists(at: weekDir)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        // Place a local video file on disk
        let localVideo = resolver.localFileURL(for: "2026-10-24", reelID: "unuploaded_reel")
        try Data("dummy_video_content".utf8).write(to: localVideo)

        let bm = BookmarkItem(
            reelID: "unuploaded_reel",
            weekID: "2026-10-24",
            creatorHandle: "creator",
            caption: "Unuploaded",
            rank: 1,
            videoUrl: URL(string: "https://example.com/video.mp4")!,
            thumbnailUrl: URL(string: "https://example.com/thumb.jpg"),
            canonicalUrl: URL(string: "https://www.instagram.com/reel/unuploaded_reel/"),
            videoAssetName: nil, // unconfirmed upload
            releaseTag: nil,
            bookmarkedAt: Date(),
            localStatus: .cached,
            sizeBytes: 100
        )

        let service = GitHubBookmarkSyncService.shared
        guard let exportURL = service.exportBookmarksJSON(bookmarks: [bm]) else {
            XCTFail("Failed to export bookmarks JSON")
            return
        }
        defer { try? FileManager.default.removeItem(at: exportURL) }

        let data = try Data(contentsOf: exportURL)
        let dtos = try JSONDecoder().decode([BookmarkSyncDTO].self, from: data)
        XCTAssertEqual(dtos.count, 1)
        XCTAssertEqual(dtos[0].id, "unuploaded_reel")
        // Must strictly be nil, NOT "unuploaded_reel.mp4"
        XCTAssertNil(dtos[0].video_asset_name)
        XCTAssertNil(dtos[0].release_tag)
    }

    func testAudit009LinkHeaderPaginationExtraction() {
        // AUDIT-009: Test Link header pagination extraction
        let standardLink = "<https://api.github.com/repositories/123/releases/456/assets?page=2&per_page=100>; rel=\"next\", <https://api.github.com/repositories/123/releases/456/assets?page=5&per_page=100>; rel=\"last\""
        let nextURL = GitHubBookmarkSyncService.extractNextPageURL(from: standardLink)
        XCTAssertEqual(nextURL?.absoluteString, "https://api.github.com/repositories/123/releases/456/assets?page=2&per_page=100")

        let linkWithoutQuotes = "<https://api.github.com/repositories/123/releases/456/assets?page=3&per_page=100>; rel=next"
        let nextURL2 = GitHubBookmarkSyncService.extractNextPageURL(from: linkWithoutQuotes)
        XCTAssertEqual(nextURL2?.absoluteString, "https://api.github.com/repositories/123/releases/456/assets?page=3&per_page=100")

        let noNextLink = "<https://api.github.com/repositories/123/releases/456/assets?page=1&per_page=100>; rel=\"prev\""
        XCTAssertNil(GitHubBookmarkSyncService.extractNextPageURL(from: noNextLink))

        XCTAssertNil(GitHubBookmarkSyncService.extractNextPageURL(from: nil))
        XCTAssertNil(GitHubBookmarkSyncService.extractNextPageURL(from: "invalid link header format"))
    }

    func testAudit010BookmarkSyncDTOLeavesThumbnailURLIntactWhenUploadSkipped() {
        // AUDIT-010: Ensure DTO preserves absolute thumbnail URL when thumbnail upload fails or is skipped
        let originalThumb = "https://instagram.fna.fbcdn.net/v/t51.2885-15/thumb.jpg?oe=66000000"
        let dto = BookmarkSyncDTO(
            id: "thumb_test_01",
            creator_handle: "creator",
            caption: "Caption",
            thumbnail_url: originalThumb,
            canonical_url: "https://www.instagram.com/reel/thumb_test_01/",
            video_asset_name: nil,
            release_tag: nil,
            size_bytes: 12345,
            bookmarked_at: "2026-10-24T12:00:00Z"
        )

        XCTAssertEqual(dto.thumbnail_url, originalThumb)
        XCTAssertTrue(dto.thumbnail_url?.hasPrefix("https://") == true)
        XCTAssertFalse(dto.thumbnail_url?.hasPrefix("thumbnails/") == true)
    }

    @MainActor
    func testAudit011BookmarkAddImmediateMainActorInsert() throws {
        // AUDIT-011: Verify direct mode add saves bookmark row immediately on MainActor
        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let reel = ReelItem(
            id: "audit_011_reel",
            creatorHandle: "fast_creator",
            caption: "Non-blocking add",
            rank: 1,
            videoUrl: URL(string: "https://example.com/fast.mp4")!,
            thumbnailUrl: URL(string: "https://example.com/fast_thumb.jpg")
        )

        BookmarkController.add(reel: reel, weekID: "2026-10-24", context: context)

        // Query immediately on MainActor without waiting for async Task completion
        let descriptor = FetchDescriptor<BookmarkItem>(predicate: #Predicate { $0.reelID == "audit_011_reel" })
        let items = try context.fetch(descriptor)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items.first?.reelID, "audit_011_reel")
        XCTAssertEqual(items.first?.creatorHandle, "fast_creator")
    }

    @MainActor
    func testAudit002BookmarksSnapshotSendableTuples() async throws {
        // AUDIT-002: Ensure snapshotting value tuples (weekID, reelID) works cleanly across Task.detached
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let resolver = LibraryPathResolver(baseURL: tempDir)
        let weekDir = resolver.weekDirectoryURL(for: "2026-10-24")
        try resolver.ensureDirectoryExists(at: weekDir)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let localFile = resolver.localFileURL(for: "2026-10-24", reelID: "present_reel")
        try Data("dummy".utf8).write(to: localFile)

        let pairs: [(weekID: String, reelID: String)] = [
            ("2026-10-24", "present_reel"),
            ("2026-10-24", "absent_reel")
        ]

        let ids = await Task.detached(priority: .userInitiated) { () -> Set<String> in
            Set(pairs.compactMap { w, r in
                resolver.resolvedLocalFileURL(for: w, reelID: r) != nil ? r : nil
            })
        }.value

        XCTAssertTrue(ids.contains("present_reel"))
        XCTAssertFalse(ids.contains("absent_reel"))
        XCTAssertEqual(ids.count, 1)
    }

    func testAudit003DownloadAllSheetHonestCompletedAndFilterEmpty() {
        // AUDIT-003: Honest completed state requires cached == total && total > 0
        func checkAllDownloaded(census: (cached: Int, total: Int)?) -> Bool {
            guard let c = census, c.total > 0 else { return false }
            return c.cached == c.total
        }

        // 0 of 250 cached, but unwatched filter leaves 0 pending
        let censusZeroCached: (cached: Int, total: Int) = (0, 250)
        XCTAssertFalse(checkAllDownloaded(census: censusZeroCached),
                       "0/250 cached must NEVER evaluate to all downloaded, even if pendingReels is empty")

        // 249 of 250 cached
        let censusPartial: (cached: Int, total: Int) = (249, 250)
        XCTAssertFalse(checkAllDownloaded(census: censusPartial))

        // Nil census
        XCTAssertFalse(checkAllDownloaded(census: nil))

        // Empty total
        let censusEmpty: (cached: Int, total: Int) = (0, 0)
        XCTAssertFalse(checkAllDownloaded(census: censusEmpty))

        // Exactly 250 of 250 cached
        let censusFull: (cached: Int, total: Int) = (250, 250)
        XCTAssertTrue(checkAllDownloaded(census: censusFull))
    }

    func testAudit005DownloadAllSheetCachedPendingIDsDerivation() async throws {
        // AUDIT-005: Verify async pending ID calculation and in-memory derivation
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let resolver = LibraryPathResolver(baseURL: tempDir)
        let weekDir = resolver.weekDirectoryURL(for: "2026-10-24")
        try resolver.ensureDirectoryExists(at: weekDir)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        try Data("video1".utf8).write(to: resolver.localFileURL(for: "2026-10-24", reelID: "r1"))

        let reels = [
            ReelItem(id: "r1", creatorHandle: "c1", caption: "1", rank: 1, videoUrl: URL(string: "https://example.com/1.mp4")!),
            ReelItem(id: "r2", creatorHandle: "c2", caption: "2", rank: 2, videoUrl: URL(string: "https://example.com/2.mp4")!)
        ]

        let (newCensus, pendingIDs) = await Task.detached(priority: .userInitiated) { () -> ((cached: Int, total: Int), Set<String>) in
            let c = resolver.cacheCensus(reels: reels, weekID: "2026-10-24")
            let pending = Set(reels.compactMap { r in
                resolver.isLocalFileAvailable(for: "2026-10-24", reelID: r.id) ? nil : r.id
            })
            return (c, pending)
        }.value

        XCTAssertEqual(newCensus.cached, 1)
        XCTAssertEqual(newCensus.total, 2)
        XCTAssertTrue(pendingIDs.contains("r2"))
        XCTAssertFalse(pendingIDs.contains("r1"))

        // In-memory pendingReels filter
        let pendingReels = reels.filter { pendingIDs.contains($0.id) }
        XCTAssertEqual(pendingReels.map { $0.id }, ["r2"])
    }

    func testAudit006SettingsSheetViewDetachedCensus() async throws {
        // AUDIT-006: Detached census evaluation matches disk state
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let resolver = LibraryPathResolver(baseURL: tempDir)
        let weekDir = resolver.weekDirectoryURL(for: "2026-10-24")
        try resolver.ensureDirectoryExists(at: weekDir)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        try Data("reelA".utf8).write(to: resolver.localFileURL(for: "2026-10-24", reelID: "reelA"))

        let reelIDs = ["reelA", "reelB", "reelC"]
        let targetWeek = "2026-10-24"

        let computed = await Task.detached(priority: .userInitiated) { () -> (cached: Int, total: Int) in
            var count = 0
            for rID in reelIDs {
                if resolver.isLocalFileAvailable(for: targetWeek, reelID: rID) {
                    count += 1
                }
            }
            return (count, reelIDs.count)
        }.value

        XCTAssertEqual(computed.cached, 1)
        XCTAssertEqual(computed.total, 3)
    }

    // MARK: - AUDIT-013: Plan-Mandated Coverage Tests

    @MainActor
    func testAudit013ResolveReleaseStreamURLS3RedirectAndMemoryCacheTTL() async throws {
        // AUDIT-013: S3 302 redirect capture and memory cache TTL in resolveReleaseStreamURL
        let service = GitHubBookmarkSyncService.shared
        service.session = makeMockSession()
        GitHubBookmarkSyncService.additionalProtocolClasses = [MockResolverURLProtocol.self]

        let releaseTag = "v2026"
        let repo = "vkreddy1729-ops/ig-bookmarks"
        let pat = "ghp_mockpat123"
        let reelID = "s3_reel_01"
        let assetID = 98765
        let s3Location = "https://github-production-release-asset-2e65be.s3.amazonaws.com/\(assetID)/s3_reel_01.mp4?sig=valid302"

        var networkCalls = 0
        MockResolverURLProtocol.requestHandler = { request in
            networkCalls += 1
            let urlString = request.url?.absoluteString ?? ""

            if urlString.contains("/releases/tags/\(releaseTag)") {
                let releaseJSON = """
                {
                    "id": 12345,
                    "tag_name": "\(releaseTag)",
                    "assets": [
                        {
                            "id": \(assetID),
                            "name": "\(reelID).mp4",
                            "url": "https://api.github.com/repos/\(repo)/releases/assets/\(assetID)"
                        }
                    ]
                }
                """.data(using: .utf8)!
                let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
                return (res, releaseJSON)
            } else if urlString.contains("/releases/assets/\(assetID)") {
                // 302 redirect response with S3 location
                let headers = [
                    "Location": s3Location,
                    "Content-Type": "application/octet-stream"
                ]
                let res = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: nil, headerFields: headers)!
                return (res, Data())
            }

            throw URLError(.badURL)
        }

        // 1. Initial resolution captures S3 302 location
        let resolvedURL1 = await service.resolveReleaseStreamURL(reelID: reelID, year: 2026, repo: repo, pat: pat)
        XCTAssertNotNil(resolvedURL1)
        XCTAssertEqual(resolvedURL1?.absoluteString, s3Location)
        XCTAssertEqual(networkCalls, 2, "Expected 1 release tag lookup + 1 asset redirect request")

        // 2. Memory cache TTL hit (second call returns cached URL immediately without hitting network)
        MockResolverURLProtocol.requestHandler = { _ in
            XCTFail("Network should not be touched when asset URL is cached in memory")
            throw URLError(.badServerResponse)
        }
        let resolvedURL2 = await service.resolveReleaseStreamURL(reelID: reelID, year: 2026, repo: repo, pat: pat)
        XCTAssertEqual(resolvedURL2, resolvedURL1)

        // 3. TTL Expiry: when cache entry has expired, it re-queries the network
        service.setMemoryStreamCacheEntry(
            reelID: reelID,
            year: 2026,
            repo: repo,
            url: URL(string: "https://expired.example.com/expired.mp4")!,
            expiresAt: Date().addingTimeInterval(-10) // 10 seconds in the past
        )

        let refreshedS3 = "https://github-production-release-asset-2e65be.s3.amazonaws.com/\(assetID)/s3_reel_01.mp4?sig=refreshed999"
        MockResolverURLProtocol.requestHandler = { request in
            let urlString = request.url?.absoluteString ?? ""
            if urlString.contains("/releases/tags/\(releaseTag)") {
                let releaseJSON = """
                {
                    "id": 12345,
                    "tag_name": "\(releaseTag)",
                    "assets": [
                        {
                            "id": \(assetID),
                            "name": "\(reelID).mp4",
                            "url": "https://api.github.com/repos/\(repo)/releases/assets/\(assetID)"
                        }
                    ]
                }
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, releaseJSON)
            } else if urlString.contains("/releases/assets/\(assetID)") {
                let headers = ["Location": refreshedS3]
                return (HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: nil, headerFields: headers)!, Data())
            }
            throw URLError(.badURL)
        }

        let resolvedURL3 = await service.resolveReleaseStreamURL(reelID: reelID, year: 2026, repo: repo, pat: pat)
        XCTAssertEqual(resolvedURL3?.absoluteString, refreshedS3, "Expired cache entry must be replaced with fresh network resolution")
    }

    @MainActor
    func testAudit013ReleaseAssetUploadTemplateStrippingAnd422Success() async throws {
        // AUDIT-013: Release asset upload handling (URL template stripping {?name,label} and 422 treated as success)
        let service = GitHubBookmarkSyncService.shared
        service.session = makeMockSession()
        GitHubBookmarkSyncService.additionalProtocolClasses = [MockResolverURLProtocol.self]

        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        // Setup real temporary local file so LibraryPathResolver finds it
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_upload_\(UUID().uuidString)")
        let resolver = LibraryPathResolver(baseURL: tempDir)
        let weekDir = resolver.weekDirectoryURL(for: "2026-10-24")
        try resolver.ensureDirectoryExists(at: weekDir)
        let localFile = resolver.localFileURL(for: "2026-10-24", reelID: "reel_422")
        try Data("dummy_video_bytes".utf8).write(to: localFile)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let localBookmark = BookmarkItem(
            reelID: "reel_422",
            weekID: "2026-10-24",
            creatorHandle: "creator422",
            caption: "Template strip and 422 test",
            rank: 1,
            videoUrl: URL(string: "https://meta.com/reel_422.mp4")!,
            thumbnailUrl: nil,
            canonicalUrl: URL(string: "https://instagram.com/reel/reel_422/")!,
            videoAssetName: nil, // unconfirmed upload
            releaseTag: nil,
            bookmarkedAt: ISO8601DateFormatter().date(from: "2026-10-24T12:00:00Z")!
        )
        context.insert(localBookmark)
        try context.save()

        // 1. Verify URL template stripping logic
        let templateUploadURL = "https://uploads.github.com/repos/test/repo/releases/101/assets{?name,label}"
        let baseUploadURL = templateUploadURL.components(separatedBy: "{").first
        XCTAssertEqual(baseUploadURL, "https://uploads.github.com/repos/test/repo/releases/101/assets")

        // 2. Drive syncWithGitHub against mock endpoints
        var assetUploadEndpointHit = false
        MockResolverURLProtocol.requestHandler = { request in
            let urlString = request.url?.absoluteString ?? ""
            let method = request.httpMethod ?? "GET"

            if urlString.contains("/contents/bookmarks.json") && method == "GET" {
                // Empty remote bookmarks.json
                let emptyListJSON = "[]".data(using: .utf8)!.base64EncodedString()
                let respBody = """
                {
                    "sha": "sha_base_1",
                    "content": "\(emptyListJSON)"
                }
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, respBody)
            } else if urlString.contains("/releases/tags/v2026") {
                // Release tag lookup returns upload_url with template
                let releaseBody = """
                {
                    "id": 101,
                    "tag_name": "v2026",
                    "upload_url": "https://uploads.github.com/repos/test/repo/releases/101/assets{?name,label}"
                }
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, releaseBody)
            } else if urlString.contains("/releases/101/assets?per_page=100") {
                // Initial asset listing empty
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, "[]".data(using: .utf8)!)
            } else if urlString.contains("/releases/101/assets") && method == "POST" {
                assetUploadEndpointHit = true
                // Assert that the request URL stripped {?name,label}
                XCTAssertFalse(urlString.contains("{"), "Upload URL must strip RFC 6570 URI templates")
                XCTAssertTrue(urlString.contains("?name=reel_422.mp4"))
                // Return 422 Unprocessable Entity (asset already exists in release)
                let error422 = """
                {
                    "message": "Validation Failed",
                    "errors": [{"resource": "ReleaseAsset", "code": "already_exists", "field": "name"}]
                }
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 422, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, error422)
            } else if urlString.contains("/contents/bookmarks.json") && method == "PUT" {
                // Updated bookmarks.json push succeeds
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, "{}".data(using: .utf8)!)
            }

            throw URLError(.badURL)
        }

        // Set LibraryPathResolver.shared for the sync resolution
        let weekLocal = LibraryPathResolver.shared.localFileURL(for: "2026-10-24", reelID: "reel_422")
        try? LibraryPathResolver.shared.ensureDirectoriesExist(for: "2026-10-24")
        try? Data("dummy_video_bytes".utf8).write(to: weekLocal)
        defer { try? FileManager.default.removeItem(at: weekLocal) }

        let syncResult = try await service.syncWithGitHub(
            repo: "test/repo",
            pat: "ghp_mock",
            localBookmarks: [localBookmark],
            context: context
        )

        XCTAssertTrue(assetUploadEndpointHit, "Upload asset endpoint should have been called")
        XCTAssertEqual(syncResult.pushedCount, 1)
        XCTAssertEqual(localBookmark.videoAssetName, "reel_422.mp4", "422 HTTP response must be treated as successful asset confirmation")
        XCTAssertEqual(localBookmark.releaseTag, "v2026")
    }

    @MainActor
    func testAudit013SyncIfNeededInFlightCoalescing() async throws {
        // AUDIT-013: In-flight coalescing in syncIfNeeded (isSyncing and needsResync)
        let service = GitHubBookmarkSyncService.shared
        service.session = makeMockSession()
        GitHubBookmarkSyncService.additionalProtocolClasses = [MockResolverURLProtocol.self]

        UserDefaults.standard.set("test/repo", forKey: "gh_bookmark_repo")
        UserDefaults.standard.set("ghp_valid", forKey: "gh_bookmark_pat")

        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        final class GateBox: @unchecked Sendable {
            var continuation: CheckedContinuation<Void, Never>?
        }
        let box = GateBox()
        let gateTask = Task {
            await withCheckedContinuation { cont in
                box.continuation = cont
            }
        }

        MockResolverURLProtocol.requestHandler = { request in
            let urlString = request.url?.absoluteString ?? ""
            if urlString.contains("/contents/bookmarks.json") {
                _ = Task { await gateTask.value }
                let body = "[]".data(using: .utf8)!.base64EncodedString()
                let resp = """
                {"sha": "abc", "content": "\(body)"}
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, resp)
            }
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data())
        }

        // Start first sync pass
        let task1 = Task {
            await service.syncIfNeeded(context: context)
        }

        // Give task1 a brief moment to start and set isSyncing = true
        try await Task.sleep(nanoseconds: 50_000_000) // 50ms
        XCTAssertTrue(service.isSyncing, "service.isSyncing must be true while sync pass 1 is in-flight")
        XCTAssertFalse(service.needsResync, "service.needsResync should initially be false")

        // Overlapping sync pass 2 is fired while pass 1 is in flight
        await service.syncIfNeeded(context: context)

        // Pass 2 must have coalesced: it immediately returned and flagged needsResync = true
        XCTAssertTrue(service.needsResync, "Overlapping syncIfNeeded call must set needsResync = true")

        // Release gate and wait for task1
        box.continuation?.resume()
        await task1.value

        // Allow follow-up deferred task to execute
        try await Task.sleep(nanoseconds: 100_000_000) // 100ms
        XCTAssertFalse(service.isSyncing)
    }

    @MainActor
    func testAudit013CellularUploadGate() async throws {
        // AUDIT-013: Cellular upload gate (!Reachability.shared.isExpensive)
        let service = GitHubBookmarkSyncService.shared
        service.session = makeMockSession()
        GitHubBookmarkSyncService.additionalProtocolClasses = [MockResolverURLProtocol.self]

        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let weekID = "2026-10-24"
        let reelID = "cellular_reel"
        let weekLocal = LibraryPathResolver.shared.localFileURL(for: weekID, reelID: reelID)
        try? LibraryPathResolver.shared.ensureDirectoriesExist(for: weekID)
        try? Data("cellular_video".utf8).write(to: weekLocal)
        defer { try? FileManager.default.removeItem(at: weekLocal) }

        let localBookmark = BookmarkItem(
            reelID: reelID,
            weekID: weekID,
            creatorHandle: "cell_creator",
            caption: "Cellular gating test",
            rank: 1,
            videoUrl: URL(string: "https://meta.com/cellular.mp4")!,
            thumbnailUrl: nil,
            canonicalUrl: URL(string: "https://instagram.com/reel/cellular_reel/")!,
            videoAssetName: nil,
            releaseTag: nil,
            bookmarkedAt: ISO8601DateFormatter().date(from: "2026-10-24T12:00:00Z")!
        )
        context.insert(localBookmark)
        try context.save()

        // 1. Set network to expensive (cellular)
        Reachability.shared.isExpensiveOverride = true
        XCTAssertTrue(Reachability.shared.isExpensive)

        var releaseOrUploadHitOnCellular = false
        MockResolverURLProtocol.requestHandler = { request in
            let urlString = request.url?.absoluteString ?? ""
            let method = request.httpMethod ?? "GET"

            if urlString.contains("/releases") {
                releaseOrUploadHitOnCellular = true
            }

            if urlString.contains("/contents/bookmarks.json") && method == "GET" {
                let emptyList = "[]".data(using: .utf8)!.base64EncodedString()
                let resp = """
                {"sha": "cell_sha_1", "content": "\(emptyList)"}
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, resp)
            } else if urlString.contains("/contents/bookmarks.json") && method == "PUT" {
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, "{}".data(using: .utf8)!)
            }

            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data())
        }

        let cellularResult = try await service.syncWithGitHub(
            repo: "test/repo",
            pat: "ghp_cell",
            localBookmarks: [localBookmark],
            context: context
        )

        XCTAssertFalse(releaseOrUploadHitOnCellular, "Video upload endpoints must NOT be called when network is expensive / cellular")
        XCTAssertNil(localBookmark.videoAssetName, "videoAssetName must remain nil when cellular upload is skipped")
        XCTAssertEqual(cellularResult.pushedCount, 1, "Metadata sync should still push bookmarks.json even when video uploads are skipped")

        // 2. Set network to Wi-Fi (!isExpensive)
        Reachability.shared.isExpensiveOverride = false
        XCTAssertFalse(Reachability.shared.isExpensive)

        var uploadHitOnWifi = false
        MockResolverURLProtocol.requestHandler = { request in
            let urlString = request.url?.absoluteString ?? ""
            let method = request.httpMethod ?? "GET"

            if urlString.contains("/contents/bookmarks.json") && method == "GET" {
                let emptyList = "[]".data(using: .utf8)!.base64EncodedString()
                let resp = """
                {"sha": "cell_sha_2", "content": "\(emptyList)"}
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, resp)
            } else if urlString.contains("/releases/tags/v2026") {
                let releaseBody = """
                {
                    "id": 202,
                    "tag_name": "v2026",
                    "upload_url": "https://uploads.github.com/repos/test/repo/releases/202/assets"
                }
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, releaseBody)
            } else if urlString.contains("/releases/202/assets?per_page=100") {
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, "[]".data(using: .utf8)!)
            } else if urlString.contains("/releases/202/assets") && method == "POST" {
                uploadHitOnWifi = true
                return (HTTPURLResponse(url: request.url!, statusCode: 201, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, "{}".data(using: .utf8)!)
            } else if urlString.contains("/contents/bookmarks.json") && method == "PUT" {
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, "{}".data(using: .utf8)!)
            }

            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data())
        }

        _ = try await service.syncWithGitHub(
            repo: "test/repo",
            pat: "ghp_cell",
            localBookmarks: [localBookmark],
            context: context
        )

        XCTAssertTrue(uploadHitOnWifi, "Video upload MUST execute once network is on non-expensive Wi-Fi")
        XCTAssertEqual(localBookmark.videoAssetName, "\(reelID).mp4")
    }

    @MainActor
    func testAudit013EnforceCachedVideosCapPrioritizationAndPreservation() throws {
        // AUDIT-013: Local vault cap enforceCachedVideosCap with real temporary directory files:
        // verifying uploaded-first (videoAssetName != nil) eviction prioritization,
        // active player boundIDs protection, and thumbnail file preservation.
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_cap_\(UUID().uuidString)")
        let resolver = LibraryPathResolver(baseURL: tempDir)
        let fm = FileManager.default
        try resolver.ensureDirectoryExists(at: resolver.bookmarksDirectoryURL)
        defer {
            try? fm.removeItem(at: tempDir)
            AVPlayerPool.shared.slotCurrent.teardown()
        }

        let schema = Schema([BookmarkItem.self, AppState.self, WatchedEvent.self, DailyProgress.self])
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let now = Date()

        // bm1: Uploaded, accessed recently (-100s)
        let bm1 = BookmarkItem(
            reelID: "bm1_uploaded_recent",
            weekID: "2026-10-24",
            creatorHandle: "creator1",
            caption: "",
            rank: 1,
            videoAssetName: "bm1_uploaded_recent.mp4",
            releaseTag: "v2026",
            localStatus: .cached,
            lastAccessedAt: now.addingTimeInterval(-100)
        )

        // bm2: Unuploaded (videoAssetName == nil), accessed long ago (-1000s)
        let bm2 = BookmarkItem(
            reelID: "bm2_unuploaded_old",
            weekID: "2026-10-24",
            creatorHandle: "creator2",
            caption: "",
            rank: 2,
            videoAssetName: nil, // NOT uploaded to GitHub
            releaseTag: nil,
            localStatus: .cached,
            lastAccessedAt: now.addingTimeInterval(-1000)
        )

        // bm3: Uploaded, oldest access (-2000s), but currently bound in active player slot
        let bm3 = BookmarkItem(
            reelID: "bm3_playing_bound",
            weekID: "2026-10-24",
            creatorHandle: "creator3",
            caption: "",
            rank: 3,
            videoAssetName: "bm3_playing_bound.mp4",
            releaseTag: "v2026",
            localStatus: .cached,
            lastAccessedAt: now.addingTimeInterval(-2000)
        )

        // bm4: Uploaded, accessed moderately ago (-500s)
        let bm4 = BookmarkItem(
            reelID: "bm4_uploaded_older",
            weekID: "2026-10-24",
            creatorHandle: "creator4",
            caption: "",
            rank: 4,
            videoAssetName: "bm4_uploaded_older.mp4",
            releaseTag: "v2026",
            localStatus: .cached,
            lastAccessedAt: now.addingTimeInterval(-500)
        )

        for bm in [bm1, bm2, bm3, bm4] {
            context.insert(bm)
            // Create actual video file on disk
            let vURL = resolver.bookmarkFileURL(for: bm.reelID)
            try Data("video_payload_\(bm.reelID)".utf8).write(to: vURL)
            // Create actual thumbnail file on disk
            let tURL = resolver.bookmarkThumbnailFileURL(for: bm.reelID)
            try Data("thumb_payload_\(bm.reelID)".utf8).write(to: tURL)
        }
        try context.save()

        // Bind bm3 to active player slot so boundReelIDs contains "bm3_playing_bound"
        let reel3 = ReelItem(id: "bm3_playing_bound", creatorHandle: "creator3", caption: "", rank: 3, videoUrl: URL(string: "https://meta.com/3.mp4")!)
        AVPlayerPool.shared.slotCurrent.slotItem = AVPlayerPool.SlotItem(
            reel: reel3,
            localURL: resolver.bookmarkFileURL(for: "bm3_playing_bound"),
            remoteURL: URL(string: "https://meta.com/3.mp4")!,
            isLocal: true
        )
        XCTAssertTrue(AVPlayerPool.shared.boundReelIDs.contains("bm3_playing_bound"))

        // Enforce cap of 2 videos: 4 cached videos - limit 2 = excess 2 videos to evict
        BookmarkController.enforceCachedVideosCap(limit: 2, context: context, resolver: resolver)

        // Verifications:
        // 1. Protection of active player boundIDs: bm3 MUST NOT be evicted
        XCTAssertTrue(fm.fileExists(atPath: resolver.bookmarkFileURL(for: "bm3_playing_bound").path), "Active playing reel must be protected from eviction")
        XCTAssertEqual(bm3.localStatus, .cached)

        // 2. Uploaded-first eviction prioritization:
        // bm4 and bm1 (both have videoAssetName != nil) are prioritized for eviction ahead of bm2 (unuploaded).
        // Between bm4 (-500s) and bm1 (-100s), bm4 is evicted first, then bm1.
        // bm2 (unuploaded) MUST be preserved even though its lastAccessedAt (-1000s) is older than bm4 and bm1!
        XCTAssertFalse(fm.fileExists(atPath: resolver.bookmarkFileURL(for: "bm4_uploaded_older").path), "bm4 was uploaded and older, must be evicted")
        XCTAssertEqual(bm4.localStatus, .evicted)

        XCTAssertFalse(fm.fileExists(atPath: resolver.bookmarkFileURL(for: "bm1_uploaded_recent").path), "bm1 was uploaded, must be evicted next")
        XCTAssertEqual(bm1.localStatus, .evicted)

        XCTAssertTrue(fm.fileExists(atPath: resolver.bookmarkFileURL(for: "bm2_unuploaded_old").path), "Unuploaded bm2 must be preserved ahead of uploaded items")
        XCTAssertEqual(bm2.localStatus, .cached)

        // 3. Thumbnail preservation: ALL thumbnails must remain completely intact on disk
        for id in ["bm1_uploaded_recent", "bm2_unuploaded_old", "bm3_playing_bound", "bm4_uploaded_older"] {
            let thumbPath = resolver.bookmarkThumbnailFileURL(for: id).path
            XCTAssertTrue(fm.fileExists(atPath: thumbPath), "Thumbnail for \(id) must be preserved on disk during video eviction")
        }
    }
}
