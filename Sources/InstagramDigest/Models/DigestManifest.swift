import Foundation

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
        if var itemsContainer = try? container.nestedUnkeyedContainer(forKey: .items) {
            while !itemsContainer.isAtEnd {
                if let reel = try? itemsContainer.decode(ReelItem.self) {
                    decodedItems.append(reel)
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
