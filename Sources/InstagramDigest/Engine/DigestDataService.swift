import Foundation

/// Service responsible for fetching remote or cached data.json manifest.
public actor DigestDataService {
    public static let shared = DigestDataService()

    public static let defaultManifestURL = URL(string: "https://vkreddy1729-ops.github.io/Instagram_digest/data.json")!

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
    public func fetchManifest(from url: URL = defaultManifestURL) async throws -> DigestManifest {
        let cacheFileURL = pathResolver.mediaCacheBaseURL.appendingPathComponent("manifest_cache.json")

        // 0. In UI test environment, prioritize bundled manifest for instant deterministic testing
        if ProcessInfo.processInfo.arguments.contains("-ui-testing"),
           let bundleURL = Bundle.main.url(forResource: "data", withExtension: "json"),
           let bundleData = try? Data(contentsOf: bundleURL),
           let manifest = try? JSONDecoder().decode(DigestManifest.self, from: bundleData) {
            return manifest
        }

        // 1. If url is a local file URL, load directly; on corrupt file fall through to fallbacks
        if url.isFileURL {
            do {
                let data = try Data(contentsOf: url)
                return try JSONDecoder().decode(DigestManifest.self, from: data)
            } catch {
                // Corrupt/unreadable file URL — fall through to cache/bundle fallback below
            }
        }

        // 2. Attempt network fetch
        do {
            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 15.0

            let (data, response) = try await session.data(for: request)
            if let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) {
                let manifest = try JSONDecoder().decode(DigestManifest.self, from: data)
                // Cache to disk
                try? pathResolver.ensureDirectoriesExist(for: manifest.weekId)
                try? data.write(to: cacheFileURL, options: .atomic)
                return manifest
            }
        } catch {
            // Network fetch failed, fall through to cache
        }

        // 3. Fallback to cached manifest
        if FileManager.default.fileExists(atPath: cacheFileURL.path),
           let cachedData = try? Data(contentsOf: cacheFileURL) {
            do {
                return try JSONDecoder().decode(DigestManifest.self, from: cachedData)
            } catch {
                // Corrupted cache file, delete it and fall through to bundle
                try? FileManager.default.removeItem(at: cacheFileURL)
            }
        }

        // 4. Fallback to bundle if present
        if let bundleURL = Bundle.main.url(forResource: "data", withExtension: "json"),
           let bundleData = try? Data(contentsOf: bundleURL) {
            do {
                return try JSONDecoder().decode(DigestManifest.self, from: bundleData)
            } catch {
                // Bundle decode error
            }
        }

        throw URLError(.cannotConnectToHost)
    }
}
