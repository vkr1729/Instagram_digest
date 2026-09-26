import Foundation

extension URLResponse {
    /// URLSession downloads "succeed" on 4xx/5xx and return the error body.
    var isHTTPSuccess: Bool {
        ((self as? HTTPURLResponse)?.statusCode).map { (200...299).contains($0) } ?? false
    }
}

/// Accepts only absolute http(s) URLs. `URL(string:)` also parses relative
/// paths ("foo.mp4") and custom schemes, which fail later in URLSession /
/// AVPlayer — reject them at decode time so the lossy decoders skip the
/// entry instead of publishing an unplayable card (IOS-P2-9).
func httpURL(from string: String?) -> URL? {
    guard let string = string,
          let url = URL(string: string),
          let scheme = url.scheme?.lowercased(),
          (scheme == "http" || scheme == "https"),
          url.host != nil else { return nil }
    return url
}

/// Codable root manifest returned by data.json on GitHub Pages or local cache.
public struct DigestManifest: Sendable, Codable {
    public let weekId: String
    public let items: [ReelItem]
    public let count: Int
    public let generatedAt: Date?

    public init(
        weekId: String,
        items: [ReelItem],
        count: Int? = nil,
        generatedAt: Date? = nil
    ) {
        self.weekId = weekId
        self.items = items
        self.count = count ?? items.count
        self.generatedAt = generatedAt
    }

    enum CodingKeys: String, CodingKey {
        case runDate = "run_date"
        case weekId = "week_id"
        case camelWeekId = "weekId"
        case items
        case count
        case generatedAt = "generated_at"
    }

    private struct AnyDecodableValue: Decodable {
        init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if container.decodeNil() { return }
            if (try? container.decode(Bool.self)) != nil { return }
            if (try? container.decode(Int64.self)) != nil { return }
            if (try? container.decode(Double.self)) != nil { return }
            if (try? container.decode(String.self)) != nil { return }
            if (try? decoder.container(keyedBy: DynamicCodingKeys.self)) != nil { return }
            if (try? decoder.unkeyedContainer()) != nil { return }
        }
        private struct DynamicCodingKeys: CodingKey {
            var stringValue: String
            init?(stringValue: String) { self.stringValue = stringValue }
            var intValue: Int?
            init?(intValue: Int) { return nil }
        }
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)

        let dateStr = (try? container.decode(String.self, forKey: .runDate))
            ?? (try? container.decode(String.self, forKey: .weekId))
            ?? (try? container.decode(String.self, forKey: .camelWeekId))
            ?? "unknown_week"
        self.weekId = dateStr

        // Lossy decoding: one corrupt reel must never wipe the entire feed
        var decodedItems: [ReelItem] = []
        var seenIDs = Set<String>()
        if var itemsContainer = try? container.nestedUnkeyedContainer(forKey: .items) {
            // Bounded loop: a decoder that fails to advance past a corrupt entry
            // must never spin forever on a hostile payload.
            var guardCount = 0
            while !itemsContainer.isAtEnd, guardCount < 10_000 {
                guardCount += 1
                if let reel = try? itemsContainer.decode(ReelItem.self) {
                    // IOS-P0-3: duplicate IDs crash ForEach(id:) in GridView.
                    // Keep the first occurrence; drop later duplicates.
                    if seenIDs.insert(reel.id).inserted {
                        decodedItems.append(reel)
                    }
                } else {
                    // Advance past invalid item of any shape (object, array, primitive)
                    _ = try? itemsContainer.decode(AnyDecodableValue.self)
                }
            }
        }
        self.items = decodedItems
        self.count = (try? container.decode(Int.self, forKey: .count)) ?? self.items.count

        if let genStr = try? container.decode(String.self, forKey: .generatedAt) {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: genStr) {
                self.generatedAt = date
            } else {
                formatter.formatOptions = [.withInternetDateTime]
                self.generatedAt = formatter.date(from: genStr)
            }
        } else {
            self.generatedAt = nil
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(weekId, forKey: .runDate)
        try container.encode(items, forKey: .items)
        try container.encode(count, forKey: .count)
        if let gen = generatedAt {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            try container.encode(formatter.string(from: gen), forKey: .generatedAt)
        }
    }

    public func item(withId id: String) -> ReelItem? {
        items.first { $0.id == id }
    }

    public func index(ofId id: String) -> Int? {
        items.firstIndex { $0.id == id }
    }
}

