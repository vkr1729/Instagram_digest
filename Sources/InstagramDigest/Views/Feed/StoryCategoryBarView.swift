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
        let topTitle = totalCount > 0 ? "Top \(totalCount)" : "Top"
        return [
            StoryCategoryItem(id: "all", title: topTitle, emoji: "🔥"),
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
        VStack(spacing: 2) {
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
            HStack(alignment: .top, spacing: 0) {
            ForEach(categories) { cat in
                let isSelected = cat.id == selectedCategoryId

                Button {
                    onSelectCategory(cat.id)
                } label: {
                    VStack(spacing: 3) {
                        // 42px Story Ring Circle
                        ZStack {
                            if isSelected {
                                Circle()
                                    .stroke(Color.white, lineWidth: 2.0)
                                    .frame(width: 44, height: 44)
                            } else {
                                Circle()
                                    .stroke(Color.white.opacity(0.14), lineWidth: 1.2)
                                    .frame(width: 44, height: 44)
                            }

                            // Inner circle with Emoji
                            Circle()
                                .fill(Color(white: 0.14))
                                .frame(width: 38, height: 38)
                                .overlay(
                                    Text(cat.emoji)
                                        .font(.system(size: 19))
                                )
                        }

                        // Category Label (clean text, no count badges)
                        Text(cat.title)
                            .font(.system(size: 10, weight: isSelected ? .bold : .medium))
                            .foregroundColor(isSelected ? .white : .white.opacity(0.75))
                            .lineLimit(1)
                            .minimumScaleFactor(0.70)
                            .frame(maxWidth: .infinity)

                        // Active Selection Dot
                        if isSelected {
                            Circle()
                                .fill(Color.white)
                                .frame(width: 3.5, height: 3.5)
                        } else {
                            Circle()
                                .fill(Color.clear)
                                .frame(width: 3.5, height: 3.5)
                        }
                    }
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity)
                .accessibilityIdentifier("Category_\(cat.id)")
                .accessibilityValue(isSelected ? "selected" : "unselected")
            }
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(
            LinearGradient(
                colors: [Color.black.opacity(0.5), Color.black.opacity(0.0)],
                startPoint: .top,
                endPoint: .bottom
            )
        )
    }
}
