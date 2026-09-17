import SwiftUI
import AVFoundation

/// Full-bleed overlay displaying metadata pills, progress bar, seek HUD, and bookmark pop.
public struct ReelCardOverlayView: View {
    public let reel: ReelItem
    public let isLatched2x: Bool
    public let isBookmarked: Bool
    public let seekFractionPreview: Double?
    public let progress: Double
    public let duration: Double
    public let currentTime: Double
    public let showBookmarkPop: Bool

    public init(
        reel: ReelItem,
        isLatched2x: Bool = false,
        isBookmarked: Bool = false,
        seekFractionPreview: Double? = nil,
        progress: Double = 0.0,
        duration: Double = 0.0,
        currentTime: Double = 0.0,
        showBookmarkPop: Bool = false
    ) {
        self.reel = reel
        self.isLatched2x = isLatched2x
        self.isBookmarked = isBookmarked
        self.seekFractionPreview = seekFractionPreview
        self.progress = progress
        self.duration = duration
        self.currentTime = currentTime
        self.showBookmarkPop = showBookmarkPop
    }

    public var body: some View {
        ZStack {
            // 1. Subtle Gradient Overlays for Readability
            VStack {
                LinearGradient(
                    colors: [Color.black.opacity(0.65), Color.clear],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .frame(height: 140)

                Spacer()

                LinearGradient(
                    colors: [Color.clear, Color.black.opacity(0.85)],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .frame(height: 240)
            }
            .ignoresSafeArea()
            .allowsHitTesting(false)

            // 2. Animated Bookmark Pop Indicator (Center)
            if showBookmarkPop {
                VStack(spacing: 8) {
                    Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark.slash.fill")
                        .font(.system(size: 48))
                        .foregroundColor(.yellow)
                    Text(isBookmarked ? "Saved to Bookmarks" : "Removed from Bookmarks")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.white)
                }
                .padding(24)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 20))
                .transition(.scale.combined(with: .opacity))
                .zIndex(20)
                .allowsHitTesting(false)
            }

            // 3. Metadata Overlays
            VStack(alignment: .leading, spacing: 0) {
                // Top Header: Creator Handle, Rank, and 2.0x Latch Pill
                HStack(alignment: .center, spacing: 8) {
                    // Creator Handle Pill
                    HStack(spacing: 6) {
                        Image(systemName: "person.crop.circle.fill")
                            .font(.system(size: 16))
                            .foregroundColor(.white.opacity(0.9))
                        Text("@\(reel.creatorHandle)")
                            .font(.system(size: 15, weight: .bold))
                            .foregroundColor(.white)
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(.ultraThinMaterial.opacity(0.8))
                    .clipShape(Capsule())
                    .accessibilityIdentifier("ReelCreatorHandle")

                    // Rank Badge
                    Text(reel.rankDisplay)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Color.white.opacity(0.2))
                        .clipShape(Capsule())
                        .accessibilityIdentifier("ReelRankBadge")

                    // Discovery Pill for external reels
                    if reel.isExternal {
                        Text("Discovery")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(.yellow)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Color.yellow.opacity(0.2))
                            .clipShape(Capsule())
                    }

                    Spacer()

                    // Latched 2.0x Badge
                    if isLatched2x {
                        HStack(spacing: 4) {
                            Image(systemName: "bolt.fill")
                                .font(.system(size: 12))
                            Text("2.0x")
                                .font(.system(size: 13, weight: .heavy))
                        }
                        .foregroundColor(.yellow)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Color.black.opacity(0.6))
                        .clipShape(Capsule())
                        .overlay(Capsule().stroke(Color.yellow.opacity(0.8), lineWidth: 1))
                        .accessibilityIdentifier("Latched2xBadge")
                    }

                    // Bookmark indicator
                    if isBookmarked {
                        Image(systemName: "bookmark.fill")
                            .font(.system(size: 15))
                            .foregroundColor(.yellow)
                            .padding(8)
                            .background(.ultraThinMaterial)
                            .clipShape(Circle())
                            .accessibilityIdentifier("BookmarkIndicator")
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 56)
                .allowsHitTesting(false)

                Spacer()

                // Bottom Content: Category and Metrics Metadata
                HStack(spacing: 12) {
                    if let cat = reel.category {
                        Text(cat.uppercased())
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(.white.opacity(0.7))
                    }
                    if let views = reel.viewCount, views > 0 {
                        Text("\(formatNumber(views)) views")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(.white.opacity(0.6))
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
                .allowsHitTesting(false)

                // 4. Seek HUD Preview (when user is scrubbing horizontally)
                if let preview = seekFractionPreview, duration > 0 {
                    let targetSec = preview * duration
                    HStack {
                        Spacer()
                        VStack(spacing: 4) {
                            Text(formatTime(targetSec))
                                .font(.system(size: 20, weight: .heavy, design: .monospaced))
                                .foregroundColor(.white)
                            Text("/ \(formatTime(duration))")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.white.opacity(0.7))
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(.ultraThinMaterial)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        Spacer()
                    }
                    .padding(.bottom, 12)
                    .allowsHitTesting(false)
                }

                // 5. Scrubbing Progress Bar
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        // Background track
                        Rectangle()
                            .fill(Color.white.opacity(0.25))
                            .frame(height: 3)

                        // Progress fill
                        let displayFraction = seekFractionPreview ?? progress
                        Rectangle()
                            .fill(seekFractionPreview != nil ? Color.yellow : Color.white)
                            .frame(width: max(0, min(geo.size.width, geo.size.width * CGFloat(displayFraction))), height: 3)
                    }
                }
                .frame(height: 3)
                .padding(.bottom, 8)
                .allowsHitTesting(false)
            }
        }
    }

    private func formatTime(_ seconds: Double) -> String {
        guard seconds.isFinite && !seconds.isNaN else { return "00:00" }
        let total = Int(seconds)
        let mins = total / 60
        let secs = total % 60
        return String(format: "%02d:%02d", mins, secs)
    }

    private func formatNumber(_ num: Int) -> String {
        if num >= 1_000_000 {
            return String(format: "%.1fM", Double(num) / 1_000_000)
        } else if num >= 1_000 {
            return String(format: "%.1fK", Double(num) / 1_000)
        }
        return "\(num)"
    }
}

