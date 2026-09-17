import Foundation

/// Codable and Sendable immutable model representing an individual curated Instagram Reel.
public struct ReelItem: Identifiable, Sendable, Hashable, Codable {
    public let id: String
    public let creatorHandle: String
    public let creatorName: String?
    public let caption: String
    public let rank: Int
    public let rankDisplay: String
    public let videoUrl: URL
    public let thumbnailUrl: URL?
    public let category: String?
    public let viewCount: Int?
    public let likeCount: Int?
    public let commentCount: Int?
    public let isExternal: Bool
    public let sizeBytes: Int64?

    public init(
        id: String,
        creatorHandle: String,
        creatorName: String? = nil,
        caption: String,
        rank: Int,
        rankDisplay: String? = nil,
        videoUrl: URL,
        thumbnailUrl: URL? = nil,
        category: String? = nil,
        viewCount: Int? = nil,
        likeCount: Int? = nil,
        commentCount: Int? = nil,
        isExternal: Bool = false,
        sizeBytes: Int64? = nil
    ) {
        self.id = id
        self.creatorHandle = creatorHandle
        self.creatorName = creatorName
        self.caption = caption
        self.rank = rank
        self.rankDisplay = rankDisplay ?? String(format: "#%02d", rank)
        self.videoUrl = videoUrl
        self.thumbnailUrl = thumbnailUrl
        self.category = category
        self.viewCount = viewCount
        self.likeCount = likeCount
        self.commentCount = commentCount
        self.isExternal = isExternal
        self.sizeBytes = sizeBytes
    }

    enum CodingKeys: String, CodingKey {
        case id
        case creatorHandle = "creator_handle"
        case altCreatorHandle = "creatorHandle"
        case creatorName = "creator_name"
        case altCreatorName = "creatorName"
        case caption
        case rank
        case rankDisplay = "rank_display"
        case altRankDisplay = "rankDisplay"
        case videoUrl = "video_url"
        case altVideoUrl = "videoUrl"
        case r2Url = "r2_url"
        case localUrl = "local_url"
        case thumbnailUrl = "thumbnail"
        case altThumbnailUrl = "thumbnail_url"
        case camelThumbnailUrl = "thumbnailUrl"
        case poster
        case category
        case viewCount = "view_count"
        case altViewCount = "viewCount"
        case likeCount = "like_count"
        case altLikeCount = "likeCount"
        case commentCount = "comment_count"
        case altCommentCount = "commentCount"
        case isExternal = "is_external"
        case altIsExternal = "isExternal"
        case sizeBytes = "sizeBytes"
        case altSizeBytes = "size_bytes"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)

        self.id = try container.decode(String.self, forKey: .id)

        let handle = try? container.decode(String.self, forKey: .creatorHandle)
        let altHandle = try? container.decode(String.self, forKey: .altCreatorHandle)
        self.creatorHandle = handle ?? altHandle ?? "creator"

        self.creatorName = (try? container.decode(String.self, forKey: .creatorName))
            ?? (try? container.decode(String.self, forKey: .altCreatorName))

        self.caption = (try? container.decode(String.self, forKey: .caption)) ?? ""

        self.rank = (try? container.decode(Int.self, forKey: .rank)) ?? 1

        let rankDisp = (try? container.decode(String.self, forKey: .rankDisplay))
            ?? (try? container.decode(String.self, forKey: .altRankDisplay))
        self.rankDisplay = rankDisp ?? String(format: "#%02d", self.rank)

        // Resolve video URL from multiple candidate keys
        var resolvedVideoURL: URL? = nil
        if let str = try? container.decode(String.self, forKey: .videoUrl), let url = URL(string: str) {
            resolvedVideoURL = url
        } else if let str = try? container.decode(String.self, forKey: .altVideoUrl), let url = URL(string: str) {
            resolvedVideoURL = url
        } else if let str = try? container.decode(String.self, forKey: .r2Url), let url = URL(string: str) {
            resolvedVideoURL = url
        } else if let str = try? container.decode(String.self, forKey: .localUrl), let url = URL(string: str) {
            resolvedVideoURL = url
        }

        guard let validVideoURL = resolvedVideoURL else {
            throw DecodingError.dataCorruptedError(
                forKey: .videoUrl,
                in: container,
                debugDescription: "Missing or invalid video URL for reel \(self.id)"
            )
        }
        self.videoUrl = validVideoURL

        // Resolve thumbnail URL
        var resolvedThumb: URL? = nil
        for key in [CodingKeys.thumbnailUrl, .altThumbnailUrl, .camelThumbnailUrl, .poster] {
            if let str = try? container.decode(String.self, forKey: key), let url = URL(string: str) {
                resolvedThumb = url
                break
            }
        }
        self.thumbnailUrl = resolvedThumb

        self.category = try? container.decode(String.self, forKey: .category)
        self.viewCount = (try? container.decode(Int.self, forKey: .viewCount))
            ?? (try? container.decode(Int.self, forKey: .altViewCount))
        self.likeCount = (try? container.decode(Int.self, forKey: .likeCount))
            ?? (try? container.decode(Int.self, forKey: .altLikeCount))
        self.commentCount = (try? container.decode(Int.self, forKey: .commentCount))
            ?? (try? container.decode(Int.self, forKey: .altCommentCount))
        self.isExternal = (try? container.decode(Bool.self, forKey: .isExternal))
            ?? (try? container.decode(Bool.self, forKey: .altIsExternal))
            ?? false

        self.sizeBytes = (try? container.decode(Int64.self, forKey: .sizeBytes))
            ?? (try? container.decode(Int64.self, forKey: .altSizeBytes))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(creatorHandle, forKey: .creatorHandle)
        try container.encodeIfPresent(creatorName, forKey: .creatorName)
        try container.encode(caption, forKey: .caption)
        try container.encode(rank, forKey: .rank)
        try container.encode(rankDisplay, forKey: .rankDisplay)
        try container.encode(videoUrl.absoluteString, forKey: .videoUrl)
        try container.encodeIfPresent(thumbnailUrl?.absoluteString, forKey: .thumbnailUrl)
        try container.encodeIfPresent(category, forKey: .category)
        try container.encodeIfPresent(viewCount, forKey: .viewCount)
        try container.encodeIfPresent(likeCount, forKey: .likeCount)
        try container.encodeIfPresent(commentCount, forKey: .commentCount)
        try container.encode(isExternal, forKey: .isExternal)
        try container.encodeIfPresent(sizeBytes, forKey: .sizeBytes)
    }
}
