import SwiftUI
import SwiftData
import AVFoundation

/// Bookmarks sheet displaying saved reels in a 3-column Grid format (matching All Reels grid),
/// with 1.5 GB storage gauge, and dedicated sequential BookmarkPlayerOverlay that auto-advances,
/// has an (x) close button, channel/caption/unsave HUD, and auto-hiding chrome.
public struct BookmarksSheet: View {
    @Query(sort: \BookmarkItem.bookmarkedAt, order: .reverse) private var bookmarks: [BookmarkItem]
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var modelContext

    @State private var totalBytes: Int64 = 0
    @State private var isPurgingStorage: Bool = false
    @State private var errorMessage: String?
    @State private var activePlaybackIndex: Int? = nil
    @State private var showOwnerKeyPrompt: Bool = false
    @State private var ownerKeyInput: String = ""
    @AppStorage("digest_owner_key") private var storedOwnerKey: String = ""

    private var hasOwnerKey: Bool {
        !storedOwnerKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private let columns = [
        GridItem(.flexible(), spacing: 2),
        GridItem(.flexible(), spacing: 2),
        GridItem(.flexible(), spacing: 2)
    ]

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

                        HStack(spacing: 12) {
                            Text("\(bookmarks.count) saved reels")
                                .font(.system(size: 11))
                                .foregroundColor(.white.opacity(0.5))
                                .accessibilityIdentifier("BookmarksCountLabel")
                            Spacer()

                            // Owner Key Link Button
                            Button {
                                ownerKeyInput = storedOwnerKey
                                showOwnerKeyPrompt = true
                            } label: {
                                HStack(spacing: 3) {
                                    Image(systemName: hasOwnerKey ? "key.fill" : "key")
                                    Text(hasOwnerKey ? "Key Linked" : "Link Key")
                                }
                                .font(.system(size: 11, weight: .bold))
                                .foregroundColor(hasOwnerKey ? .yellow : .cyan)
                            }
                            .accessibilityIdentifier("LinkOwnerKeyButton")

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

                    // Bookmarks Grid (3-column layout matching All Reels Grid)
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
                        ScrollView {
                            LazyVGrid(columns: columns, spacing: 2) {
                                ForEach(Array(bookmarks.enumerated()), id: \.element.reelID) { index, bookmark in
                                    Button {
                                        activePlaybackIndex = index
                                    } label: {
                                        ZStack(alignment: .bottomLeading) {
                                            AsyncThumbnailView(url: bookmark.thumbnailUrl)
                                                .frame(height: 170)
                                                .clipped()

                                            LinearGradient(
                                                colors: [Color.clear, Color.black.opacity(0.8)],
                                                startPoint: .top,
                                                endPoint: .bottom
                                            )

                                            VStack(alignment: .leading, spacing: 2) {
                                                HStack {
                                                    Spacer()

                                                    if bookmark.localStatus == .cached {
                                                        Image(systemName: "checkmark.circle.fill")
                                                            .font(.system(size: 12))
                                                            .foregroundColor(.green)
                                                    }
                                                }

                                                Spacer()

                                                Text("@\(bookmark.creatorHandle)")
                                                    .font(.system(size: 11, weight: .semibold))
                                                    .foregroundColor(.white)
                                                    .lineLimit(1)
                                            }
                                            .padding(6)
                                        }
                                    }
                                    .accessibilityIdentifier("BookmarkGridItem_\(index)")
                                }
                            }
                            .padding(.vertical, 2)
                        }
                        .scrollBounceBehavior(.always)
                        .accessibilityIdentifier("BookmarksGrid")
                    }
                }