/// Standalone PlayerLayerView for SwiftUI usage if needed
public struct PlayerLayerView: UIViewRepresentable {
    public let slotIndex: Int

    public init(slotIndex: Int) {
        self.slotIndex = slotIndex
    }

    public func makeUIView(context: Context) -> PlayerContainerView {
        let view = PlayerContainerView()
        AVPlayerPool.shared.attachLayer(view.playerLayer, forSlotIndex: slotIndex)
        return view
    }

    public func updateUIView(_ uiView: PlayerContainerView, context: Context) {
        AVPlayerPool.shared.attachLayer(uiView.playerLayer, forSlotIndex: slotIndex)
    }

    public static func dismantleUIView(_ uiView: PlayerContainerView, coordinator: ()) {
        uiView.playerLayer.player = nil
    }
}

public final class PlayerContainerView: UIView {
    public override static var layerClass: AnyClass {
        AVPlayerLayer.self
    }

    public var playerLayer: AVPlayerLayer {
        layer as! AVPlayerLayer
    }

    public override init(frame: CGRect) {
        super.init(frame: frame)
        playerLayer.videoGravity = .resizeAspectFill
        backgroundColor = .black
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        playerLayer.videoGravity = .resizeAspectFill
        backgroundColor = .black
    }
}

/// Simple cached async image thumbnail view
public struct AsyncThumbnailView: View {
    public let url: URL?

    public init(url: URL?) {
        self.url = url
    }

    public var body: some View {
        if let imageURL = url {
            AsyncImage(url: imageURL) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                default:
                    Color.black
                }
            }
        } else {
            Color.black
        }
    }
}
