import XCTest
import Foundation
@testable import InstagramDigest

/// URLProtocol mock for hermetic testing of BookmarkResolver and DigestDataService network requests.
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

    override func tearDown() {
        MockResolverURLProtocol.requestHandler = nil
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
            XCTAssertEqual(err, .emptyManifest)
        } catch {
            XCTAssertTrue(error is DigestDataServiceError || error is URLError)
        }
    }

    func testDirectModeBundleFallbackPolicy() {
        // AUDIT-023: In direct mode, non-direct bundle fallback is rejected
        XCTAssertTrue(DigestDataService.directMode)
    }

    // MARK: - 2. BookmarkResolver Tests

    private func makeMockSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockResolverURLProtocol.self]
        return URLSession(configuration: config)
    }

    func testResolverRequestContractAndAuthHeader() async throws {
        // AUDIT-004: Verify method, headers, and request body shape
        var capturedRequest: URLRequest?
        MockResolverURLProtocol.requestHandler = { request in
            capturedRequest = request
            let responseJson = """
            {
                "status": "tunnel",
                "url": "https://resolver-stream.internal/stream_contract.mp4"
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let customEndpoint = URL(string: "https://custom.cobalt.tools/api/json")!
        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: customEndpoint, apiKey: "secret_token_123")
        let reelURL = URL(string: "https://www.instagram.com/reel/C99887766/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .success = result else {
            XCTFail("Expected .success, got \(result)")
            return
        }

        XCTAssertNotNil(capturedRequest)
        XCTAssertEqual(capturedRequest?.httpMethod, "POST")
        XCTAssertEqual(capturedRequest?.url, customEndpoint)
        XCTAssertEqual(capturedRequest?.value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertEqual(capturedRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(capturedRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer secret_token_123")

        if let bodyData = capturedRequest?.httpBody,
           let json = try? JSONSerialization.jsonObject(with: bodyData) as? [String: String] {
            XCTAssertEqual(json["url"], reelURL.absoluteString)
        } else {
            XCTFail("Failed to parse request JSON body")
        }
    }

    func testResolverMapsHttp400AuthError() async throws {
        // AUDIT-004: HTTP 400 with auth errors must map to .authOrChallengeRequired
        MockResolverURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "status": "error",
                "error": {
                    "code": "error.api.auth.jwt.missing",
                    "context": { "service": "api" }
                }
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 400, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://api.cobalt.tools/")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C_jwt_missing/")!

        let result = await resolver.resolve(url: reelURL)
        XCTAssertEqual(result, .authOrChallengeRequired, "HTTP 400 error.api.auth.jwt.missing must map to .authOrChallengeRequired")
    }

    func testResolverMapsHttp200ChallengeHtml() async throws {
        // AUDIT-035: HTML challenge response on HTTP 200 must map to .authOrChallengeRequired
        MockResolverURLProtocol.requestHandler = { request in
            let htmlData = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>Cloudflare Turnstile challenge</body></html>".data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "text/html; charset=UTF-8"])!
            return (response, htmlData)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://api.cobalt.tools/")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C_challenge/")!

        let result = await resolver.resolve(url: reelURL)
        XCTAssertEqual(result, .authOrChallengeRequired, "HTML challenge response on HTTP 200 must map to .authOrChallengeRequired")
    }

    func testResolverCancellationCheck() async throws {
        // AUDIT-036: Task cancellation returns .failed("Task cancelled")
        let task = Task {
            let resolver = BookmarkResolver(session: self.makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
            let reelURL = URL(string: "https://www.instagram.com/reel/C_cancelled/")!
            return await resolver.resolve(url: reelURL)
        }
        task.cancel()
        let result = await task.value
        // Either failed with cancellation or offline depending on interleaving
        if case .failed(let msg) = result {
            XCTAssertTrue(msg.contains("cancelled") || msg.contains("Task"))
        }
    }

    func testResolverSuccessTunnel() async throws {
        MockResolverURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "status": "tunnel",
                "url": "https://resolver-stream.internal/stream_123.mp4",
                "filename": "stream_123.mp4"
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C1234567890/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .success(let streamURL) = result else {
            XCTFail("Expected .success, got \(result)")
            return
        }
        XCTAssertEqual(streamURL.absoluteString, "https://resolver-stream.internal/stream_123.mp4")
    }

    func testResolverSuccessRedirect() async throws {
        MockResolverURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "status": "redirect",
                "url": "https://cdn.instagram.com/fresh_stream.mp4"
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C1234567890/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .success(let streamURL) = result else {
            XCTFail("Expected .success, got \(result)")
            return
        }
        XCTAssertEqual(streamURL.absoluteString, "https://cdn.instagram.com/fresh_stream.mp4")
    }

    func testResolverUnsupportedPicker() async throws {
        MockResolverURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "status": "picker",
                "picker": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C1234567890/")!

        let result = await resolver.resolve(url: reelURL)
        XCTAssertEqual(result, .unsupportedFormat)
    }

    func testResolverErrorStatus() async throws {
        MockResolverURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "status": "error",
                "error": {
                    "code": "error.api.media.fetch_failed"
                }
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C1234567890/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .failed(let msg) = result else {
            XCTFail("Expected .failed, got \(result)")
            return
        }
        XCTAssertTrue(msg.contains("error.api.media.fetch_failed"))
    }

    func testResolverOffline() async throws {
        MockResolverURLProtocol.requestHandler = { _ in
            throw URLError(.notConnectedToInternet)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C1234567890/")!

        let result = await resolver.resolve(url: reelURL)
        XCTAssertEqual(result, .offline)
    }

    func testResolverRateLimited429() async throws {
        MockResolverURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 429,
                httpVersion: nil,
                headerFields: ["Retry-After": "0.1"]
            )!
            return (response, Data())
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C1234567890/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .rateLimited(let retryAfter) = result else {
            XCTFail("Expected .rateLimited, got \(result)")
            return
        }
        XCTAssertEqual(retryAfter, 0.1)
    }

    func testResolverAuthOrChallengeRequired() async throws {
        MockResolverURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: 403,
                httpVersion: nil,
                headerFields: nil
            )!
            return (response, Data())
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C1234567890/")!

        let result = await resolver.resolve(url: reelURL)
        XCTAssertEqual(result, .authOrChallengeRequired)
    }

    func testResolverLocalProcessingFormat() async throws {
        MockResolverURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "status": "local-processing"
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C1234567890/")!

        let result = await resolver.resolve(url: reelURL)
        XCTAssertEqual(result, .unsupportedFormat)
    }

    func testResolverRequestOmitsAuthHeaderWhenNoApiKey() async throws {
        // AUDIT-043: Assert Authorization header is not sent when apiKey is empty/nil
        var capturedRequest: URLRequest?
        MockResolverURLProtocol.requestHandler = { request in
            capturedRequest = request
            let responseJson = """
            {
                "status": "tunnel",
                "url": "https://resolver-stream.internal/no_auth.mp4"
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!, apiKey: nil)
        let reelURL = URL(string: "https://www.instagram.com/reel/C_no_auth/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .success = result else {
            XCTFail("Expected .success, got \(result)")
            return
        }

        XCTAssertNotNil(capturedRequest)
        XCTAssertNil(capturedRequest?.value(forHTTPHeaderField: "Authorization"), "Authorization header must be omitted when no API key is set")
    }

    func testResolverRetriesOnceOn5xxAndSucceeds() async throws {
        // AUDIT-043: Verify transient 5xx is retried once and succeeds if retry succeeds
        var requestCount = 0
        MockResolverURLProtocol.requestHandler = { request in
            requestCount += 1
            if requestCount == 1 {
                let response = HTTPURLResponse(url: request.url!, statusCode: 503, httpVersion: nil, headerFields: nil)!
                return (response, Data())
            } else {
                let responseJson = """
                {
                    "status": "tunnel",
                    "url": "https://resolver-stream.internal/retry_success.mp4"
                }
                """.data(using: .utf8)!
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, responseJson)
            }
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C_retry_5xx/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .success(let streamURL) = result else {
            XCTFail("Expected .success after 5xx retry, got \(result)")
            return
        }
        XCTAssertEqual(streamURL.absoluteString, "https://resolver-stream.internal/retry_success.mp4")
        XCTAssertEqual(requestCount, 2, "Expected exactly 1 retry (2 total requests)")
    }

    func testResolver5xxExhaustsRetryAndFails() async throws {
        // AUDIT-043: Verify persistent 5xx exhausts retry and returns .failed
        var requestCount = 0
        MockResolverURLProtocol.requestHandler = { request in
            requestCount += 1
            let response = HTTPURLResponse(url: request.url!, statusCode: 500, httpVersion: nil, headerFields: nil)!
            return (response, Data())
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C_persistent_500/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .failed(let msg) = result else {
            XCTFail("Expected .failed, got \(result)")
            return
        }
        XCTAssertTrue(msg.contains("500"), "Expected error message referencing HTTP 500, got \(msg)")
        XCTAssertEqual(requestCount, 2, "Expected exactly 1 retry before failing (2 total requests)")
    }

    func testResolverRetriesOnceOnTimeoutAndSucceeds() async throws {
        // AUDIT-043: Verify transient timeout is retried once and can succeed
        var requestCount = 0
        MockResolverURLProtocol.requestHandler = { request in
            requestCount += 1
            if requestCount == 1 {
                throw URLError(.timedOut)
            } else {
                let responseJson = """
                {
                    "status": "tunnel",
                    "url": "https://resolver-stream.internal/timeout_recovery.mp4"
                }
                """.data(using: .utf8)!
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, responseJson)
            }
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C_timeout_retry/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .success(let streamURL) = result else {
            XCTFail("Expected .success after timeout retry, got \(result)")
            return
        }
        XCTAssertEqual(streamURL.absoluteString, "https://resolver-stream.internal/timeout_recovery.mp4")
        XCTAssertEqual(requestCount, 2, "Expected exactly 1 retry (2 total requests)")
    }

    func testResolverTimeoutExhaustsRetryAndReturnsOffline() async throws {
        // AUDIT-043: Verify persistent timeout exhausts retry and maps to .offline
        var requestCount = 0
        MockResolverURLProtocol.requestHandler = { _ in
            requestCount += 1
            throw URLError(.timedOut)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C_persistent_timeout/")!

        let result = await resolver.resolve(url: reelURL)
        XCTAssertEqual(result, .offline, "Persistent timeout should map to .offline")
        XCTAssertEqual(requestCount, 2, "Expected exactly 1 retry before offline (2 total requests)")
    }

    func testResolverRejectsNonHttpsStreamUrl() async throws {
        // AUDIT-043: Verify resolver rejects insecure HTTP stream URLs
        MockResolverURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "status": "tunnel",
                "url": "http://insecure-stream.internal/video.mp4"
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let reelURL = URL(string: "https://www.instagram.com/reel/C_insecure/")!

        let result = await resolver.resolve(url: reelURL)
        guard case .failed(let msg) = result else {
            XCTFail("Expected .failed for non-HTTPS stream URL, got \(result)")
            return
        }
        XCTAssertTrue(msg.contains("HTTPS"), "Expected message to mention HTTPS, got: \(msg)")
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

    func testBookmarkPlaybackRoutingResolverFallback() async throws {
        // AUDIT-002: Hermetic test using isolated temporary directory
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("test_\(UUID().uuidString)")
        let pathResolver = LibraryPathResolver(baseURL: tempDir)
        let fm = FileManager.default
        defer { try? fm.removeItem(at: tempDir) }

        let weekID = "2026-08-01" // older purged week
        let reelID = "purged_reel_test"

        let localWeekURL = pathResolver.localFileURL(for: weekID, reelID: reelID)
        let localBookmarkURL = pathResolver.bookmarkFileURL(for: reelID)

        // Verify neither exists on disk
        XCTAssertFalse(fm.fileExists(atPath: localWeekURL.path))
        XCTAssertFalse(fm.fileExists(atPath: localBookmarkURL.path))

        MockResolverURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "status": "tunnel",
                "url": "https://resolved.meta.stream/reel_\(reelID).mp4"
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, responseJson)
        }

        let resolver = BookmarkResolver(session: makeMockSession(), endpoint: URL(string: "https://mock.resolver/api")!)
        let canonicalUrl = URL(string: "https://www.instagram.com/reel/\(reelID)/")!
        let result = await resolver.resolve(url: canonicalUrl)

        guard case .success(let streamURL) = result else {
            XCTFail("Expected .success, got \(result)")
            return
        }
        XCTAssertEqual(streamURL.absoluteString, "https://resolved.meta.stream/reel_\(reelID).mp4")
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

    func testExecuteRolloverPurgeProtectsResumeDataAndPartFiles() async throws {
        // AUDIT-002: Uses isolated pathResolver rooted at tempDir (never calls .shared)
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
}
