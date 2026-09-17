import SwiftUI
import SwiftData
import AVFoundation

/// Bookmarks sheet with 1.5 GB storage gauge, dedicated ephemeral modal player,
/// actor-routed file deletion, and per-row Keep Offline admission.
public struct BookmarksSheet: View {
    @Query(sort: \BookmarkItem.bookmarkedAt, order: .reverse) private var bookmarks: [BookmarkItem]
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var modelContext

    @State private var totalBytes: Int64 = 0
    @State private var isPurgingStorage: Bool = false
    @State private var errorMessage: String?
    @State private var selectedBookmarkForPlayback: BookmarkItem?

    public init() {}

    public var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                VStack(spacing: 0) {
                    // Storage Gauge Header
                    VStack(spacing: 8) {
                        HStack {
                            Label("Bookmark Storage", systemImage: "internaldrive.fill")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.white.opacity(0.8))
                            Spacer()
                            Text("\(formatMB(totalBytes)) / 1.5 GB")
                                .font(.system(size: 13, weight: .bold, design: .monospaced))
                                .foregroundColor(.cyan)
                                .accessibilityIdentifier("StorageGaugeLabel")
                        }

                        // Progress Gauge Bar
                        let fraction = min(1.0, Double(totalBytes) / Double(MediaCacheManager.maxBookmarkStorageBytes))
                        ProgressView(value: fraction)
                            .tint(fraction > 0.9 ? .orange : .cyan)

                        HStack {
                            Text("\(bookmarks.count) saved reels")
                                .font(.system(size: 11))
                                .foregroundColor(.white.opacity(0.5))
                                .accessibilityIdentifier("BookmarksCountLabel")
                            Spacer()

                            // Free Local Storage Action Button
                            Button {
                                performFreeStorage()
                            } label: {
                                if isPurgingStorage {
                                    ProgressView()
                                        .tint(.white)
                                } else {
                                    Text("Free Local Storage")
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundColor(.red.opacity(0.9))
                                }
                            }
                            .accessibilityIdentifier("FreeStorageButton")
                            .disabled(isPurgingStorage || totalBytes == 0)
                        }
                    }
                    .padding(16)
                    .background(Color.white.opacity(0.06))

                    // Error Alert Banner
                    if let err = errorMessage {
                        Text(err)
                            .font(.system(size: 12))
                            .foregroundColor(.red)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 6)
                            .frame(maxWidth: .infinity)
                            .background(Color.red.opacity(0.1))
                    }

                    // Bookmarks List
                    if bookmarks.isEmpty {
                        VStack(spacing: 12) {
                            Spacer()
                            Image(systemName: "bookmark")
                                .font(.system(size: 44))
                                .foregroundColor(.white.opacity(0.3))
                            Text("No saved bookmarks yet")
                                .font(.system(size: 16, weight: .medium))
                                .foregroundColor(.white.opacity(0.6))
                                .accessibilityIdentifier("BookmarksEmptyStateText")
                            Spacer()
                        }
                    } else {
                        List {
                            ForEach(bookmarks) { bookmark in
                                BookmarkRowView(
                                    bookmark: bookmark,
                                    onPlay: {
                                        selectedBookmarkForPlayback = bookmark
                                    },
                                    onKeepOffline: {
                                        Task {
                                            do {
                                                try await MediaCacheManager.shared.keepBookmarkOffline(
                                                    weekID: bookmark.weekID,
                                                    reelID: bookmark.reelID,
                                                    fallbackSizeBytes: bookmark.sizeBytes
                                                )
                                                do {
                                                    try modelContext.save()
                                                } catch {
                                                    modelContext.rollback()
                                                }
                                                await refreshLedger()
                                            } catch {
                                                errorMessage = error.localizedDescription
                                            }
                                        }
                                    }
                                )
                                .accessibilityIdentifier("BookmarkRow_\(bookmark.reelID)")
                                .listRowBackground(Color.clear)
                                .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
                            }
                            .onDelete(perform: deleteBookmarks)
                        }
                        .listStyle(.plain)
                    }
                }
            }
            .navigationTitle("Saved Bookmarks")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(.white)
                    .accessibilityIdentifier("BookmarksDoneButton")
                }
            }
            .sheet(item: $selectedBookmarkForPlayback) { bookmark in
                EphemeralPlayerSheet(bookmark: bookmark)
            }
            .task {
                await refreshLedger()
            }
        }
    }

    private func refreshLedger() async {
        totalBytes = await MediaCacheManager.shared.totalBookmarkBytes
    }

    private func performFreeStorage() {
        isPurgingStorage = true
        Task {
            await MediaCacheManager.shared.freeBookmarkStorage()
            await refreshLedger()
            isPurgingStorage = false
        }
    }

    private func deleteBookmarks(at offsets: IndexSet) {
        for index in offsets {
            let item = bookmarks[index]
            let reelID = item.reelID
            Task {
                await MediaCacheManager.shared.deleteBookmarkFile(reelID: reelID)
            }
            modelContext.delete(item)
        }
        do {
            try modelContext.save()
        } catch {
            modelContext.rollback()
        }
        Task {
            await refreshLedger()
        }
    }

    private func formatMB(_ bytes: Int64) -> String {
        String(format: "%.1f MB", Double(bytes) / 1_000_000)
    }
}

