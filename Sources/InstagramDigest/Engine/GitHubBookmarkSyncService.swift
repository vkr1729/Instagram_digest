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

@MainActor
public final class GitHubBookmarkSyncService {
    public static let shared = GitHubBookmarkSyncService()

    private init() {}

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
        guard let str, !str.isEmpty else { return Date.distantPast }
        if let d = isoFormatter.date(from: str) { return d }
        if let d = fallbackIsoFormatter.date(from: str) { return d }
        return Date.distantPast
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
                video_asset_name: nil,
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
                try? LibraryPathResolver.shared.ensureDirectoryExists(at: LibraryPathResolver.shared.bookmarksDirectoryURL)
                try? thumbData.write(to: dest, options: .atomic)
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
        let trimmedRepo = repo.trimmingCharacters(in: .whitespacesAndNewlines)
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

        let (data, response) = try await URLSession.shared.data(for: request)
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
        onProgress?(0, max(remoteDTOs.count, 1))
        for (index, dto) in remoteDTOs.enumerated() {
            allMergedDTOs[dto.id] = dto
            let id = dto.id
            let descriptor = FetchDescriptor<BookmarkItem>(predicate: #Predicate { $0.reelID == id })
            let existing = (try? context.fetch(descriptor))?.first
            if existing == nil {
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
                    bookmarkedAt: parseDate(dto.bookmarked_at),
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
                try? LibraryPathResolver.shared.ensureDirectoryExists(at: LibraryPathResolver.shared.bookmarksDirectoryURL)
                if let thumbApiURL = URL(string: "https://api.github.com/repos/\(trimmedRepo)/contents/thumbnails/\(safeID)_portrait.jpg") {
                    var thumbReq = URLRequest(url: thumbApiURL)
                    thumbReq.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
                    thumbReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
                    thumbReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")
                    if let (thumbJsonData, thumbRes) = try? await URLSession.shared.data(for: thumbReq),
                       let thumbHttp = thumbRes as? HTTPURLResponse, thumbHttp.statusCode == 200,
                       let thumbJson = try? JSONSerialization.jsonObject(with: thumbJsonData) as? [String: Any],
                       let contentB64 = thumbJson["content"] as? String {
                        let cleanB64 = contentB64.replacingOccurrences(of: "\n", with: "").replacingOccurrences(of: "\r", with: "")
                        if let imgData = Data(base64Encoded: cleanB64) {
                            try? imgData.write(to: thumbDest, options: .atomic)
                        }
                    } else if let urlStr = dto.thumbnail_url, urlStr.hasPrefix("http"), let remoteURL = URL(string: urlStr) {
                        if let (imgData, _) = try? await URLSession.shared.data(from: remoteURL) {
                            try? imgData.write(to: thumbDest, options: .atomic)
                        }
                    }
                }
            }
            onProgress?(index + 1, max(remoteDTOs.count, 1))
        }

        // 3. Merge local bookmarks that are missing remotely
        // Note: Deletes are local-only; re-sync from remote preserves existing remote entries.
        var newlyAddedLocal = 0
        for local in localBookmarks {
            if allMergedDTOs[local.reelID] == nil {
                let safeID = LibraryPathResolver.sanitizeComponent(local.reelID)
                // If local thumbnail exists, upload to repository thumbnails/ directory
                let thumbLocal = LibraryPathResolver.shared.bookmarkThumbnailFileURL(for: local.reelID)
                if let thumbData = try? Data(contentsOf: thumbLocal),
                   let thumbPutURL = URL(string: "https://api.github.com/repos/\(trimmedRepo)/contents/thumbnails/\(safeID)_portrait.jpg") {
                    Task.detached {
                        // AUDIT-031: GET existing thumbnail first to retrieve SHA if present (prevents 422 on re-upload)
                        var existingThumbSHA: String? = nil
                        var getReq = URLRequest(url: thumbPutURL)
                        getReq.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
                        getReq.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
                        getReq.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")
                        if let (getData, getRes) = try? await URLSession.shared.data(for: getReq),
                           let httpGet = getRes as? HTTPURLResponse, httpGet.statusCode == 200,
                           let json = try? JSONSerialization.jsonObject(with: getData) as? [String: Any],
                           let sha = json["sha"] as? String {
                            existingThumbSHA = sha
                        }

                        var thumbPut = URLRequest(url: thumbPutURL)
                        thumbPut.httpMethod = "PUT"
                        thumbPut.setValue("Bearer \(trimmedPAT)", forHTTPHeaderField: "Authorization")
                        thumbPut.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
                        thumbPut.setValue("application/json", forHTTPHeaderField: "Content-Type")
                        thumbPut.setValue("InstagramDigestDirect-iOS", forHTTPHeaderField: "User-Agent")
                        var body: [String: Any] = [
                            "message": "Add thumbnail for \(safeID)",
                            "content": thumbData.base64EncodedString()
                        ]
                        if let sha = existingThumbSHA {
                            body["sha"] = sha
                        }
                        thumbPut.httpBody = try? JSONSerialization.data(withJSONObject: body)
                        _ = try? await URLSession.shared.data(for: thumbPut)
                    }
                }

                let dto = BookmarkSyncDTO(
                    id: local.reelID,
                    creator_handle: local.creatorHandle,
                    caption: local.caption,
                    category: nil,
                    thumbnail_url: "thumbnails/\(safeID)_portrait.jpg",
                    thumbnail_base64: nil,
                    canonical_url: local.canonicalUrl?.absoluteString,
                    video_asset_name: nil,
                    size_bytes: local.sizeBytes,
                    bookmarked_at: isoFormatter.string(from: local.bookmarkedAt)
                )
                allMergedDTOs[local.reelID] = dto
                newlyAddedLocal += 1
            }
        }

        if importedCount > 0 {
            try context.save()
        }

        // 4. If local had new bookmarks, push updated bookmarks.json back to GitHub
        if newlyAddedLocal > 0 || remoteDTOs.isEmpty {
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

            let (putData, putRes) = try await URLSession.shared.data(for: putReq)
            guard let putHttp = putRes as? HTTPURLResponse, (200...299).contains(putHttp.statusCode) else {
                let err = (try? JSONSerialization.jsonObject(with: putData) as? [String: Any])?["message"] as? String ?? "HTTP error"
                throw NSError(domain: "GitHubSync", code: 500, userInfo: [NSLocalizedDescriptionKey: "Failed to push to GitHub: \(err)"])
            }
        }

        return BookmarkSyncResult(
            totalLocal: allMergedDTOs.count,
            importedCount: importedCount,
            pushedCount: newlyAddedLocal,
            message: "Sync complete! \(allMergedDTOs.count) total bookmarks (\(importedCount) imported, \(newlyAddedLocal) uploaded)."
        )
    }
}
