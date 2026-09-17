import SwiftUI
import AVFoundation

/// Full-bleed overlay strictly conforming to Mock 2 (Mobile PWA Standard).
/// Features soft natural gradient scrim (rgba(0,0,0,0.45) down to transparent),
/// bottom metadata HUD with @creatorHandle, orange #rank badge, interactive WhatsApp Share
/// and Gold Bookmark buttons, 2-line expandable caption, and hairline progress bar.
public struct ReelCardOverlayView: View {
    public let reel: ReelItem
    public let isLatched2x: Bool
    public let isBookmarked: Bool
    public let seekFractionPreview: Double?
    public let progress: Double
    public let duration: Double
    public let currentTime: Double
    public let showBookmarkPop: Bool
    public let isCaptionExpanded: Bool
    public var onTogglePlayPause: () -> Void
    public var onTriggerBookmark: () -> Void
    public var onTriggerShare: () -> Void
    public var onToggleCaption: () -> Void

    public init(
        reel: ReelItem,
        isLatched2x: Bool = false,
        isBookmarked: Bool = false,
        seekFractionPreview: Double? = nil,
        progress: Double = 0.0,
        duration: Double = 0.0,
        currentTime: Double = 0.0,
        showBookmarkPop: Bool = false,
        isCaptionExpanded: Bool = false,
        onTogglePlayPause: @escaping () -> Void = {},
        onTriggerBookmark: @escaping () -> Void = {},
        onTriggerShare: @escaping () -> Void = {},
        onToggleCaption: @escaping () -> Void = {}
    ) {
        self.reel = reel
        self.isLatched2x = isLatched2x
        self.isBookmarked = isBookmarked
        self.seekFractionPreview = seekFractionPreview
        self.progress = progress
        self.duration = duration
        self.currentTime = currentTime
        self.showBookmarkPop = showBookmarkPop
        self.isCaptionExpanded = isCaptionExpanded
        self.onTogglePlayPause = onTogglePlayPause
        self.onTriggerBookmark = onTriggerBookmark
        self.onTriggerShare = onTriggerShare
        self.onToggleCaption = onToggleCaption
    }

    public var body: some View {
        ZStack {
            // 1. Soft Natural Gradient Scrim at Bottom (Mock 2: rgba(0,0,0,0.45) -> transparent)
            VStack {
                Spacer()
                LinearGradient(
                    colors: [Color.clear, Color.black.opacity(0.48), Color.black.opacity(0.85)],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .frame(height: 180)
            }
            .ignoresSafeArea()
            .allowsHitTesting(false)

            // 2. Animated Bookmark Pop Indicator (Center)
            if showBookmarkPop {
                VStack(spacing: 8) {
                    Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark.slash.fill")
                        .font(.system(size: 44))
                        .foregroundColor(Color(red: 1.0, green: 0.8, blue: 0.25))
                    Text(isBookmarked ? "Saved to Bookmarks" : "Removed from Bookmarks")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.white)
                }
                .padding(22)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 18))
                .accessibilityIdentifier("BookmarkIndicator")
                .transition(.scale.combined(with: .opacity))
                .zIndex(20)
                .allowsHitTesting(false)
            }