/// Remote DTO returned as an unkeyed array from Cloudflare R2 worker at bookmarks/manifest.json
public struct BookmarkRemoteDTO: Sendable, Codable {
    public let id: String
    public let creatorHandle: String
    public let caption: String
    public let category: String?
    public let thumbnailUrl: URL?
    public let videoUrl: URL
    public let sizeBytes: Int64?
    public let bookmarkedAt: String?
    public let telegramMessageId: Int?

    public init(
        id: String,
        creatorHandle: String,
        caption: String,
        category: String? = nil,
        thumbnailUrl: URL? = nil,
        videoUrl: URL,
        sizeBytes: Int64? = nil,
        bookmarkedAt: String? = nil,
        telegramMessageId: Int? = nil
    ) {
        self.id = id
        self.creatorHandle = creatorHandle
        self.caption = caption
        self.category = category
        self.thumbnailUrl = thumbnailUrl
        self.videoUrl = videoUrl
        self.sizeBytes = sizeBytes
        self.bookmarkedAt = bookmarkedAt
        self.telegramMessageId = telegramMessageId
    }

    enum CodingKeys: String, CodingKey {
        case id
        case creatorHandle = "creator_handle"
        case altCreatorHandle = "creatorHandle"
        case caption
        case category
        case thumbnailUrl = "thumbnail_url"
        case altThumbnailUrl = "thumbnailUrl"
        case thumbnail
        case poster
        case videoUrl = "video_url"
        case altVideoUrl = "videoUrl"
        case r2Url = "r2_url"
        case sizeBytes = "size_bytes"
        case altSizeBytes = "sizeBytes"
        case bookmarkedAt = "bookmarked_at"
        case altBookmarkedAt = "bookmarkedAt"
        case telegramMessageId = "telegram_message_id"
        case altTelegramMessageId = "telegramMessageId"
    }

    /// Resilient decoding: `id` and a usable video URL are mandatory (an entry
    /// without them is skipped by the lossy array decoder); every other field
    /// falls back to a safe default or an alternate key spelling instead of
    /// throwing, so one malformed field never invalidates the entry.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.creatorHandle = (try? container.decode(String.self, forKey: .creatorHandle))
            ?? (try? container.decode(String.self, forKey: .altCreatorHandle))
            ?? "creator"
        self.caption = (try? container.decode(String.self, forKey: .caption)) ?? ""
        self.category = try? container.decode(String.self, forKey: .category)

        var resolvedThumb: URL? = nil
        for key in [CodingKeys.thumbnailUrl, .altThumbnailUrl, .thumbnail, .poster] {
            if let str = try? container.decode(String.self, forKey: key),
               let url = httpURL(from: str) {
                resolvedThumb = url
                break
            }
        }
        self.thumbnailUrl = resolvedThumb

        var resolvedVideo: URL? = nil
        for key in [CodingKeys.videoUrl, .altVideoUrl, .r2Url] {
            if let str = try? container.decode(String.self, forKey: key),
               let url = httpURL(from: str) {
                resolvedVideo = url
                break
            }
        }
        guard let validVideoURL = resolvedVideo else {
            throw DecodingError.dataCorruptedError(
                forKey: .videoUrl,
                in: container,
                debugDescription: "Missing or invalid video URL for bookmark \(self.id)"
            )
        }
        self.videoUrl = validVideoURL

