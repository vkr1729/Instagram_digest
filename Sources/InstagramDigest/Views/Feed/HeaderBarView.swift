import SwiftUI

/// Top navigation header bar strictly conforming to Mock 2 (Mobile PWA Standard).
/// Features cursive "Instagram" brand logo, frosted glass action pills:
/// Grid icon, Download icon, and Bookmarks chip with count badge.
/// The Jump-to-N pill was retired in favor of grid selection + last-reel resume.
/// Strictly excludes any desktop daily target badge.
public struct HeaderBarView: View {
    public let currentIndex: Int
    public let totalCount: Int
    public let bookmarkCount: Int
    public let watchHoursText: String
    public var onTapGrid: () -> Void
    public var onTapOffline: () -> Void
    public var onTapBookmarks: () -> Void
    public var onTapManifesto: () -> Void
    public var onTapSettings: () -> Void

    public init(
        currentIndex: Int,
        totalCount: Int,
        bookmarkCount: Int,
        watchHoursText: String = "0.0 hrs",
        onTapGrid: @escaping () -> Void = {},
        onTapOffline: @escaping () -> Void = {},
        onTapBookmarks: @escaping () -> Void = {},
        onTapManifesto: @escaping () -> Void = {},
        onTapSettings: @escaping () -> Void = {}
    ) {
        self.currentIndex = currentIndex
        self.totalCount = totalCount
        self.bookmarkCount = bookmarkCount
        self.watchHoursText = watchHoursText
        self.onTapGrid = onTapGrid
        self.onTapOffline = onTapOffline
        self.onTapBookmarks = onTapBookmarks
        self.onTapManifesto = onTapManifesto
        self.onTapSettings = onTapSettings
    }

    public var body: some View {
        HStack(alignment: .center, spacing: 8) {
            // 1. Cursive "Instagram" Brand Logo with Subtitle (fixed horizontal size to prevent wrapping)
            VStack(alignment: .leading, spacing: 0) {
                Text("Instagram")
                    .font(instagramLogoFont(size: 24))
                    .foregroundColor(.white)
                    .accessibilityIdentifier("InstagramLogoText")
                Text("The Anti-Doomscroll Briefing")
                    .font(.system(size: 8, weight: .semibold))
                    .foregroundColor(Color.white.opacity(0.6))
                    .lineLimit(1)
            }
            .fixedSize(horizontal: true, vertical: false)
            .layoutPriority(2)
            .padding(.leading, 4)

            Spacer(minLength: 6)

            // 2. Action Controls Group (Timer, Grid, Bookmarks, Settings)
            HStack(spacing: 6) {
                // Watch Timer Pill (⏱ X.X hrs)
                HStack(spacing: 4) {
                    Image(systemName: "stopwatch")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(Color.white.opacity(0.85))
                    Text(watchHoursText)
                        .font(.system(size: 12, weight: .bold, design: .rounded))
                        .foregroundColor(.white)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
                .padding(.horizontal, 8)
                .frame(height: 32)
                .background(Color.white.opacity(0.12))
                .clipShape(Capsule())
                .overlay(
                    Capsule().stroke(Color.white.opacity(0.2), lineWidth: 0.5)
                )
                .contentShape(Rectangle())
                .frame(minHeight: 44) // ≥44pt hit target per Apple HIG
                .accessibilityIdentifier("WatchTimerPill")

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
                }
                .contentShape(Rectangle())
                .frame(width: 44, height: 44) // ≥44pt hit target per Apple HIG (visual stays 32pt)
                .accessibilityIdentifier("GridIconButton")

                // Bookmarks Chip (🔖 [count]) with gold badge
                Button(action: onTapBookmarks) {
                    HStack(spacing: 4) {
                        Image(systemName: "bookmark.fill")
                            .font(.system(size: 11))
                            .foregroundColor(Color(red: 1.0, green: 0.78, blue: 0.28)) // Amber/gold
                        Text("\(bookmarkCount)")
                            .font(.system(size: 12, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                            .lineLimit(1)
                            .fixedSize(horizontal: true, vertical: false)
                    }
                    .padding(.horizontal, 10)
                    .frame(height: 32)
                    .background(Color(red: 0.95, green: 0.65, blue: 0.15).opacity(0.25))
                    .clipShape(Capsule())
                    .overlay(
                        Capsule().stroke(Color(red: 0.95, green: 0.65, blue: 0.15).opacity(0.6), lineWidth: 0.8)
                    )
                }
                .contentShape(Rectangle())
                .frame(minHeight: 44) // ≥44pt hit target per Apple HIG
                .layoutPriority(1)
                .accessibilityIdentifier("BookmarksChipButton")

                // Settings Button (⚙)
                Button(action: onTapSettings) {
                    Image(systemName: "gearshape")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(width: 32, height: 32)
                        .background(Color.white.opacity(0.12))
                        .clipShape(Circle())
                        .overlay(
                            Circle().stroke(Color.white.opacity(0.2), lineWidth: 0.5)
                        )
                }
                .contentShape(Rectangle())
                .frame(width: 44, height: 44) // ≥44pt hit target per Apple HIG (visual stays 32pt)
                .accessibilityIdentifier("SettingsIconButton")
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
