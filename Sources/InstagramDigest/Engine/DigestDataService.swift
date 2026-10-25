import Foundation

public enum DigestDataServiceError: Error, LocalizedError {
    case emptyManifest

    public var errorDescription: String? {
        switch self {
        case .emptyManifest:
            return "Digest manifest is empty"
        }
    }
}

/// Service responsible for fetching remote or cached data.json manifest.
public actor DigestDataService {
    public static let shared = DigestDataService()

    // CONTRACT-PAIR: Must match config.py:direct_pages_manifest_url() (PAGES_BASE_URL + "/data_direct.json") (AUDIT-071)
    public static let directManifestURL = URL(string: "https://vkr1729.github.io/Instagram_digest/data_direct.json")!
    public static let primaryManifestURL = URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/data.json")!
    public static let fallbackManifestURL = URL(string: "https://vkr1729.github.io/Instagram_digest/data.json")!
    public static let defaultManifestURL = directManifestURL
    public static let directManifestCacheFilename = "manifest_cache_direct.json"
    public static let defaultBookmarksURL = URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/bookmarks/manifest.json")!
    public static let directMode: Bool = true

    private let session: URLSession
    private let pathResolver: LibraryPathResolver

    public init(
        session: URLSession = .shared,
        pathResolver: LibraryPathResolver = .shared
    ) {
        self.session = session
        self.pathResolver = pathResolver
    }

    /// Fetches manifest from network with fallback to cached manifest on disk
    public func fetchManifest(from url: URL = defaultManifestURL, bypassCache: Bool = false) async throws -> DigestManifest {
        // AUDIT-065: Direct mode fail-closed gate: reject explicit R2/worker or non-direct URLs
        if Self.directMode && url != Self.directManifestURL && !url.isFileURL {
            throw URLError(.unsupportedURL)
        }

        let cacheFilename = (url == Self.directManifestURL) ? Self.directManifestCacheFilename : "manifest_cache.json"
        let cacheFileURL = pathResolver.mediaCacheBaseURL.appendingPathComponent(cacheFilename)
        let isUITesting = ProcessInfo.processInfo.arguments.contains("-ui-testing") || CommandLine.arguments.contains("-ui-testing")

        // 0. In UI test environment, prioritize bundled manifest for instant deterministic testing
        if isUITesting,
           let bundleURL = Bundle.main.url(forResource: "data", withExtension: "json"),
           let bundleData = try? Data(contentsOf: bundleURL),
           let manifest = try? JSONDecoder().decode(DigestManifest.self, from: bundleData) {
            return manifest
        }

        // 1. If url is a local file URL, load directly; on corrupt file fall through to fallbacks
        if url.isFileURL {
            do {
                let data = try Data(contentsOf: url)
                let manifest = try JSONDecoder().decode(DigestManifest.self, from: data)
                if manifest.items.isEmpty {
                    throw DigestDataServiceError.emptyManifest
                }
                return manifest
            } catch let err as DigestDataServiceError {
                throw err
            } catch {
                // Corrupt/unreadable file URL — fall through to cache/bundle fallback below
            }
        }

        // 2. Attempt network fetch (try primary, fallback to secondary if applicable)
        let urlsToAttempt: [URL]
        if url == Self.primaryManifestURL {
            urlsToAttempt = [Self.primaryManifestURL, Self.fallbackManifestURL]
        } else {
            urlsToAttempt = [url]
        }

        for attemptURL in urlsToAttempt {
            do {
                var request = URLRequest(url: attemptURL)
                request.cachePolicy = .reloadIgnoringLocalCacheData
                request.timeoutInterval = 15.0

                let (data, response) = try await session.data(for: request)
                if let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) {
                    let manifest = try JSONDecoder().decode(DigestManifest.self, from: data)
                    // AUDIT-022: Reject empty manifests atomically - throw instead of returning
                    guard !manifest.items.isEmpty else {
                        throw DigestDataServiceError.emptyManifest
                    }
                    try? pathResolver.ensureDirectoryExists(at: pathResolver.mediaCacheBaseURL)
                    try? pathResolver.ensureDirectoriesExist(for: manifest.weekId)
                    try? data.write(to: cacheFileURL, options: .atomic)
                    return manifest
                }
            } catch let err as DigestDataServiceError {
                throw err
            } catch {
                // Try next URL or fall through to cache
            }
        }

        if bypassCache {
            throw URLError(.cannotConnectToHost)
        }

        // 3. Fallback to cached manifest
        if FileManager.default.fileExists(atPath: cacheFileURL.path),
           let cachedData = try? Data(contentsOf: cacheFileURL) {
            do {
                let cachedManifest = try JSONDecoder().decode(DigestManifest.self, from: cachedData)
                if !cachedManifest.items.isEmpty {
                    return cachedManifest
                }
            } catch {
                // Corrupted cache file, delete it and fall through to bundle
                try? FileManager.default.removeItem(at: cacheFileURL)
            }
        }

        // 4. Fallback to bundle if present (AUDIT-023: In direct mode, check data_direct then data)
        let bundleResourceNames = Self.directMode ? ["data_direct", "data"] : ["data"]
        for resName in bundleResourceNames {
            if let bundleURL = Bundle.main.url(forResource: resName, withExtension: "json"),
               let bundleData = try? Data(contentsOf: bundleURL),
               let manifest = try? JSONDecoder().decode(DigestManifest.self, from: bundleData) {
                if !Self.directMode || manifest.channel == "direct" || isUITesting {
                    if !manifest.items.isEmpty {
                        return manifest
                    }
                }
            }
        }

        throw URLError(.cannotConnectToHost)
    }

    /// Fetches remote bookmarks manifest from Cloudflare R2 worker.
    /// Falls back to the last good disk cache (mirroring fetchManifest) so a
    /// flaky worker serves stale bookmarks instead of an empty list.
    public func fetchRemoteBookmarks(from url: URL = defaultBookmarksURL) async throws -> [BookmarkRemoteDTO] {
        if Self.directMode {
            return []
        }
        let cacheFileURL = pathResolver.mediaCacheBaseURL.appendingPathComponent("bookmarks_cache.json")

        do {
            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 15.0

            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                throw URLError(.badServerResponse)
            }

            // Lossy decode: one malformed R2 entry must not invalidate the whole list.
            let dtos = try LossyBookmarkList.decode(from: data)
            try? pathResolver.ensureDirectoryExists(at: pathResolver.mediaCacheBaseURL)
            try? data.write(to: cacheFileURL, options: .atomic)
            return dtos
        } catch {
            // Offline fallback: last good cache wins over an empty list.
            if FileManager.default.fileExists(atPath: cacheFileURL.path),
               let cachedData = try? Data(contentsOf: cacheFileURL),
               let cached = try? LossyBookmarkList.decode(from: cachedData) {
                return cached
            }
            throw error
        }
    }

    public static let defaultBookmarkApiBase = URL(string: "https://ig-digest-api.kedarvreddy.workers.dev")!

    public struct BookmarkPayload: Codable, Sendable {
        public let id: String
        public let video_url: String
        public let thumbnail_url: String
        public let creator_handle: String
        public let caption: String
        public let category: String

        public init(reel: ReelItem) {
            self.id = reel.id
            self.video_url = reel.videoUrl.absoluteString
            self.thumbnail_url = reel.thumbnailUrl?.absoluteString ?? ""
            self.creator_handle = reel.creatorHandle
            self.caption = reel.caption
            self.category = reel.category ?? ""
        }
    }

    /// Saves bookmark remotely via Cloudflare Worker, triggering Telegram video forwarding
    @discardableResult
    public func saveRemoteBookmark(
        reel: ReelItem,
        ownerKey: String? = nil,
        apiBase: URL = defaultBookmarkApiBase
    ) async throws -> Bool {
        if Self.directMode {
            return true
        }
        let key = ownerKey ?? UserDefaults.standard.string(forKey: "digest_owner_key") ?? ProcessInfo.processInfo.environment["OWNER_KEY"] ?? ""
        let endpoint = apiBase.appendingPathComponent("api/bookmark")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !key.isEmpty {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
            request.setValue(key, forHTTPHeaderField: "X-Owner-Key")
        }
        let payload = BookmarkPayload(reel: reel)
        request.httpBody = try JSONEncoder().encode(payload)
        request.timeoutInterval = 15.0

        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if http.statusCode == 403 {
            throw URLError(.userAuthenticationRequired)
        }
        return (200...299).contains(http.statusCode)
    }

    /// Deletes bookmark remotely via Cloudflare Worker
    @discardableResult
    public func deleteRemoteBookmark(
        reelID: String,
        ownerKey: String? = nil,
        apiBase: URL = defaultBookmarkApiBase
    ) async throws -> Bool {
        if Self.directMode {
            return true
        }
        let key = ownerKey ?? UserDefaults.standard.string(forKey: "digest_owner_key") ?? ProcessInfo.processInfo.environment["OWNER_KEY"] ?? ""
        let safeID = reelID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? reelID
        let endpoint = apiBase.appendingPathComponent("api/bookmark/\(safeID)")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "DELETE"
        if !key.isEmpty {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
            request.setValue(key, forHTTPHeaderField: "X-Owner-Key")
        }
        request.timeoutInterval = 15.0

        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if http.statusCode == 403 {
            throw URLError(.userAuthenticationRequired)
        }
        return (200...299).contains(http.statusCode)
    }
}
