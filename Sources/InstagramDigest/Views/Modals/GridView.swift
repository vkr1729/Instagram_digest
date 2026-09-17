import SwiftUI

/// 300-reel jump grid providing high-velocity discovery and Jump-to-N bulk watched marking.
public struct GridView: View {
    public let reels: [ReelItem]
    public let currentIndex: Int
    public let watchedReelIDs: Set<String>
    public var onSelectReel: (Int) -> Void
    @Environment(\.dismiss) private var dismiss

    private let columns = [
        GridItem(.flexible(), spacing: 2),
        GridItem(.flexible(), spacing: 2),
        GridItem(.flexible(), spacing: 2)
    ]

    public init(
        reels: [ReelItem],
        currentIndex: Int,
        watchedReelIDs: Set<String>,
        onSelectReel: @escaping (Int) -> Void
    ) {
        self.reels = reels
        self.currentIndex = currentIndex
        self.watchedReelIDs = watchedReelIDs
        self.onSelectReel = onSelectReel
    }

    public var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                ScrollView {
                    LazyVGrid(columns: columns, spacing: 2) {
                        ForEach(Array(reels.enumerated()), id: \.element.id) { index, reel in
                            let isWatched = watchedReelIDs.contains(reel.id)
                            let isCurrent = index == currentIndex

                            Button {
                                onSelectReel(index)
                                dismiss()
                            } label: {
                                ZStack(alignment: .bottomLeading) {
                                    // Thumbnail
                                    AsyncThumbnailView(url: reel.thumbnailUrl)
                                        .frame(height: 170)
                                        .clipped()
                                        .opacity(isWatched ? 0.6 : 1.0)

                                    // Gradient overlay
                                    LinearGradient(
                                        colors: [Color.clear, Color.black.opacity(0.8)],
                                        startPoint: .top,
                                        endPoint: .bottom
                                    )

                                    // Content overlays
                                    VStack(alignment: .leading, spacing: 2) {
                                        HStack {
                                            Text(reel.rankDisplay)
                                                .font(.system(size: 11, weight: .bold))
                                                .foregroundColor(.white)
                                                .padding(.horizontal, 6)
                                                .padding(.vertical, 2)
                                                .background(isCurrent ? Color.cyan : Color.black.opacity(0.6))
                                                .clipShape(Capsule())

                                            Spacer()

                                            if isWatched {
                                                Image(systemName: "checkmark.circle.fill")
                                                    .font(.system(size: 12))
                                                    .foregroundColor(.green)
                                            }
                                        }

                                        Spacer()

                                        Text("@\(reel.creatorHandle)")
                                            .font(.system(size: 11, weight: .semibold))
                                            .foregroundColor(.white)
                                            .lineLimit(1)
                                    }
                                    .padding(6)
                                }
                                .overlay(
                                    Rectangle()
                                        .stroke(isCurrent ? Color.cyan : Color.clear, lineWidth: 2)
                                )
                            }
                            .accessibilityIdentifier("GridReelItem_\(index)")
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
            .navigationTitle("All Reels (\(reels.count))")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(.white)
                    .accessibilityIdentifier("GridDoneButton")
                }
            }
        }
    }
}