                // Dedicated Sequential Bookmark Player Overlay.
                // Gated on non-empty (not on idx < count) so an unsave that
                // shrinks the grid can't yank the player mid-playback; the
                // overlay clamps its own index via onChange below.
                if let idx = activePlaybackIndex, idx >= 0, !bookmarks.isEmpty {
                    BookmarkPlayerOverlay(
                        bookmarks: bookmarks,
                        initialIndex: min(idx, bookmarks.count - 1),
                        onClose: {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                activePlaybackIndex = nil
                            }
                        },
                        onDeleteBookmark: { item in
                            let countBefore = bookmarks.count
                            deleteBookmark(item)
                            if countBefore <= 1 {
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    activePlaybackIndex = nil
                                }
                            }
                        }
                    )
                    .ignoresSafeArea()
                    .transition(.opacity)
                    .zIndex(20)
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
            .task {
                await refreshLedger()
            }
            .alert("Cloudflare Owner Key", isPresented: $showOwnerKeyPrompt) {
                TextField("Owner Key", text: $ownerKeyInput)
                Button("Save") {
                    let trimmed = ownerKeyInput.trimmingCharacters(in: .whitespacesAndNewlines)
                    storedOwnerKey = trimmed
                    UserDefaults.standard.set(trimmed, forKey: "digest_owner_key")
                }
                if hasOwnerKey {
                    Button("Unlink Key", role: .destructive) {
                        storedOwnerKey = ""
                        UserDefaults.standard.removeObject(forKey: "digest_owner_key")
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Enter the OWNER_KEY secret configured in your Cloudflare Worker to enable Telegram sync and R2 bookmarks.")
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

    private func deleteBookmark(_ item: BookmarkItem) {
        let reelID = item.reelID
        Task {
            await MediaCacheManager.shared.deleteBookmarkFile(reelID: reelID)
            _ = try? await DigestDataService.shared.deleteRemoteBookmark(reelID: reelID)
        }
        modelContext.delete(item)
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

/// Standalone Sequential Bookmark Player Overlay
/// Plays bookmarks in order, auto-advances on end, has (x) close button at top,
/// channel name, caption, unsave bookmark, and auto-hides chrome during playback.
public struct BookmarkPlayerOverlay: View {
    public let bookmarks: [BookmarkItem]
    public let initialIndex: Int
    public var onClose: () -> Void
    public var onDeleteBookmark: (BookmarkItem) -> Void

    @State private var currentIndex: Int
    @State private var scrolledReelID: String?
    @State private var player: AVPlayer?
    @State private var isPlaying: Bool = true
    @State private var isChromeVisible: Bool = true
    @State private var isCaptionExpanded: Bool = false
    @State private var hideChromeWorkItem: DispatchWorkItem?
    @State private var endObserverToken: NSObjectProtocol?

    public init(
        bookmarks: [BookmarkItem],
        initialIndex: Int,
        onClose: @escaping () -> Void,
        onDeleteBookmark: @escaping (BookmarkItem) -> Void
    ) {
        self.bookmarks = bookmarks
        self.initialIndex = initialIndex
        self._currentIndex = State(initialValue: initialIndex)
        let initialID = (initialIndex >= 0 && initialIndex < bookmarks.count) ? bookmarks[initialIndex].reelID : nil
        self._scrolledReelID = State(initialValue: initialID)
        self.onClose = onClose
        self.onDeleteBookmark = onDeleteBookmark
    }

    private var currentBookmark: BookmarkItem? {
        if let id = scrolledReelID, let match = bookmarks.first(where: { $0.reelID == id }) {
            return match
        }
        guard !bookmarks.isEmpty, currentIndex >= 0, currentIndex < bookmarks.count else { return nil }
        return bookmarks[currentIndex]
    }

    public var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            // Vertical Paging Pager (iOS 17 native)
            ScrollView(.vertical, showsIndicators: false) {
                LazyVStack(spacing: 0) {
                    ForEach(Array(bookmarks.enumerated()), id: \.element.reelID) { index, bookmark in
                        ZStack {
                            Color.black
                            if (bookmark.reelID == scrolledReelID || (scrolledReelID == nil && index == currentIndex)), let p = player {
                                BookmarkVideoContainer(player: p)
                            }
                        }
                        .containerRelativeFrame([.horizontal, .vertical])
                        .id(bookmark.reelID)
                        .contentShape(Rectangle())
                        .onTapGesture {
                            togglePlayPause()
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.paging)
            .scrollPosition(id: $scrolledReelID)
            .ignoresSafeArea()
            .onChange(of: scrolledReelID) { _, newID in
                guard let newID = newID,
                      let idx = bookmarks.firstIndex(where: { $0.reelID == newID }),
                      idx != currentIndex else { return }
                currentIndex = idx
                isCaptionExpanded = false
                if let bm = currentBookmark {
                    loadVideo(for: bm)
                }
            }

            // Natural background scrim for chrome readability
            if isChromeVisible {
                LinearGradient(
                    colors: [Color.black.opacity(0.6), Color.clear, Color.black.opacity(0.8)],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .ignoresSafeArea()
                .allowsHitTesting(false)
            }

            // Chrome Overlays
            if let bookmark = currentBookmark {
                VStack {
                    // Top Bar with (x) Close Button
                    HStack {
                        Spacer()

                        Button {
                            player?.pause()
                            onClose()
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 32))
                                .foregroundColor(.white.opacity(0.9))
                                .background(Circle().fill(Color.black.opacity(0.4)))
                        }
                        .contentShape(Rectangle())
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityIdentifier("BookmarkPlayerCloseButton")
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 56)

                    Spacer()

                    // Bottom HUD (Channel, Caption, Unsave, Share)
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 8) {
                            Text("@\(bookmark.creatorHandle)")
                                .font(.system(size: 16, weight: .bold))
                                .foregroundColor(.white)

                            Spacer()

                            // Bookmark Toggle (Clicking removes it from bookmarks!)
                            Button {
                                handleUnsave(bookmark: bookmark)
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: "bookmark.fill")
                                        .font(.system(size: 12))
                                        .foregroundColor(Color(red: 1.0, green: 0.78, blue: 0.28))
                                    Text("Saved")
                                        .font(.system(size: 12, weight: .bold))
                                        .foregroundColor(.white)
                                }
                                .padding(.horizontal, 10)
                                .frame(height: 32)
                                .background(Color(red: 0.95, green: 0.65, blue: 0.15).opacity(0.28))
                                .clipShape(Capsule())
                                .overlay(
                                    Capsule().stroke(Color(red: 0.95, green: 0.65, blue: 0.15).opacity(0.6), lineWidth: 0.8)
                                )
                            }
                            .contentShape(Rectangle())
                            .frame(minHeight: 44)
                            .accessibilityIdentifier("BookmarkPlayerUnsaveButton")

                            // Share Button
                            Button {
                                triggerShare(bookmark: bookmark)
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: "arrowshape.turn.up.right.fill")
                                        .font(.system(size: 12))
                                    Text("Share")
                                        .font(.system(size: 12, weight: .bold))
                                }
                                .foregroundColor(.white)
                                .padding(.horizontal, 10)
                                .frame(height: 32)
                                .background(Color.green.opacity(0.28))
                                .clipShape(Capsule())
                                .overlay(
                                    Capsule().stroke(Color.green.opacity(0.6), lineWidth: 0.8)
                                )
                            }
                            .contentShape(Rectangle())
                            .frame(minHeight: 44)
                            .accessibilityIdentifier("BookmarkPlayerShareButton")
                        }

                        // Caption
                        if !bookmark.caption.isEmpty {
                            Text(bookmark.caption)
                                .font(.system(size: 13))
                                .foregroundColor(.white.opacity(0.9))
                                .lineLimit(isCaptionExpanded ? nil : 2)
                                .onTapGesture {
                                    withAnimation(.easeInOut(duration: 0.2)) {
                                        isCaptionExpanded.toggle()
                                    }
                                }
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.bottom, 40)
                }
                .opacity(isChromeVisible ? 1.0 : 0.0)
                .animation(.easeInOut(duration: 0.25), value: isChromeVisible)
            }
        }
        .onAppear {
            AVPlayerPool.shared.pause()
            scrolledReelID = currentBookmark?.reelID
            if let bm = currentBookmark {
                loadVideo(for: bm)
            }
        }
        .onDisappear {
            teardownPlayer()
        }
        .onChange(of: bookmarks.count) { _, newCount in
            // Grid shrank behind the open player (unsave, eviction, remote sync).
            if newCount == 0 {
                teardownPlayer()
                onClose()
            } else if currentIndex >= newCount {
                currentIndex = max(0, newCount - 1)
                scrolledReelID = currentBookmark?.reelID
                isCaptionExpanded = false
                if let bm = currentBookmark {
                    loadVideo(for: bm)
                }
            }
        }
    }

    private func togglePlayPause() {
        guard let p = player else { return }
        if isPlaying {
            p.pause()
            isPlaying = false
            hideChromeWorkItem?.cancel()
            withAnimation(.easeInOut(duration: 0.2)) {
                isChromeVisible = true
            }
        } else {
            p.play()
            isPlaying = true
            scheduleChromeAutoHide()
        }
    }

    private func scheduleChromeAutoHide() {
        hideChromeWorkItem?.cancel()
        let work = DispatchWorkItem {
            withAnimation(.easeInOut(duration: 0.25)) {
                if self.isPlaying {
                    self.isChromeVisible = false
                }
            }
        }
        hideChromeWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5, execute: work)
    }

    private func loadVideo(for bookmark: BookmarkItem) {
        if let token = endObserverToken {
            NotificationCenter.default.removeObserver(token)
            endObserverToken = nil
        }
        player?.pause()

        let mediaURL: URL
        let localURL = LibraryPathResolver.shared.bookmarkFileURL(for: bookmark.reelID)
        if FileManager.default.fileExists(atPath: localURL.path) {
            mediaURL = localURL
        } else if let remoteURL = bookmark.videoUrl {
            mediaURL = remoteURL
        } else {
            return
        }

        let isLocal = (mediaURL == localURL)
        let asset = AVURLAsset(url: mediaURL)
        let item = AVPlayerItem(asset: asset)
        if !isLocal {
            item.preferredForwardBufferDuration = 8.0
        }

        let avPlayer = AVPlayer(playerItem: item)
        avPlayer.automaticallyWaitsToMinimizeStalling = !isLocal
        self.player = avPlayer
        self.isPlaying = true
        AudioSessionCoordinator.shared.activateSession()
        avPlayer.play()

        endObserverToken = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak avPlayer] _ in
            guard avPlayer != nil else { return }
            handleVideoEnded()
        }

        scheduleChromeAutoHide()
    }

    private func handleVideoEnded() {
        if currentIndex + 1 < bookmarks.count {
            advanceToNextBookmark()
        } else {
            // Replay only after the seek completes to avoid re-firing end-of-item.
            let currentPlayer = player
            currentPlayer?.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) { [weak currentPlayer] _ in
                currentPlayer?.play()
            }
        }
    }

    private func advanceToNextBookmark() {
        guard currentIndex + 1 < bookmarks.count else { return }
        isCaptionExpanded = false
        currentIndex += 1
        scrolledReelID = bookmarks[currentIndex].reelID
        if let bm = currentBookmark {
            loadVideo(for: bm)
        }
    }

    private func handleUnsave(bookmark: BookmarkItem) {
        let remaining = bookmarks.filter { $0.reelID != bookmark.reelID }
        if remaining.isEmpty {
            teardownPlayer()
            onDeleteBookmark(bookmark)
            onClose()
            return
        }

        // Determine next bookmark to play and load it first so the scroll anchor is preserved
        let targetIndex = min(currentIndex, remaining.count - 1)
        let nextBookmark = remaining[targetIndex]

        currentIndex = targetIndex
        scrolledReelID = nextBookmark.reelID
        isCaptionExpanded = false
        loadVideo(for: nextBookmark)

        // Delete the unsaved item from database
        onDeleteBookmark(bookmark)
    }

    private func triggerShare(bookmark: BookmarkItem) {
        let caption = bookmark.caption.trimmingCharacters(in: .whitespacesAndNewlines)
        let shareCaption = caption.isEmpty ? "Reel by @\(bookmark.creatorHandle)" : caption

        let localURL = LibraryPathResolver.shared.bookmarkFileURL(for: bookmark.reelID)
        if FileManager.default.fileExists(atPath: localURL.path) {
            ShareSheetPresenter.present(items: [localURL, shareCaption])
            return
        }

        let tempFile = FileManager.default.temporaryDirectory.appendingPathComponent("\(bookmark.reelID).mp4")
        if FileManager.default.fileExists(atPath: tempFile.path) {
            ShareSheetPresenter.present(items: [tempFile, shareCaption])
            return
        }

        guard let videoURL = bookmark.videoUrl else { return }
        Task {
            do {
                let (downloadedURL, _) = try await URLSession.shared.download(from: videoURL)
                if FileManager.default.fileExists(atPath: tempFile.path) {
                    try? FileManager.default.removeItem(at: tempFile)
                }
                try FileManager.default.moveItem(at: downloadedURL, to: tempFile)
                await MainActor.run {
                    ShareSheetPresenter.present(items: [tempFile, shareCaption])
                }
            } catch {
                await MainActor.run {
                    ShareSheetPresenter.present(items: [videoURL, shareCaption])
                }
            }
        }
    }

    private func teardownPlayer() {
        if let token = endObserverToken {
            NotificationCenter.default.removeObserver(token)
            endObserverToken = nil
        }
        hideChromeWorkItem?.cancel()
        hideChromeWorkItem = nil
        player?.pause()
        player = nil
    }
}

public struct BookmarkVideoContainer: UIViewRepresentable {
    public let player: AVPlayer

    public func makeUIView(context: Context) -> PlayerContainerView {
        let view = PlayerContainerView()
        view.playerLayer.player = player
        view.playerLayer.videoGravity = .resizeAspect
        return view
    }

    public func updateUIView(_ uiView: PlayerContainerView, context: Context) {
        if uiView.playerLayer.player !== player {
            uiView.playerLayer.player = player
        }
        uiView.playerLayer.videoGravity = .resizeAspect
        uiView.setNeedsLayout()
    }
}
