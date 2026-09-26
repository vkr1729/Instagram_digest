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
/// Rings are 42px with conic-gradient borders and active dot indicators.
public struct StoryCategoryBarView: View {
    public let categories: [StoryCategoryItem]
    public let selectedCategoryId: String
    public var onSelectCategory: (String) -> Void

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
        onSelectCategory: @escaping (String) -> Void = { _ in }
    ) {
        self.categories = categories ?? Self.standardCategories(totalCount: totalCount)
        self.selectedCategoryId = selectedCategoryId
        self.onSelectCategory = onSelectCategory
    }

    private let ringGradient = AngularGradient(
        gradient: Gradient(colors: [
            Color(red: 0.95, green: 0.60, blue: 0.20), // #f09433
            Color(red: 0.88, green: 0.15, blue: 0.28), // #dc2743
            Color(red: 0.74, green: 0.10, blue: 0.55), // #bc1888
            Color(red: 0.95, green: 0.60, blue: 0.20)
        ]),
        center: .center
    )

    public var body: some View {
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
                                    .stroke(ringGradient, lineWidth: 2.2)
                                    .frame(width: 44, height: 44)
                                    .shadow(color: Color(red: 0.9, green: 0.2, blue: 0.4).opacity(0.55), radius: 4)
                            } else {
                                Circle()
                                    .stroke(Color.white.opacity(0.35), lineWidth: 1.2)
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
