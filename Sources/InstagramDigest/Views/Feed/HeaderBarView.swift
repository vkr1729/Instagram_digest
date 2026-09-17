import SwiftUI

/// Top navigation header bar strictly conforming to Mock 2 (Mobile PWA Standard).
/// Features cursive "Instagram" brand logo, frosted glass action pills:
/// Jump-to-N pill, Grid icon, Download icon, and Bookmarks chip with count badge.
/// Strictly excludes any desktop daily target badge.
public struct HeaderBarView: View {
    public let currentIndex: Int
    public let totalCount: Int
    public let bookmarkCount: Int
    public var onTapJump: () -> Void
    public var onTapGrid: () -> Void
    public var onTapOffline: () -> Void
    public var onTapBookmarks: () -> Void

    public init(
        currentIndex: Int,
        totalCount: Int,
        bookmarkCount: Int,
        onTapJump: @escaping () -> Void = {},
        onTapGrid: @escaping () -> Void = {},
        onTapOffline: @escaping () -> Void = {},
        onTapBookmarks: @escaping () -> Void = {}
    ) {
        self.currentIndex = currentIndex
        self.totalCount = totalCount
        self.bookmarkCount = bookmarkCount
        self.onTapJump = onTapJump
        self.onTapGrid = onTapGrid
        self.onTapOffline = onTapOffline
        self.onTapBookmarks = onTapBookmarks
    }

    public var body: some View {
        HStack(alignment: .center, spacing: 8) {
            // 1. Cursive "Instagram" Brand Logo
            Text("Instagram")
                .font(instagramLogoFont(size: 27))
                .foregroundColor(.white)
                .padding(.leading, 4)
                .accessibilityIdentifier("InstagramLogoText")

            Spacer()

            // 2. Action Controls Group
            HStack(spacing: 6) {
                // Jump Pill (# N / total, counts fully dynamic)
                Button(action: onTapJump) {
                    HStack(spacing: 3) {
                        Text(String(format: "# %d / %d", currentIndex + 1, totalCount))
                            .font(.system(size: 11, weight: .bold, design: .monospaced))
                            .foregroundColor(.white)
                    }
                    .padding(.horizontal, 9)
                    .padding(.vertical, 6)
                    .background(Color.white.opacity(0.12))
                    .clipShape(Capsule())
                    .overlay(
                        Capsule().stroke(Color.white.opacity(0.2), lineWidth: 0.5)
                    )
                }
                .frame(minHeight: 44) // ≥44pt hit target per Apple HIG
                .accessibilityIdentifier("JumpPillButton")

                // Grid View Button (⊞)
                Button(action: onTapGrid) {
                    Image(systemName: "square.grid.3x3.fill")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(width: 32, height: 32)
                        .background(Color.white.opacity(0.12))
                        .clipShape(Circle())
                        .overlay(
                            Circle().stroke(Color.white.opacity(0.2), lineWidth: 0.5)
                        )
                        .frame(width: 44, height: 44) // ≥44pt hit target per Apple HIG (visual stays 32pt)
                }
                .accessibilityIdentifier("GridIconButton")

                // Download All / Offline Button (📥)
                Button(action: onTapOffline) {
                    Image(systemName: "arrow.down.circle.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(width: 32, height: 32)
                        .background(Color.white.opacity(0.12))
                        .clipShape(Circle())
                        .overlay(
                            Circle().stroke(Color.white.opacity(0.2), lineWidth: 0.5)
                        )
                        .frame(width: 44, height: 44) // ≥44pt hit target per Apple HIG (visual stays 32pt)
                }
                .accessibilityIdentifier("OfflineIconButton")

                // Bookmarks Chip (🔖 18) with gold badge
                Button(action: onTapBookmarks) {
                    HStack(spacing: 4) {
                        Image(systemName: "bookmark.fill")
                            .font(.system(size: 11))
                            .foregroundColor(Color(red: 1.0, green: 0.78, blue: 0.28)) // Amber/gold
                        Text("\(bookmarkCount)")
                            .font(.system(size: 12, weight: .heavy))
                            .foregroundColor(.white)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color(red: 0.95, green: 0.65, blue: 0.15).opacity(0.22))
                    .clipShape(Capsule())
                    .overlay(
                        Capsule().stroke(Color(red: 0.95, green: 0.65, blue: 0.15).opacity(0.6), lineWidth: 0.8)
                    )
                }
                .frame(minHeight: 44) // ≥44pt hit target per Apple HIG
                .accessibilityIdentifier("BookmarksChipButton")
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 4)
        .padding(.bottom, 6)
        .background(
            LinearGradient(
                colors: [Color.black.opacity(0.75), Color.black.opacity(0.0)],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea(edges: .top)
        )
    }

    private func instagramLogoFont(size: CGFloat) -> Font {
        if UIFont(name: "GrandHotel-Regular", size: size) != nil {
            return .custom("GrandHotel-Regular", size: size)
        } else if UIFont(name: "Grand Hotel", size: size) != nil {
            return .custom("Grand Hotel", size: size)
        } else {
            return .system(size: size, weight: .bold, design: .serif).italic()
        }
    }
}