            // 3. Latched 2.0x Indicator (Upper Right)
            if isLatched2x {
                VStack {
                    HStack {
                        Spacer()
                        HStack(spacing: 4) {
                            Image(systemName: "bolt.fill")
                                .font(.system(size: 12))
                            Text("2.0x")
                                .font(.system(size: 13, weight: .heavy))
                        }
                        .foregroundColor(.yellow)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Color.black.opacity(0.65))
                        .clipShape(Capsule())
                        .overlay(Capsule().stroke(Color.yellow.opacity(0.8), lineWidth: 1))
                        .accessibilityIdentifier("Latched2xBadge")
                    }
                    .padding(.trailing, 16)
                    .padding(.top, 100)
                    Spacer()
                }
                .allowsHitTesting(false)
            }

            // 4. Seek HUD Preview (Center while scrubbing horizontally)
            if let preview = seekFractionPreview, duration > 0 {
                let targetSec = preview * duration
                VStack {
                    Spacer()
                    VStack(spacing: 4) {
                        Text(formatTime(targetSec))
                            .font(.system(size: 24, weight: .heavy, design: .monospaced))
                            .foregroundColor(.white)
                        Text("/ \(formatTime(duration))")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(.white.opacity(0.7))
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    Spacer()
                }
                .allowsHitTesting(false)
            }

            // 5. Bottom Metadata HUD
            VStack(alignment: .leading, spacing: 0) {
                Spacer()

                // Hairline Progress Bar
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Rectangle()
                            .fill(Color.white.opacity(0.25))
                            .frame(height: 2.5)

                        let displayFraction = seekFractionPreview ?? progress
                        Rectangle()
                            .fill(seekFractionPreview != nil ? Color.yellow : Color.white)
                            .frame(width: max(0, min(geo.size.width, geo.size.width * CGFloat(displayFraction))), height: 2.5)
                    }
                }
                .frame(height: 2.5)
                .padding(.bottom, 10)
                .allowsHitTesting(false)

                // Creator & Action Row (Mock 2: @handle, #rank, Share, Save)
                HStack(alignment: .center, spacing: 8) {
                    // Creator Handle
                    Text("@\(reel.creatorHandle)")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundColor(.white)
                        .accessibilityIdentifier("ReelCreatorHandle")

                    // #rank Badge (Orange)
                    Text(reel.rankDisplay)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(Color(red: 0.95, green: 0.60, blue: 0.20))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(Color(red: 0.95, green: 0.60, blue: 0.20).opacity(0.22))
                        .clipShape(Capsule())
                        .overlay(
                            Capsule().stroke(Color(red: 0.95, green: 0.60, blue: 0.20).opacity(0.6), lineWidth: 0.8)
                        )
                        .accessibilityIdentifier("ReelRankBadge")

                    Spacer()

                    // WhatsApp Share Button (🟢 Share)
                    Button(action: onTriggerShare) {
                        HStack(spacing: 4) {
                            Image(systemName: "arrowshape.turn.up.right.fill")
                                .font(.system(size: 11, weight: .bold))
                            Text("Share")
                                .font(.system(size: 12, weight: .bold))
                        }
                        .foregroundColor(Color(red: 0.25, green: 0.92, blue: 0.45))
                        .padding(.horizontal, 11)
                        .padding(.vertical, 7)
                        .background(Color(red: 0.15, green: 0.82, blue: 0.40).opacity(0.22))
                        .clipShape(Capsule())
                        .overlay(
                            Capsule().stroke(Color(red: 0.25, green: 0.92, blue: 0.45).opacity(0.6), lineWidth: 0.8)
                        )
                    }
                    .frame(minHeight: 44) // ≥44pt hit target per Apple HIG
                    .accessibilityIdentifier("WhatsAppShareButton")

                    // Bookmark Button (🔖 Save / 🔖 Saved)
                    Button(action: onTriggerBookmark) {
                        HStack(spacing: 4) {
                            Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                                .font(.system(size: 11, weight: .bold))
                            Text(isBookmarked ? "Saved" : "Save")
                                .font(.system(size: 12, weight: .bold))
                        }
                        .foregroundColor(Color(red: 1.0, green: 0.80, blue: 0.28))
                        .padding(.horizontal, 11)
                        .padding(.vertical, 7)
                        .background(Color(red: 0.95, green: 0.75, blue: 0.20).opacity(isBookmarked ? 0.38 : 0.18))
                        .clipShape(Capsule())
                        .overlay(
                            Capsule().stroke(Color(red: 1.0, green: 0.80, blue: 0.28).opacity(0.6), lineWidth: 0.8)
                        )
                    }
                    .frame(minHeight: 44) // ≥44pt hit target per Apple HIG
                    .accessibilityIdentifier("SaveBookmarkButton")
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 6)

                // Caption Snippet (2 lines, tap to expand)
                if !reel.caption.isEmpty {
                    Text(reel.caption)
                        .font(.system(size: 13))
                        .foregroundColor(.white.opacity(0.92))
                        .lineLimit(isCaptionExpanded ? 8 : 2)
                        .multilineTextAlignment(.leading)
                        .padding(.horizontal, 14)
                        .padding(.bottom, 12)
                        .onTapGesture(perform: onToggleCaption)
                        .accessibilityIdentifier("ReelCaptionText")
                }
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
        playerLayer.videoGravity = .resizeAspect
        backgroundColor = .black
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        playerLayer.videoGravity = .resizeAspect
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