        self.sizeBytes = (try? container.decode(Int64.self, forKey: .sizeBytes))
            ?? (try? container.decode(Int64.self, forKey: .altSizeBytes))
        self.bookmarkedAt = (try? container.decode(String.self, forKey: .bookmarkedAt))
            ?? (try? container.decode(String.self, forKey: .altBookmarkedAt))
        self.telegramMessageId = (try? container.decode(Int.self, forKey: .telegramMessageId))
            ?? (try? container.decode(Int.self, forKey: .altTelegramMessageId))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(creatorHandle, forKey: .creatorHandle)
        try container.encode(caption, forKey: .caption)
        try container.encodeIfPresent(category, forKey: .category)
        try container.encodeIfPresent(thumbnailUrl?.absoluteString, forKey: .thumbnailUrl)
        try container.encode(videoUrl.absoluteString, forKey: .videoUrl)
        try container.encodeIfPresent(sizeBytes, forKey: .sizeBytes)
        try container.encodeIfPresent(bookmarkedAt, forKey: .bookmarkedAt)
        try container.encodeIfPresent(telegramMessageId, forKey: .telegramMessageId)
    }
}

/// Lossy decoder for the remote bookmarks manifest: one malformed entry is
/// skipped while every well-formed entry is preserved.
public enum LossyBookmarkList {
    private struct SkippedValue: Decodable {
        init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if container.decodeNil() { return }
            if (try? container.decode(Bool.self)) != nil { return }
            if (try? container.decode(Int64.self)) != nil { return }
            if (try? container.decode(Double.self)) != nil { return }
            if (try? container.decode(String.self)) != nil { return }
            if (try? decoder.container(keyedBy: DynamicKeys.self)) != nil { return }
            if (try? decoder.unkeyedContainer()) != nil { return }
        }
        private struct DynamicKeys: CodingKey {
            var stringValue: String
            init?(stringValue: String) { self.stringValue = stringValue }
            var intValue: Int?
            init?(intValue: Int) { return nil }
        }
    }

    /// Decodes either a bare JSON array of bookmarks or an object wrapping the
    /// array under `bookmarks`/`items`, skipping malformed entries. Throws only
    /// when the payload has neither shape.
    public static func decode(from data: Data) throws -> [BookmarkRemoteDTO] {
        let decoder = JSONDecoder()
        if let strict = try? decoder.decode([BookmarkRemoteDTO].self, from: data) {
            return strict
        }
        // IOS-P1-9: the wrapped shape must be lossy too. Each element is
        // re-encoded in isolation so one malformed entry can never poison a
        // shared decoder and wipe the whole list.
        if let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let arr = (raw["bookmarks"] ?? raw["items"]) as? [Any] {
            var output: [BookmarkRemoteDTO] = []
            for element in arr {
                guard JSONSerialization.isValidJSONObject(element),
                      let elData = try? JSONSerialization.data(withJSONObject: element),
                      let dto = try? decoder.decode(BookmarkRemoteDTO.self, from: elData) else { continue }
                output.append(dto)
            }
            return output
        }
        // Fully lossy element-by-element pass: malformed entries are skipped.
        var output: [BookmarkRemoteDTO] = []
        var container = try decoder.unkeyedContainer(from: data)
        var guardCount = 0
        while !container.isAtEnd, guardCount < 10_000 {
            guardCount += 1
            if let dto = try? container.decode(BookmarkRemoteDTO.self) {
                output.append(dto)
            } else {
                _ = try? container.decode(SkippedValue.self)
            }
        }
        // An empty result from a non-empty payload means the shape itself is
        // wrong (e.g. a single object); surface that instead of silent success.
        if output.isEmpty,
           let raw = try? JSONSerialization.jsonObject(with: data),
           !(raw is [Any]) {
            throw DecodingError.dataCorrupted(
                DecodingError.Context(codingPath: [], debugDescription: "Bookmarks payload is not an array")
            )
        }
        return output
    }
}

private extension JSONDecoder {
    /// Decodes a top-level unkeyed container straight from data.
    func unkeyedContainer(from data: Data) throws -> UnkeyedDecodingContainer {
        struct ArrayBox: Decodable {
            var container: UnkeyedDecodingContainer
            init(from decoder: Decoder) throws {
                container = try decoder.unkeyedContainer()
            }
        }
        return try self.decode(ArrayBox.self, from: data).container
    }
}

