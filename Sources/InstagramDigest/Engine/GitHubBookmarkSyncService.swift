import Foundation
import SwiftData
import UIKit

public struct BookmarkSyncDTO: Codable, Sendable {
    public let id: String
    public let creator_handle: String
    public let caption: String?
    public let category: String?
    public let thumbnail_url: String?
    public let thumbnail_base64: String?
    public let canonical_url: String?
    public let video_asset_name: String?
    public let release_tag: String?
    public let size_bytes: Int64?
    public let bookmarked_at: String?

    public init(
        id: String,
        creator_handle: String,
        caption: String? = nil,
        category: String? = nil,
        thumbnail_url: String? = nil,
        thumbnail_base64: String? = nil,
        canonical_url: String? = nil,
        video_asset_name: String? = nil,
        release_tag: String? = nil,
        size_bytes: Int64? = nil,
        bookmarked_at: String? = nil
    ) {
        self.id = id
        self.creator_handle = creator_handle
        self.caption = caption
        self.category = category
        self.thumbnail_url = thumbnail_url
        self.thumbnail_base64 = thumbnail_base64
        self.canonical_url = canonical_url
        self.video_asset_name = video_asset_name
        self.release_tag = release_tag
        self.size_bytes = size_bytes
        self.bookmarked_at = bookmarked_at
    }
}

public struct BookmarkSyncResult: Sendable {
    public let totalLocal: Int
    public let importedCount: Int
    public let pushedCount: Int
    public let message: String
}

final class RedirectCaptureDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    var capturedLocation: URL?
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        if let locationString = response.value(forHTTPHeaderField: "Location"), let url = URL(string: locationString) {
            capturedLocation = url
        } else if let url = request.url {
            capturedLocation = url
        }
        completionHandler(nil)
    }
}

@MainActor
public final class GitHubBookmarkSyncService {
    public static let shared = GitHubBookmarkSyncService()

    public var session: URLSession = .shared
    public static var additionalProtocolClasses: [AnyClass] = []

    public private(set) var isSyncing: Bool = false
    public private(set) var needsResync: Bool = false
    private var memoryStreamCache: [String: (url: URL, expiresAt: Date)] = [:]

    private init() {}

    public nonisolated static func streamCacheKey(repo: String, tag: String, reelID: String) -> String {
        let sanitizedRepo = sanitizeRepo(repo)
        let safeTag = tag.trimmingCharacters(in: .whitespacesAndNewlines)
        let safeID = LibraryPathResolver.sanitizeComponent(reelID)
        return "\(sanitizedRepo)/\(safeTag)/\(safeID)"
    }

    public func clearMemoryStreamCache() {
        memoryStreamCache.removeAll()
    }

    public func setMemoryStreamCacheEntry(reelID: String, tag: String? = nil, year: Int = 2026, repo: String, url: URL, expiresAt: Date) {
        let validYear = year > 2000 ? year : Calendar.current.component(.year, from: Date())
        let effectiveTag: String
        if let tag = tag?.trimmingCharacters(in: .whitespacesAndNewlines), !tag.isEmpty {
            effectiveTag = tag
        } else {
            effectiveTag = "v\(validYear)"
        }
        let cacheKey = Self.streamCacheKey(repo: repo, tag: effectiveTag, reelID: reelID)
        memoryStreamCache[cacheKey] = (url, expiresAt)
    }

