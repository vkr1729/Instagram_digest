import SwiftUI

/// Category definition for the story circles bar.
public struct StoryCategoryItem: Identifiable, Sendable {
    public let id: String
    public let title: String
    public let emoji: String

    public init(id: String, title: String, emoji: String) {
        self.id = id
        self.title = title
        self.emoji = emoji
    }
}

/// Horizontal story circles bar strictly conforming to Mock 2 (Mobile PWA Standard).
/// Displays all 7 categories in a single edge-to-edge row without horizontal clipping.
/// Rings are 42px frosted slate (active = solid white, inactive = translucent)
/// with active dot indicators — zero neon gradients per quiet-luxury spec.
public struct StoryCategoryBarView: View {
    public let categories: [StoryCategoryItem]
    public let selectedCategoryId: String
    public var onSelectCategory: (String) -> Void
    /// Rec 4: "Offline · N downloaded" while the offline filter applies.
    public var offlineCaption: String = ""

    public static func standardCategories(totalCount: Int = 0) -> [StoryCategoryItem] {
        return [
            StoryCategoryItem(id: "all", title: "All", emoji: "🔥"),
            StoryCategoryItem(id: "entertainment", title: "Entertain", emoji: "🎬"),
            StoryCategoryItem(id: "finance", title: "Finance", emoji: "💰"),
            StoryCategoryItem(id: "ai_tech", title: "AI & Tech", emoji: "💻"),
            StoryCategoryItem(id: "niche", title: "Niche", emoji: "🧠"),
            StoryCategoryItem(id: "health", title: "Health", emoji: "🏋️"),
            StoryCategoryItem(id: "food", title: "Food", emoji: "🥗")
        ]
    }

    public init(
        totalCount: Int = 0,
        categories: [StoryCategoryItem]? = nil,
        selectedCategoryId: String = "all",
        offlineCaption: String = "",
        onSelectCategory: @escaping (String) -> Void = { _ in }
    ) {
        self.categories = categories ?? Self.standardCategories(totalCount: totalCount)
        self.selectedCategoryId = selectedCategoryId
        self.offlineCaption = offlineCaption
        self.onSelectCategory = onSelectCategory
    }

    public var body: some View {
        VStack(spacing: 3) {
            // Rec 4: offline chip — visible only while the offline filter
            // narrows the playlist to downloaded reels.
            if !offlineCaption.isEmpty {
                Text(offlineCaption)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.cyan)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 3)
                    .background(Color.cyan.opacity(0.15))
                    .clipShape(Capsule())
                    .accessibilityIdentifier("OfflinePlaylistChip")
            }

            // Compact horizontal pill row (low vertical profile, leaves creator face clear)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(categories) { cat in
                        let isSelected = cat.id == selectedCategoryId

                        Button {
                            onSelectCategory(cat.id)
                        } label: {
                            HStack(spacing: 5) {
                                Text(cat.emoji)
                                    .font(.system(size: 12))
                                Text(cat.title)
                                    .font(.system(size: 12, weight: isSelected ? .bold : .medium))
                                    .lineLimit(1)
                            }
                            .padding(.horizontal, 10)
                            .frame(height: 28)
                            .background(
                                isSelected
                                    ? Color.white
                                    : Color.white.opacity(0.12)
                            )
                            .foregroundColor(isSelected ? .black : .white)
                            .clipShape(Capsule())
                            .overlay(
                                Capsule().stroke(
                                    isSelected ? Color.clear : Color.white.opacity(0.2),
                                    lineWidth: 0.5
                                )
                            )
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("Category_\(cat.id)")
                        .accessibilityValue(isSelected ? "selected" : "unselected")
                    }
                }
                .padding(.horizontal, 14)
            }
        }
        .padding(.vertical, 3)
    }
}