public struct BookmarkRowView: View {
    public let bookmark: BookmarkItem
    public var onPlay: () -> Void
    public var onKeepOffline: () -> Void

    public var body: some View {
        HStack(spacing: 12) {
            // Thumbnail
            AsyncThumbnailView(url: bookmark.thumbnailUrl)
                .frame(width: 54, height: 80)
                .clipShape(RoundedRectangle(cornerRadius: 8))

            // Metadata
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text("@\(bookmark.creatorHandle)")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.white)

                    Text(String(format: "#%02d", bookmark.rank))
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.white.opacity(0.6))
                }

                if !bookmark.caption.isEmpty {
                    Text(bookmark.caption)
                        .font(.system(size: 12))
                        .foregroundColor(.white.opacity(0.8))
                        .lineLimit(2)
                }

                // Status Badge: Cached vs Stream
                HStack(spacing: 8) {
                    if bookmark.localStatus == .cached {
                        Label("Offline", systemImage: "checkmark.circle.fill")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.green)
                    } else {
                        Label("Cloud Stream", systemImage: "icloud")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(.white.opacity(0.5))

                        Button {
                            onKeepOffline()
                        } label: {
                            Text("Keep Offline")
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(.cyan)
                        }
                    }
                }
                .padding(.top, 2)
            }

            Spacer()

            // Play Trigger Button
            Button {
                onPlay()
            } label: {
                Image(systemName: "play.circle.fill")
                    .font(.system(size: 28))
                    .foregroundColor(.white)
            }
        }
        .padding(10)
        .background(Color.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

/// Standalone Ephemeral Player Sheet (plays without hijacking or modifying AVPlayerPool feed playlist)
public struct EphemeralPlayerSheet: View {
    public let bookmark: BookmarkItem
    @Environment(\.dismiss) private var dismiss
    @State private var player: AVQueuePlayer?
    @State private var looper: AVPlayerLooper?

    public var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if let p = player {
                EphemeralVideoContainer(player: p)
                    .ignoresSafeArea()
            }

            VStack {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("@\(bookmark.creatorHandle)")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundColor(.white)
                        Text(String(format: "#%02d", bookmark.rank))
                            .font(.system(size: 12))
                            .foregroundColor(.white.opacity(0.7))
                    }
                    .padding(12)
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 12))

                    Spacer()

                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 28))
                            .foregroundColor(.white.opacity(0.8))
                    }
                }
                .padding(20)

                Spacer()
            }
        }
        .onAppear {
            AVPlayerPool.shared.pause()
            let mediaURL: URL
            let localURL = LibraryPathResolver.shared.bookmarkFileURL(for: bookmark.reelID)
            if FileManager.default.fileExists(atPath: localURL.path) {
                mediaURL = localURL
            } else if let remoteURL = bookmark.videoUrl {
                mediaURL = remoteURL
            } else {
                return
            }

            let asset = AVURLAsset(url: mediaURL)
            let item = AVPlayerItem(asset: asset)
            let queuePlayer = AVQueuePlayer()
            self.looper = AVPlayerLooper(player: queuePlayer, templateItem: item)
            self.player = queuePlayer
            queuePlayer.play()
        }
        .onDisappear {
            player?.pause()
            looper?.disableLooping()
            looper = nil
            player = nil
        }
    }
}

public struct EphemeralVideoContainer: UIViewRepresentable {
    public let player: AVQueuePlayer

    public func makeUIView(context: Context) -> PlayerContainerView {
        let view = PlayerContainerView()
        view.playerLayer.player = player
        return view
    }

    public func updateUIView(_ uiView: PlayerContainerView, context: Context) {
        uiView.playerLayer.player = player
    }
}