    private func makeEphemeralSession(delegate: URLSessionTaskDelegate) -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 15
        if !Self.additionalProtocolClasses.isEmpty {
            config.protocolClasses = Self.additionalProtocolClasses
        } else if let sessionClasses = session.configuration.protocolClasses, !sessionClasses.isEmpty {
            config.protocolClasses = sessionClasses
        }
        return URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
    }

    public nonisolated static func sanitizeRepo(_ repo: String) -> String {
        var trimmed = repo.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("https://github.com/") {
            trimmed = String(trimmed.dropFirst("https://github.com/".count))
        } else if trimmed.hasPrefix("http://github.com/") {
            trimmed = String(trimmed.dropFirst("http://github.com/".count))
        } else if trimmed.hasPrefix("github.com/") {
            trimmed = String(trimmed.dropFirst("github.com/".count))
        }
        if trimmed.hasSuffix(".git") {
            trimmed = String(trimmed.dropLast(4))
        }
        return trimmed.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    public nonisolated static func releaseTag(for date: Date) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
        let year = cal.component(.year, from: date)
        let validYear = year > 2000 ? year : Calendar.current.component(.year, from: Date())
        return "v\(validYear)"
    }

    public func resolveReleaseStreamURL(
        reelID: String,
        releaseTag: String? = nil,
        year: Int = 0,
        repo: String,
        pat: String
    ) async -> URL? {
        let trimmedRepo = Self.sanitizeRepo(repo)
        let trimmedPAT = pat.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedRepo.isEmpty, !trimmedPAT.isEmpty else { return nil }

        let safeID = LibraryPathResolver.sanitizeComponent(reelID)

        // Build list of candidate tags to check
        var candidateTags: [String] = []
        if let releaseTag = releaseTag?.trimmingCharacters(in: .whitespacesAndNewlines), !releaseTag.isEmpty {
            candidateTags.append(releaseTag)
        }
        let validYear = year > 2000 ? year : Calendar.current.component(.year, from: Date())
        let yearTag = "v\(validYear)"
        if !candidateTags.contains(yearTag) { candidateTags.append(yearTag) }
        let bareYearTag = "\(validYear)"
        if !candidateTags.contains(bareYearTag) { candidateTags.append(bareYearTag) }

        let primaryTag = candidateTags.first ?? yearTag
        let cacheKey = Self.streamCacheKey(repo: trimmedRepo, tag: primaryTag, reelID: reelID)
        if let cached = memoryStreamCache[cacheKey], cached.expiresAt > Date() {
            return cached.url
        }

        // Build list of candidate release URLs to check:
        // Candidate tags first, then latest release as ultimate fallback
        var releaseCandidateURLs: [URL] = []
        for tag in candidateTags {
            if let apiURL = URL(string: "https://api.github.com/repos/\(trimmedRepo)/releases/tags/\(tag)") {
                releaseCandidateURLs.append(apiURL)
            }
        }
        if let latestURL = URL(string: "https://api.github.com/repos/\(trimmedRepo)/releases/latest"),
           !releaseCandidateURLs.contains(latestURL) {
            releaseCandidateURLs.append(latestURL)
        }

        let targetAssetName = "\(safeID).mp4"
        let alternateAssetName = "\(reelID).mp4"
        let assetNamesToMatch = [targetAssetName, alternateAssetName]
        var targetAssetURLString: String? = nil

        // F-P2-1 & F-P3-8: Loop through candidate releases; if asset is missing in one, continue to the next
        for releaseURL in releaseCandidateURLs {
            if Task.isCancelled { return nil }
            var req = URLRequest(url: releaseURL)
            req.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
            req.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
            req.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

            guard let (data, resp) = try? await session.data(for: req),
                  let http = resp as? HTTPURLResponse, http.statusCode == 200,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                continue
            }

            // Check release embedded assets (first page, ~30 entries)
            if let assets = json["assets"] as? [[String: Any]] {
                let match = assets.first(where: {
                    guard let name = $0["name"] as? String else { return false }
                    return assetNamesToMatch.contains(name)
                })
                if let urlStr = match?["url"] as? String {
                    targetAssetURLString = urlStr
                    break
                }
            }

            // AUDIT-009 & F-P3-9: Paginated release assets following Link: rel="next" up to 500 assets
            if let releaseID = json["id"] as? Int {
                if let urlStr = await fetchReleaseAssetDownloadURL(
                    repo: trimmedRepo,
                    pat: trimmedPAT,
                    releaseID: releaseID,
                    assetNames: assetNamesToMatch,
                    maxPages: 5
                ) {
                    targetAssetURLString = urlStr
                    break
                }
            }
        }

        guard let assetURLString = targetAssetURLString,
              let assetURL = URL(string: assetURLString) else {
            return nil
        }

        let delegate = RedirectCaptureDelegate()
        let ephemeralSession = makeEphemeralSession(delegate: delegate)

        // AUDIT-008: Use trimmedPAT for asset redirect capture
        var assetReq = URLRequest(url: assetURL)
        assetReq.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
        assetReq.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        assetReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

        _ = try? await ephemeralSession.data(for: assetReq)
        ephemeralSession.finishTasksAndInvalidate()

        if let s3URL = delegate.capturedLocation {
            memoryStreamCache[cacheKey] = (s3URL, Date().addingTimeInterval(240))
            return s3URL
        }
        return nil
    }

    public func resolveReleaseStreamURL(reelID: String, year: Int, repo: String, pat: String) async -> URL? {
        await resolveReleaseStreamURL(reelID: reelID, releaseTag: nil, year: year, repo: repo, pat: pat)
    }

    /// AUDIT-009: Extract "next" page URL from GitHub API Link header
    public nonisolated static func extractNextPageURL(from linkHeader: String?) -> URL? {
        guard let linkHeader else { return nil }
        let parts = linkHeader.components(separatedBy: ",")
        for part in parts {
            let section = part.components(separatedBy: ";")
            if section.count >= 2 {
                let linkPart = section[0].trimmingCharacters(in: .whitespacesAndNewlines)
                let relPart = section[1].trimmingCharacters(in: .whitespacesAndNewlines)
                if relPart.contains("rel=\"next\"") || relPart.contains("rel=next") {
                    if linkPart.hasPrefix("<") && linkPart.hasSuffix(">") {
                        let urlString = String(linkPart.dropFirst().dropLast())
                        return URL(string: urlString)
                    }
                }
            }
        }
        return nil
    }

    /// AUDIT-009 & F-P3-9: Search paginated release assets for download URL matching candidate asset names
    private func fetchReleaseAssetDownloadURL(
        repo: String,
        pat: String,
        releaseID: Int,
        assetNames: [String],
        maxPages: Int = 5
    ) async -> String? {
        var nextPageURL: URL? = URL(string: "https://api.github.com/repos/\(repo)/releases/\(releaseID)/assets?per_page=100")
        var pageCount = 0

        while let currentURL = nextPageURL, pageCount < maxPages {
            if Task.isCancelled { return nil }
            pageCount += 1
            var aReq = URLRequest(url: currentURL)
            aReq.setValue("Bearer \(pat)", forHTTPHeaderField: "Authorization")
            aReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
            aReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

            guard let (aData, aResp) = try? await session.data(for: aReq),
                  let aHttp = aResp as? HTTPURLResponse, aHttp.statusCode == 200,
                  let assetList = try? JSONSerialization.jsonObject(with: aData) as? [[String: Any]] else {
                break
            }

            if let match = assetList.first(where: {
                guard let name = $0["name"] as? String else { return false }
                return assetNames.contains(name)
            }),
               let downloadURL = match["url"] as? String {
                return downloadURL
            }

            let linkHeader = aHttp.value(forHTTPHeaderField: "Link")
            if let nextFromLink = Self.extractNextPageURL(from: linkHeader) {
                nextPageURL = nextFromLink
            } else if assetList.count == 100 {
                nextPageURL = URL(string: "https://api.github.com/repos/\(repo)/releases/\(releaseID)/assets?per_page=100&page=\(pageCount + 1)")
            } else {
                nextPageURL = nil
            }
        }
        return nil
    }

    /// AUDIT-009: List all release asset names with Link: rel="next" pagination up to maxPages (default 5 pages / 500 assets)
    private func fetchAllReleaseAssetNames(
        repo: String,
        pat: String,
        releaseID: Int,
        maxPages: Int = 5
    ) async -> Set<String> {
        var names = Set<String>()
        var nextPageURL: URL? = URL(string: "https://api.github.com/repos/\(repo)/releases/\(releaseID)/assets?per_page=100")
        var pageCount = 0

        while let currentURL = nextPageURL, pageCount < maxPages {
            pageCount += 1
            var aReq = URLRequest(url: currentURL)
            aReq.setValue("Bearer \(pat)", forHTTPHeaderField: "Authorization")
            aReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
            aReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

            guard let (aData, aResp) = try? await session.data(for: aReq),
                  let aHttp = aResp as? HTTPURLResponse, aHttp.statusCode == 200,
                  let assetList = try? JSONSerialization.jsonObject(with: aData) as? [[String: Any]] else {
                break
            }

            for a in assetList {
                if let name = a["name"] as? String {
                    names.insert(name)
                }
            }

            let linkHeader = aHttp.value(forHTTPHeaderField: "Link")
            if let nextFromLink = Self.extractNextPageURL(from: linkHeader) {
                nextPageURL = nextFromLink
            } else if assetList.count == 100 {
                nextPageURL = URL(string: "https://api.github.com/repos/\(repo)/releases/\(releaseID)/assets?per_page=100&page=\(pageCount + 1)")
            } else {
                nextPageURL = nil
            }
        }
        return names
    }

    /// AUDIT-010: Structured thumbnail upload with retry x1
    private func uploadThumbnail(
        repo: String,
        pat: String,
        safeID: String,
        data: Data
    ) async -> Bool {
        guard let thumbPutURL = URL(string: "https://api.github.com/repos/\(repo)/contents/thumbnails/\(safeID)_portrait.jpg") else {
            return false
        }

        for attempt in 1...2 {
            var existingThumbSHA: String? = nil
            var getReq = URLRequest(url: thumbPutURL)
            getReq.setValue("Bearer \(pat)", forHTTPHeaderField: "Authorization")
            getReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
            getReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

            if let (getData, getRes) = try? await session.data(for: getReq),
               let httpGet = getRes as? HTTPURLResponse, httpGet.statusCode == 200,
               let json = try? JSONSerialization.jsonObject(with: getData) as? [String: Any],
               let sha = json["sha"] as? String {
                existingThumbSHA = sha
            }

            var thumbPut = URLRequest(url: thumbPutURL)
            thumbPut.httpMethod = "PUT"
            thumbPut.setValue("Bearer \(pat)", forHTTPHeaderField: "Authorization")
            thumbPut.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
            thumbPut.setValue("application/json", forHTTPHeaderField: "Content-Type")
            thumbPut.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

            var body: [String: Any] = [
                "message": "Add thumbnail for \(safeID)",
                "content": data.base64EncodedString()
            ]
            if let sha = existingThumbSHA {
                body["sha"] = sha
            }
            thumbPut.httpBody = try? JSONSerialization.data(withJSONObject: body)

            if let (_, putRes) = try? await session.data(for: thumbPut),
               let httpPut = putRes as? HTTPURLResponse, (httpPut.statusCode == 200 || httpPut.statusCode == 201) {
                return true
            }

            if attempt < 2 {
                try? await Task.sleep(nanoseconds: 300_000_000)
            }
        }
        return false
    }

    private let isoFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private let fallbackIsoFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private func parseDate(_ str: String?) -> Date {
        guard let str, !str.isEmpty else { return Date() }
        if let d = isoFormatter.date(from: str) { return d }
        if let d = fallbackIsoFormatter.date(from: str) { return d }
        let customFormatters = [
            "yyyy-MM-dd'T'HH:mm:ss.SSSSSSZ",
            "yyyy-MM-dd'T'HH:mm:ssZ",
            "yyyy-MM-dd HH:mm:ss",
            "yyyy-MM-dd"
        ]
        let df = DateFormatter()
        df.locale = Locale(identifier: "en_US_POSIX")
        df.timeZone = TimeZone(secondsFromGMT: 0)
        for fmt in customFormatters {
            df.dateFormat = fmt
            if let d = df.date(from: str) { return d }
        }
        return Date()
    }

    /// Export bookmarks to a temporary JSON file for sharing via UIActivityViewController
    public func exportBookmarksJSON(bookmarks: [BookmarkItem]) -> URL? {
        let resolver = LibraryPathResolver.shared
        let dtos: [BookmarkSyncDTO] = bookmarks.map { bm in
            let bmThumb = resolver.bookmarkThumbnailFileURL(for: bm.reelID)
            let weekThumb = resolver.thumbnailFileURL(for: bm.weekID, reelID: bm.reelID)
            var base64Thumb: String? = nil
            if let data = (try? Data(contentsOf: bmThumb)) ?? (try? Data(contentsOf: weekThumb)) {
                base64Thumb = data.base64EncodedString()
            }
            return BookmarkSyncDTO(
                id: bm.reelID,
                creator_handle: bm.creatorHandle,
                caption: bm.caption,
                category: nil,
                thumbnail_url: bm.thumbnailUrl?.absoluteString,
                thumbnail_base64: base64Thumb,
                canonical_url: bm.canonicalUrl?.absoluteString,
                video_asset_name: bm.videoAssetName,
                release_tag: bm.videoAssetName != nil ? (bm.releaseTag ?? GitHubBookmarkSyncService.releaseTag(for: bm.bookmarkedAt)) : nil,
                size_bytes: bm.sizeBytes,
                bookmarked_at: isoFormatter.string(from: bm.bookmarkedAt)
            )
        }

        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(dtos)
            let tempDir = FileManager.default.temporaryDirectory
            let fileURL = tempDir.appendingPathComponent("ig_bookmarks_backup.json")
            try data.write(to: fileURL, options: .atomic)
            return fileURL
        } catch {
            return nil
        }
    }

    /// Import bookmarks from a selected JSON file into SwiftData
    public func importBookmarks(from fileURL: URL, context: ModelContext) throws -> Int {
        let shouldStopAccessing = fileURL.startAccessingSecurityScopedResource()
        defer {
            if shouldStopAccessing {
                fileURL.stopAccessingSecurityScopedResource()
            }
        }

        let data = try Data(contentsOf: fileURL)
        let decoder = JSONDecoder()
        let dtos = try decoder.decode([BookmarkSyncDTO].self, from: data)

        var newCount = 0
        for dto in dtos {
            guard !dto.id.isEmpty else { continue }
            let id = dto.id
            let descriptor = FetchDescriptor<BookmarkItem>(
                predicate: #Predicate { $0.reelID == id }
            )
            let existing = (try? context.fetch(descriptor))?.first
            if existing == nil {
                let canonical = dto.canonical_url.flatMap { URL(string: $0) }
                    ?? URL(string: "https://www.instagram.com/reel/\(dto.id)/")
                let thumb = dto.thumbnail_url.flatMap { URL(string: $0) }
                    ?? URL(string: "https://vkr1729.github.io/Instagram_digest/thumbnails/\(dto.id)_portrait.jpg")
                let item = BookmarkItem(
                    reelID: dto.id,
                    weekID: "imported",
                    creatorHandle: dto.creator_handle.isEmpty ? "unknown" : dto.creator_handle,
                    caption: dto.caption ?? "",
                    rank: 0,
                    videoUrl: nil,
                    thumbnailUrl: thumb,
                    canonicalUrl: canonical,
                    videoAssetName: dto.video_asset_name,
                    releaseTag: dto.release_tag,
                    bookmarkedAt: parseDate(dto.bookmarked_at),
                    localStatus: .evicted,
                    sizeBytes: dto.size_bytes ?? 0,
                    lastAccessedAt: Date()
                )
                context.insert(item)
                newCount += 1
            }

            // Restore embedded thumbnail if present
            if let b64 = dto.thumbnail_base64, let thumbData = Data(base64Encoded: b64) {
                let dest = LibraryPathResolver.shared.bookmarkThumbnailFileURL(for: dto.id)
                _ = try? LibraryPathResolver.shared.ensureDirectoryExists(at: LibraryPathResolver.shared.bookmarksDirectoryURL)
                _ = try? thumbData.write(to: dest, options: .atomic)
            }
        }

        if newCount > 0 {
            try context.save()
        }
        return newCount
    }

    /// Perform bidirectional sync with private GitHub repository
    public func syncWithGitHub(
        repo: String,
        pat: String,
        localBookmarks: [BookmarkItem],
        context: ModelContext,
        onProgress: (@Sendable (_ current: Int, _ total: Int) -> Void)? = nil
    ) async throws -> BookmarkSyncResult {
        let trimmedRepo = Self.sanitizeRepo(repo)
        let trimmedPAT = pat.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !trimmedRepo.isEmpty else {
            throw NSError(domain: "GitHubSync", code: 400, userInfo: [NSLocalizedDescriptionKey: "GitHub repository not specified (e.g. vkreddy1729-ops/ig-bookmarks-2026)."])
        }
        let repoParts = trimmedRepo.split(separator: "/")
        guard repoParts.count == 2, !repoParts[0].isEmpty, !repoParts[1].isEmpty, !trimmedRepo.contains(" ") else {
            throw NSError(domain: "GitHubSync", code: 400, userInfo: [NSLocalizedDescriptionKey: "Invalid GitHub repository format (expected owner/repo)."])
        }
        guard !trimmedPAT.isEmpty else {
            throw NSError(domain: "GitHubSync", code: 401, userInfo: [NSLocalizedDescriptionKey: "Personal Access Token (PAT) required."])
        }

        // 1. Fetch remote bookmarks.json from repository
        guard let apiURL = URL(string: "https://api.github.com/repos/\(trimmedRepo)/contents/bookmarks.json") else {
            throw NSError(domain: "GitHubSync", code: 400, userInfo: [NSLocalizedDescriptionKey: "Invalid GitHub repository URL."])
        }
        var request = URLRequest(url: apiURL)
        request.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
        request.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
        request.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

        let (data, response) = try await session.data(for: request)
        guard let httpRes = response as? HTTPURLResponse else {
            throw NSError(domain: "GitHubSync", code: 500, userInfo: [NSLocalizedDescriptionKey: "Invalid server response."])
        }

        var remoteDTOs: [BookmarkSyncDTO] = []
        var existingFileSHA: String? = nil

        if httpRes.statusCode == 200 {
            // Parse GitHub contents response
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let contentBase64 = json["content"] as? String,
               let sha = json["sha"] as? String {
                existingFileSHA = sha
                let cleanBase64 = contentBase64.replacingOccurrences(of: "\n", with: "").replacingOccurrences(of: "\r", with: "")
                if let decodedData = Data(base64Encoded: cleanBase64) {
                    remoteDTOs = (try? JSONDecoder().decode([BookmarkSyncDTO].self, from: decodedData)) ?? []
                }
            }
        } else if httpRes.statusCode != 404 {
            let errorMsg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["message"] as? String ?? "HTTP \(httpRes.statusCode)"
            throw NSError(domain: "GitHubSync", code: httpRes.statusCode, userInfo: [NSLocalizedDescriptionKey: "GitHub API error: \(errorMsg)"])
        }

        // 2. Merge remote into local SwiftData
        var importedCount = 0
        var allMergedDTOs: [String: BookmarkSyncDTO] = [:]
        var dtoMutated = false
        let tombstones = MediaCacheManager.unbookmarkedTombstones
        var clearedTombstones: [String] = []

        onProgress?(0, max(remoteDTOs.count, 1))
        for (index, dto) in remoteDTOs.enumerated() {
            if tombstones.contains(dto.id) {
                // User explicitly unsaved this item locally; omit from sync to persist deletion to GitHub
                dtoMutated = true
                clearedTombstones.append(dto.id)
                continue
            }
            allMergedDTOs[dto.id] = dto
            let id = dto.id
            let descriptor = FetchDescriptor<BookmarkItem>(predicate: #Predicate { $0.reelID == id })
            let existing = (try? context.fetch(descriptor))?.first
            let parsedDate = parseDate(dto.bookmarked_at)
            let resolvedTag = dto.release_tag ?? (dto.video_asset_name != nil ? Self.releaseTag(for: parsedDate) : nil)
            if let item = existing {
                if item.videoAssetName == nil && dto.video_asset_name != nil {
                    item.videoAssetName = dto.video_asset_name
                    item.releaseTag = resolvedTag
                }
                if item.releaseTag == nil && resolvedTag != nil {
                    item.releaseTag = resolvedTag
                }
            } else {
                let canonical = dto.canonical_url.flatMap { URL(string: $0) }
                    ?? URL(string: "https://www.instagram.com/reel/\(dto.id)/")
                let thumb = dto.thumbnail_url.flatMap { URL(string: $0) }
                    ?? URL(string: "https://vkr1729.github.io/Instagram_digest/thumbnails/\(dto.id)_portrait.jpg")
                let item = BookmarkItem(
                    reelID: dto.id,
                    weekID: "github_sync",
                    creatorHandle: dto.creator_handle.isEmpty ? "unknown" : dto.creator_handle,
                    caption: dto.caption ?? "",
                    rank: 0,
                    videoUrl: nil,
                    thumbnailUrl: thumb,
                    canonicalUrl: canonical,
                    videoAssetName: dto.video_asset_name,
                    releaseTag: resolvedTag,
                    bookmarkedAt: parsedDate,
                    localStatus: .evicted,
                    sizeBytes: dto.size_bytes ?? 0,
                    lastAccessedAt: Date()
                )
                context.insert(item)
                importedCount += 1
            }

            // Cache thumbnail locally so new device displays covers offline
            let safeID = LibraryPathResolver.sanitizeComponent(dto.id)
            let thumbDest = LibraryPathResolver.shared.bookmarkThumbnailFileURL(for: safeID)
            if !FileManager.default.fileExists(atPath: thumbDest.path) {
                _ = try? LibraryPathResolver.shared.ensureDirectoryExists(at: LibraryPathResolver.shared.bookmarksDirectoryURL)
                if let thumbApiURL = URL(string: "https://api.github.com/repos/\(trimmedRepo)/contents/thumbnails/\(safeID)_portrait.jpg") {
                    var thumbReq = URLRequest(url: thumbApiURL)
                    thumbReq.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
                    thumbReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
                    thumbReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")
                    if let (thumbJsonData, thumbRes) = try? await session.data(for: thumbReq),
                       let thumbHttp = thumbRes as? HTTPURLResponse, thumbHttp.statusCode == 200,
                       let thumbJson = try? JSONSerialization.jsonObject(with: thumbJsonData) as? [String: Any],
                       let contentB64 = thumbJson["content"] as? String {
                        let cleanB64 = contentB64.replacingOccurrences(of: "\n", with: "").replacingOccurrences(of: "\r", with: "")
                        if let imgData = Data(base64Encoded: cleanB64) {
                            try? imgData.write(to: thumbDest, options: .atomic)
                        }
                    } else if let urlStr = dto.thumbnail_url, urlStr.hasPrefix("http"), let remoteURL = URL(string: urlStr) {
                        if let (imgData, _) = try? await session.data(from: remoteURL) {
                            try? imgData.write(to: thumbDest, options: .atomic)
                        }
                    }
                }
            }
            onProgress?(index + 1, max(remoteDTOs.count, 1))
        }

        // 3. Upload locally cached videos to GitHub Releases (v{year})
        let canUploadVideos = !Reachability.shared.isExpensive

        if canUploadVideos {
            var bookmarksByTag: [String: [BookmarkItem]] = [:]
            for local in localBookmarks {
                let remoteHasAsset = (allMergedDTOs[local.reelID]?.video_asset_name != nil)

                if !remoteHasAsset,
                   LibraryPathResolver.shared.resolvedLocalFileURL(for: local.weekID, reelID: local.reelID) != nil {
                    let tag = local.releaseTag ?? GitHubBookmarkSyncService.releaseTag(for: local.bookmarkedAt)
                    bookmarksByTag[tag, default: []].append(local)
                }
            }

            for (tag, bookmarksToUpload) in bookmarksByTag {
                var releaseID: Int? = nil
                var baseUploadURLString: String? = nil

                if let tagApiURL = URL(string: "https://api.github.com/repos/\(trimmedRepo)/releases/tags/\(tag)") {
                    var tagReq = URLRequest(url: tagApiURL)
                    tagReq.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
                    tagReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
                    tagReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

                    if let (tagData, tagResp) = try? await session.data(for: tagReq),
                       let tagHttp = tagResp as? HTTPURLResponse {
                        if tagHttp.statusCode == 200,
                           let json = try? JSONSerialization.jsonObject(with: tagData) as? [String: Any] {
                            releaseID = json["id"] as? Int
                            baseUploadURLString = (json["upload_url"] as? String)?.components(separatedBy: "{").first
                        } else if tagHttp.statusCode == 404 {
                            if let createURL = URL(string: "https://api.github.com/repos/\(trimmedRepo)/releases") {
                                var createReq = URLRequest(url: createURL)
                                createReq.httpMethod = "POST"
                                createReq.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
                                createReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
                                createReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
                                createReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")
                                let body: [String: Any] = [
                                    "tag_name": tag,
                                    "name": "\(tag) Bookmarked Reels Media Assets",
                                    "notes": "Permanent video backup for \(tag) bookmarks"
                                ]
                                createReq.httpBody = try? JSONSerialization.data(withJSONObject: body)
                                if let (cData, cResp) = try? await session.data(for: createReq),
                                   let cHttp = cResp as? HTTPURLResponse, (cHttp.statusCode == 201 || cHttp.statusCode == 422),
                                   let cJson = try? JSONSerialization.jsonObject(with: cData) as? [String: Any] {
                                    releaseID = cJson["id"] as? Int
                                    baseUploadURLString = (cJson["upload_url"] as? String)?.components(separatedBy: "{").first
                                }
                            }
                        }
                    }
                }

                if let baseUploadURL = baseUploadURLString, let relID = releaseID {
                    // AUDIT-009: Paged asset listing following Link: rel="next" up to 500 assets
                    var existingAssetNames = await fetchAllReleaseAssetNames(
                        repo: trimmedRepo,
                        pat: trimmedPAT,
                        releaseID: relID,
                        maxPages: 5
                    )

                    for local in bookmarksToUpload {
                        let safeID = LibraryPathResolver.sanitizeComponent(local.reelID)
                        let assetName = "\(safeID).mp4"
                        guard let localFile = LibraryPathResolver.shared.resolvedLocalFileURL(for: local.weekID, reelID: local.reelID) else { continue }

                        if !existingAssetNames.contains(assetName) {
                            if let uploadURL = URL(string: "\(baseUploadURL)?name=\(assetName)") {
                                var upReq = URLRequest(url: uploadURL)
                                upReq.httpMethod = "POST"
                                upReq.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
                                upReq.setValue("video/mp4", forHTTPHeaderField: "Content-Type")
                                upReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
                                upReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")

                                if let (_, upResp) = try? await session.upload(for: upReq, fromFile: localFile),
                                   let upHttp = upResp as? HTTPURLResponse, (upHttp.statusCode == 201 || upHttp.statusCode == 422) {
                                    existingAssetNames.insert(assetName)
                                    local.videoAssetName = assetName
                                    local.releaseTag = tag
                                    dtoMutated = true
                                }
                            }
                        } else {
                            local.videoAssetName = assetName
                            local.releaseTag = tag
                            dtoMutated = true
                        }
                    }
                }
            }
        }

        // 4. Merge local bookmarks that are missing remotely
        var newlyAddedLocal = 0
        var thumbnailFailures = 0

        for local in localBookmarks {
            let safeID = LibraryPathResolver.sanitizeComponent(local.reelID)
            let existingRemote = allMergedDTOs[local.reelID]

            if existingRemote == nil {
                // AUDIT-010: Structured, awaited thumbnail upload with fallback to original URL
                var resolvedThumbURL = local.thumbnailUrl?.absoluteString
                let thumbLocal = LibraryPathResolver.shared.bookmarkThumbnailFileURL(for: local.reelID)
                if let thumbData = try? Data(contentsOf: thumbLocal) {
                    let uploaded = await uploadThumbnail(
                        repo: trimmedRepo,
                        pat: trimmedPAT,
                        safeID: safeID,
                        data: thumbData
                    )
                    if uploaded {
                        resolvedThumbURL = "thumbnails/\(safeID)_portrait.jpg"
                    } else {
                        thumbnailFailures += 1
                    }
                }

                // AUDIT-001: Never claim an unconfirmed asset.
                // Set video_asset_name in the pushed DTO strictly from local.videoAssetName
                // (which is only set when confirmed via 201/422 upload or presence-check hit).
                // If local file exists on disk but upload was skipped (e.g. cellular) or failed,
                // push nil so the next Wi-Fi sync retries the upload.
                let assetName: String? = local.videoAssetName
                let tag = assetName != nil ? (local.releaseTag ?? GitHubBookmarkSyncService.releaseTag(for: local.bookmarkedAt)) : nil

                let dto = BookmarkSyncDTO(
                    id: local.reelID,
                    creator_handle: local.creatorHandle,
                    caption: local.caption,
                    category: nil,
                    thumbnail_url: resolvedThumbURL,
                    thumbnail_base64: nil,
                    canonical_url: local.canonicalUrl?.absoluteString,
                    video_asset_name: assetName,
                    release_tag: tag,
                    size_bytes: local.sizeBytes,
                    bookmarked_at: isoFormatter.string(from: local.bookmarkedAt)
                )
                allMergedDTOs[local.reelID] = dto
                newlyAddedLocal += 1
            } else {
                var needsDTOUpdate = false
                var currentThumbURL = existingRemote?.thumbnail_url

                // If remote has no thumbnail or has an http URL and we have local thumbnail, attempt upload
                if currentThumbURL == nil || currentThumbURL?.hasPrefix("http") == true {
                    let thumbLocal = LibraryPathResolver.shared.bookmarkThumbnailFileURL(for: local.reelID)
                    if let thumbData = try? Data(contentsOf: thumbLocal) {
                        let uploaded = await uploadThumbnail(
                            repo: trimmedRepo,
                            pat: trimmedPAT,
                            safeID: safeID,
                            data: thumbData
                        )
                        if uploaded {
                            currentThumbURL = "thumbnails/\(safeID)_portrait.jpg"
                            needsDTOUpdate = true
                        }
                    }
                }

                if local.videoAssetName != nil && existingRemote?.video_asset_name != local.videoAssetName {
                    needsDTOUpdate = true
                }

                if needsDTOUpdate {
                    let assetName = local.videoAssetName ?? existingRemote?.video_asset_name
                    let tag = assetName != nil ? (local.releaseTag ?? existingRemote?.release_tag ?? GitHubBookmarkSyncService.releaseTag(for: local.bookmarkedAt)) : nil
                    let updatedDTO = BookmarkSyncDTO(
                        id: local.reelID,
                        creator_handle: local.creatorHandle,
                        caption: local.caption,
                        category: existingRemote?.category,
                        thumbnail_url: currentThumbURL ?? existingRemote?.thumbnail_url ?? local.thumbnailUrl?.absoluteString,
                        thumbnail_base64: nil,
                        canonical_url: local.canonicalUrl?.absoluteString,
                        video_asset_name: assetName,
                        release_tag: tag,
                        size_bytes: local.sizeBytes,
                        bookmarked_at: existingRemote?.bookmarked_at ?? isoFormatter.string(from: local.bookmarkedAt)
                    )
                    allMergedDTOs[local.reelID] = updatedDTO
                    dtoMutated = true
                }
            }
        }

        if importedCount > 0 || dtoMutated {
            try context.save()
        }

        // 5. If local had new bookmarks or assets uploaded, push updated bookmarks.json back to GitHub
        if dtoMutated || newlyAddedLocal > 0 || remoteDTOs.isEmpty {
            let sortedList = Array(allMergedDTOs.values).sorted { $0.bookmarked_at ?? "" > $1.bookmarked_at ?? "" }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let payloadData = try encoder.encode(sortedList)
            let base64Content = payloadData.base64EncodedString()

            var putBody: [String: Any] = [
                "message": "Sync \(sortedList.count) bookmarks from IG Direct",
                "content": base64Content
            ]
            if let sha = existingFileSHA {
                putBody["sha"] = sha
            }

            var putReq = URLRequest(url: apiURL)
            putReq.httpMethod = "PUT"
            putReq.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
            putReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
            putReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
            putReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")
            putReq.httpBody = try JSONSerialization.data(withJSONObject: putBody)

            let (putData, putRes) = try await session.data(for: putReq)
            guard let putHttp = putRes as? HTTPURLResponse, (200...299).contains(putHttp.statusCode) else {
                let err = (try? JSONSerialization.jsonObject(with: putData) as? [String: Any])?["message"] as? String ?? "HTTP error"
                throw NSError(domain: "GitHubSync", code: 500, userInfo: [NSLocalizedDescriptionKey: "Failed to push to GitHub: \(err)"])
            }
            for id in clearedTombstones {
                MediaCacheManager.clearUnbookmarkedTombstone(id)
            }
        }

        var resultMessage = "Sync complete! \(allMergedDTOs.count) total bookmarks (\(importedCount) imported, \(newlyAddedLocal) uploaded)."
        if thumbnailFailures > 0 {
            resultMessage += " (\(thumbnailFailures) thumbnail upload(s) failed, preserving original URLs)."
        }
        return BookmarkSyncResult(
            totalLocal: allMergedDTOs.count,
            importedCount: importedCount,
            pushedCount: newlyAddedLocal,
            message: resultMessage
        )
    }

    /// Centralized silent sync with in-flight coalescing
    public func syncIfNeeded(context: ModelContext) async {
        let repo = Self.sanitizeRepo(UserDefaults.standard.string(forKey: "gh_bookmark_repo") ?? "vkreddy1729-ops/ig-bookmarks")
        let pat = UserDefaults.standard.string(forKey: "gh_bookmark_pat") ?? ""
        guard !repo.isEmpty, !pat.isEmpty else { return }

        if isSyncing {
            needsResync = true
            return
        }
        isSyncing = true
        defer {
            isSyncing = false
            if needsResync {
                needsResync = false
                Task { [weak self] in
                    await self?.syncIfNeeded(context: context)
                }
            }
        }

        let descriptor = FetchDescriptor<BookmarkItem>()
        guard let local = try? context.fetch(descriptor) else { return }
        _ = try? await syncWithGitHub(repo: repo, pat: pat, localBookmarks: local, context: context)
    }
}
