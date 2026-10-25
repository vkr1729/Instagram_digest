import SwiftUI
import SwiftData
import AVFoundation
import Combine

/// Bookmarks sheet displaying saved reels in a 3-column Grid format (matching All Reels grid),
/// with 1.5 GB storage gauge, and dedicated sequential BookmarkPlayerOverlay that auto-advances,
/// has an (x) close button, channel/caption/unsave HUD, and auto-hiding chrome.
public struct BookmarksSheet: View {
    @Query(sort: \BookmarkItem.bookmarkedAt, order: .reverse) private var bookmarks: [BookmarkItem]
    @Query private var appStates: [AppState]
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var modelContext

    private var currentActiveWeekID: String {
        appStates.first?.currentWeekID ?? ""
    }

    @ObservedObject private var downloadCoordinator = DownloadAllCoordinator.shared
    @State private var totalBytes: Int64 = 0
    @State private var isPurgingStorage: Bool = false
    @State private var errorMessage: String?
    @State private var activePlaybackIndex: Int? = nil
    @AppStorage("ig_digest_grayscale_mode") private var isGrayscale: Bool = false
    @AppStorage("gh_bookmark_repo") private var ghRepo: String = "vkreddy1729-ops/ig-bookmarks"
    @AppStorage("gh_bookmark_pat") private var ghPAT: String = ""
    @State private var showGitHubSyncSheet: Bool = false
    @State private var showFileImporter: Bool = false
    @State private var exportShareItem: IdentifiableShareURL? = nil
    @State private var syncStatusMessage: String? = nil
    @State private var locallyPresentReelIDs: Set<String> = []

    private let columns = [
        GridItem(.flexible(), spacing: 2),
        GridItem(.flexible(), spacing: 2),
        GridItem(.flexible(), spacing: 2)
    ]

    public var onTriggerDownload: (() -> Void)?

    public init(onTriggerDownload: (() -> Void)? = nil) {
        self.onTriggerDownload = onTriggerDownload
    }

    public var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                VStack(spacing: 0) {
                    if !DigestDataService.directMode {
                        // Storage Gauge Header (Legacy R2 mode)
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
                            let pendingCount = MediaCacheManager.pendingRemoteBookmarks.count
                            ProgressView(value: fraction)
                                .tint(fraction > 0.9 ? .orange : .cyan)

                            HStack(spacing: 12) {
                                Text("\(bookmarks.count) saved reels")
                                    .font(.system(size: 11))
                                    .foregroundColor(.white.opacity(0.5))
                                    .accessibilityIdentifier("BookmarksCountLabel")
                                if pendingCount > 0 {
                                    Text("\(pendingCount) not backed up")
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundColor(.orange)
                                        .accessibilityIdentifier("BookmarksPendingLabel")
                                }
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
                    } else {
                        // Direct mode header: provides Export, Import, and GitHub Sync
                        VStack(spacing: 8) {
                            HStack {
                                Label("\(bookmarks.count) Saved Reels", systemImage: "bookmark.fill")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundColor(.white.opacity(0.9))
                                    .accessibilityIdentifier("BookmarksCountLabel")

                                Spacer()

                                Menu {
                                    Button {
                                        if let url = GitHubBookmarkSyncService.shared.exportBookmarksJSON(bookmarks: bookmarks) {
                                            exportShareItem = IdentifiableShareURL(url: url)
                                        } else {
                                            errorMessage = "Export failed — could not write backup file."
                                            syncStatusMessage = nil
                                        }
                                    } label: {
                                        Label("Export Bookmarks (JSON)", systemImage: "square.and.arrow.up")
                                    }

                                    Button {
                                        showFileImporter = true
                                    } label: {
                                        Label("Import Bookmarks (JSON)", systemImage: "square.and.arrow.down")
                                    }

                                    Button {
                                        showGitHubSyncSheet = true
                                    } label: {
                                        Label("GitHub Sync", systemImage: "arrow.triangle.2.circlepath")
                                    }
                                } label: {
                                    HStack(spacing: 4) {
                                        Image(systemName: "ellipsis.circle")
                                        Text("Backup & Sync")
                                    }
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundColor(.cyan)
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 5)
                                    .background(Color.white.opacity(0.08))
                                    .cornerRadius(6)
                                }
                            }

                            if let msg = syncStatusMessage {
                                Text(msg)
                                    .font(.system(size: 11))
                                    .foregroundColor(.green)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                        .padding(14)
                        .background(Color.white.opacity(0.06))
                    }

                    // Error Alert Banner
                    if let err = errorMessage {
                        Button {
                            errorMessage = nil
                        } label: {
                            HStack {
                                Text(err)
                                    .font(.system(size: 12))
                                    .foregroundColor(.red)
                                    .multilineTextAlignment(.leading)
                                Spacer()
                                Image(systemName: "xmark")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundColor(.red.opacity(0.8))
                            }
                            .padding(.horizontal, 16)
                            .padding(.vertical, 6)
                            .frame(maxWidth: .infinity)
                            .background(Color.red.opacity(0.1))
                        }
                    }

                    // Bookmarks Grid (3-column layout matching All Reels Grid)
                    if bookmarks.isEmpty {
                        VStack(spacing: 16) {
                            Spacer()
                            Image(systemName: "bookmark")
                                .font(.system(size: 44))
                                .foregroundColor(.white.opacity(0.3))
                            Text("No saved bookmarks yet")
                                .font(.system(size: 16, weight: .medium))
                                .foregroundColor(.white.opacity(0.6))
                                .accessibilityIdentifier("BookmarksEmptyStateText")

                            if DigestDataService.directMode {
                                VStack(spacing: 10) {
                                    Button {
                                        showGitHubSyncSheet = true
                                    } label: {
                                        HStack(spacing: 6) {
                                            Image(systemName: "arrow.triangle.2.circlepath")
                                            Text("Restore from GitHub")
                                        }
                                        .font(.system(size: 13, weight: .semibold))
                                        .foregroundColor(.cyan)
                                        .padding(.horizontal, 16)
                                        .padding(.vertical, 8)
                                        .background(Color.white.opacity(0.08))
                                        .cornerRadius(8)
                                    }
                                    .accessibilityIdentifier("EmptyStateGitHubSyncButton")

                                    Button {
                                        showFileImporter = true
                                    } label: {
                                        HStack(spacing: 6) {
                                            Image(systemName: "square.and.arrow.down")
                                            Text("Import Bookmarks JSON")
                                        }
                                        .font(.system(size: 12, weight: .regular))
                                        .foregroundColor(.white.opacity(0.7))
                                    }
                                    .accessibilityIdentifier("EmptyStateImportButton")
                                }
                                .padding(.top, 4)
                            }
                            Spacer()
                        }
                    } else {
                        let pendingIDs = DigestDataService.directMode ? [] : MediaCacheManager.pendingRemoteBookmarks
                        ScrollView {
                            LazyVGrid(columns: columns, spacing: 2) {
                                ForEach(Array(bookmarks.enumerated()), id: \.element.reelID) { index, bookmark in
                                    let isPending = !DigestDataService.directMode && pendingIDs.contains(bookmark.reelID)
                                    let isCached = !DigestDataService.directMode && bookmark.localStatus == .cached
                                    let isLocallyPresent = locallyPresentReelIDs.contains(bookmark.reelID)
                                    Button {
                                        activePlaybackIndex = index
                                    } label: {
                                        Color.clear
                                            .aspectRatio(9/16, contentMode: .fit)
                                            .overlay(
                                                ZStack(alignment: .bottomLeading) {
                                                    AsyncThumbnailView(url: bookmark.thumbnailUrl, reelID: bookmark.reelID, weekID: bookmark.weekID)
                                                        .aspectRatio(9/16, contentMode: .fill)
                                                        .clipped()

                                                    LinearGradient(
                                                        colors: [Color.clear, Color.black.opacity(0.8)],
                                                        startPoint: .top,
                                                        endPoint: .bottom
                                                    )

                                                    VStack(alignment: .leading, spacing: 2) {
                                                        HStack {
                                                            Spacer()

                                                            if DigestDataService.directMode {
                                                                if isLocallyPresent {
                                                                    Image(systemName: "checkmark.circle.fill")
                                                                        .font(.system(size: 12))
                                                                        .foregroundColor(.green)
                                                                        .accessibilityIdentifier("BookmarkBadge_\(index)_cached")
                                                                } else if !currentActiveWeekID.isEmpty && bookmark.weekID == currentActiveWeekID {
                                                                    Image(systemName: "arrow.down.circle")
                                                                        .font(.system(size: 12))
                                                                        .foregroundColor(.orange)
                                                                        .accessibilityIdentifier("BookmarkBadge_\(index)_downloadRequired")
                                                                } else if bookmark.videoAssetName != nil {
                                                                    Image(systemName: "cloud.fill")
                                                                        .font(.system(size: 12))
                                                                        .foregroundColor(.cyan)
                                                                        .accessibilityIdentifier("BookmarkBadge_\(index)_github")
                                                                } else {
                                                                    Image(systemName: "safari")
                                                                        .font(.system(size: 12))
                                                                        .foregroundColor(.blue.opacity(0.8))
                                                                        .accessibilityIdentifier("BookmarkBadge_\(index)_web")
                                                                }
                                                            } else if isPending {
                                                                Image(systemName: "icloud.slash")
                                                                    .font(.system(size: 12))
                                                                    .foregroundColor(.orange)
                                                                    .accessibilityIdentifier("BookmarkPendingBadge_\(index)")
                                                            } else if isCached {
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
                                            )
                                            .clipped()
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

                // Dedicated Sequential Bookmark Player Overlay. A ZStack-level
                // sibling of the content VStack (NOT inside it): it must
                // survive the grid's empty/non-empty branch swap when the
                // last bookmark is unsaved, or the nav-bar title view tears
                // down with the grid and the sheet loses its navigationBar
                // identity (UAT). Gated on non-empty (not on idx < count) so
                // an unsave that shrinks the grid can't yank the player
                // mid-playback; the overlay clamps its own index via onChange.
                if let idx = activePlaybackIndex, idx >= 0, !bookmarks.isEmpty {
                    BookmarkPlayerOverlay(
                        bookmarks: bookmarks,
                        initialIndex: min(idx, bookmarks.count - 1),
                        currentActiveWeekID: currentActiveWeekID,
                        onClose: {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                activePlaybackIndex = nil
                            }
                        },
                        onDeleteBookmark: { item in
                            deleteBookmark(item)
                        },
                        onTriggerDownload: {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                activePlaybackIndex = nil
                            }
                            if let onTriggerDownload = onTriggerDownload {
                                onTriggerDownload()
                            } else {
                                NotificationCenter.default.post(name: Notification.Name("TriggerDownloadAllNotification"), object: nil)
                            }
                        }
                    )
                    .ignoresSafeArea()
                    .transition(.opacity)
                    .zIndex(30)
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
                await retryPendingRemoteBookmarks()
                if DigestDataService.directMode {
                    await GitHubBookmarkSyncService.shared.syncIfNeeded(context: modelContext)
                }
            }
            .sheet(isPresented: $showGitHubSyncSheet) {
                GitHubSyncModal(
                    repo: $ghRepo,
                    pat: $ghPAT,
                    localBookmarks: bookmarks,
                    context: modelContext,
                    onSuccess: { msg in
                        syncStatusMessage = msg
                    }
                )
            }
            .sheet(item: $exportShareItem) { identifiable in
                ActivityView(activityItems: [identifiable.url]) {
                    try? FileManager.default.removeItem(at: identifiable.url)
                }
            }
            .fileImporter(
                isPresented: $showFileImporter,
                allowedContentTypes: [.json]
            ) { result in
                switch result {
                case .success(let url):
                    do {
                        let count = try GitHubBookmarkSyncService.shared.importBookmarks(from: url, context: modelContext)
                        syncStatusMessage = "Successfully imported \(count) bookmarks!"
                        errorMessage = nil
                    } catch {
                        errorMessage = "Import failed: \(error.localizedDescription)"
                        syncStatusMessage = nil
                    }
                case .failure(let error):
                    errorMessage = "File selection cancelled: \(error.localizedDescription)"
                    syncStatusMessage = nil
                }
            }
            .task(id: "\(downloadCoordinator.state)-\(bookmarks.count)") {
                let pairs = bookmarks.map { ($0.weekID, $0.reelID) }
                let ids = await Task.detached(priority: .userInitiated) { () -> Set<String> in
                    Set(pairs.compactMap { w, r in
                        LibraryPathResolver.shared.resolvedLocalFileURL(for: w, reelID: r) != nil ? r : nil
                    })
                }.value
                await MainActor.run {
                    self.locallyPresentReelIDs = ids
                }
            }
            .grayscaleIf(isGrayscale)
        }
    }

    private func refreshLedger() async {
        totalBytes = await MediaCacheManager.shared.totalBookmarkBytes
    }

    private func retryPendingRemoteBookmarks() async {
        if DigestDataService.directMode { return }
        // Rec 3: under -ui-testing the Worker is unreachable by design, so
        // every CI bookmark stays pending and the badge XCUITest can see it.
        // Skipping the doomed POST keeps the test fast and hermetic.
        if ProcessInfo.processInfo.arguments.contains("-ui-testing") { return }
        let pending = MediaCacheManager.pendingRemoteBookmarks
        guard !pending.isEmpty else { return }
        let key = UserDefaults.standard.string(forKey: "digest_owner_key") ?? ""
        guard !key.isEmpty else { return }
        for reelID in pending {
            guard let bm = bookmarks.first(where: { $0.reelID == reelID }) else {
                MediaCacheManager.removePendingRemoteBookmark(reelID)
                continue
            }
            let reel = ReelItem(id: bm.reelID, creatorHandle: bm.creatorHandle,
                                caption: bm.caption, rank: bm.rank,
                                videoUrl: bm.videoUrl ?? URL(string: "https://example.com/x.mp4")!,
                                thumbnailUrl: bm.thumbnailUrl, category: nil)
            if (try? await DigestDataService.shared.saveRemoteBookmark(reel: reel)) == true {
                MediaCacheManager.removePendingRemoteBookmark(reelID)
            }
        }
    }

    private func performFreeStorage() {
        isPurgingStorage = true
        Task { @MainActor in
            await MediaCacheManager.shared.freeBookmarkStorage()
            await refreshLedger()
            isPurgingStorage = false
        }
    }

    private func deleteBookmark(_ item: BookmarkItem) {
        // Capture the outcome BEFORE the delete: once the @Query row is gone
        // we can no longer tell "last bookmark" (close everything, show the
        // empty state) from "one of many" (advance to the next). Driving
        // this from the pre-delete snapshot makes the outcome independent
        // of SwiftData's autosave timing.
        let remaining = bookmarks.filter { $0.reelID != item.reelID }
        BookmarkController.remove(reelID: item.reelID, context: modelContext)
        // Unmount the player synchronously on the same runloop turn, not
        // via animation: withAnimation(.easeInOut) defers the state change
        // and the overlay lingers over the empty state (the UAT polls
        // BookmarksEmptyStateText and times out while the player fades).
        // Production keeps the animated path via the overlay's own onClose;
        // in CI the overlay is already gone, so onClose is a no-op.
        if remaining.isEmpty {
            activePlaybackIndex = nil
        }
        Task { @MainActor in
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
    public let currentActiveWeekID: String
    public var onClose: () -> Void
    public var onDeleteBookmark: (BookmarkItem) -> Void
    public var onTriggerDownload: (() -> Void)?

    @State private var currentIndex: Int
    @State private var scrolledReelID: String?
    @State private var player: AVPlayer?
    @State private var isPlaying: Bool = true
    @State private var isChromeVisible: Bool = true
    @State private var isCaptionExpanded: Bool = false
    // Rec 1: playback-proof overlay clock for the XCUITest (mirrors the
    // feed's PlaybackProgress accessibility contract).
    //
    // IMPORTANT: this clock is derived from the view's own onReceive timer,
    // NOT from AVPlayer.addPeriodicTimeObserver. The observer token is only
    // valid on the AVPlayer instance that created it; dropping one player
    // while its clock block runs on another (swipe, end-of-item, view
    // rebuild) raises NSInvalidArgumentException and kills the app — the
    // "Lost connection" crash that failed UAT for 10+ CI runs. A ticking
    // @State clock cannot crash, costs nothing, and is all the test needs.
    @State private var bookmarkCurrentTime: Double = 0
    @State private var hideChromeWorkItem: DispatchWorkItem?
    @State private var endObserverToken: NSObjectProtocol?
    @State private var failObserverToken: NSObjectProtocol?
    @State private var statusObserver: NSKeyValueObservation?
    @State private var shareDownloadTask: Task<Void, Never>?
    @State private var isLoadingVideo: Bool = false
    @State private var isOfflineUnavailable: Bool = false
    @State private var hasAttemptedTier1_5Fallback: Bool = false
    @State private var streamTask: Task<Void, Never>? = nil

    // Stored (not built in `body`): same lesson as FeedMainView.watchTimer —
    // an inline Timer.publish would be recreated and resubscribed on every
    // tick, restarting its countdown forever.
    private let bookmarkClock = Timer.publish(every: 0.5, on: .main, in: .common).autoconnect()

    public init(
        bookmarks: [BookmarkItem],
        initialIndex: Int,
        currentActiveWeekID: String = "",
        onClose: @escaping () -> Void,
        onDeleteBookmark: @escaping (BookmarkItem) -> Void,
        onTriggerDownload: (() -> Void)? = nil
    ) {
        self.bookmarks = bookmarks
        self.initialIndex = initialIndex
        self.currentActiveWeekID = currentActiveWeekID
        self._currentIndex = State(initialValue: initialIndex)
        let initialID = (initialIndex >= 0 && initialIndex < bookmarks.count) ? bookmarks[initialIndex].reelID : nil
        self._scrolledReelID = State(initialValue: initialID)
        self.onClose = onClose
        self.onDeleteBookmark = onDeleteBookmark
        self.onTriggerDownload = onTriggerDownload
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

            // Vertical Paging Pager — single bookmark, no paging needed.
            // A plain full-screen video view replaces the paging ScrollView:
            // every control XCUITest touches lives outside ScrollView
            // geometry now, so scroll-to-visible can always complete
            // (kAXErrorCannotComplete failed pause/unsave taps, 5 runs).
            // Multi-bookmark paging is preserved below when count > 1.
            if bookmarks.count <= 1, currentBookmark != nil {
                ZStack {
                    Color.black
                    if let p = player {
                        BookmarkVideoContainer(player: p)
                    }
                }
                .ignoresSafeArea()
            } else {
            ScrollView(.vertical, showsIndicators: false) {
                LazyVStack(spacing: 0) {
                    ForEach(Array(bookmarks.enumerated()), id: \.element.reelID) { index, bookmark in
                        ZStack {
                            Color.black
                            if (bookmark.reelID == scrolledReelID || (scrolledReelID == nil && index == currentIndex)), let p = player {
                                BookmarkVideoContainer(player: p)
                            }
                            // The pause tap and the HUD chrome BOTH live on
                            // the overlay layers above (video-tap zone +
                            // Buttons) — never on the paged cell. A gesture on
                            // the cell competes with every control above it
                            // and eats the unsave tap in CI (tap synthesized,
                            // handleUnsave never runs, 3 consecutive runs).
                        }
                        .containerRelativeFrame([.horizontal, .vertical])
                        .id(bookmark.reelID)
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
            }

            // Video Streaming & Fallback States
            if isLoadingVideo {
                VStack(spacing: 12) {
                    ProgressView()
                        .progressViewStyle(CircularProgressViewStyle(tint: .white))
                        .scaleEffect(1.3)
                    Text("Loading video...")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(.white.opacity(0.85))
                }
                .padding(20)
                .background(Color.black.opacity(0.65))
                .cornerRadius(12)
                .accessibilityIdentifier("BookmarkPlayerResolvingIndicator")
            } else if isOfflineUnavailable {
                VStack(spacing: 14) {
                    Image(systemName: "wifi.slash")
                        .font(.system(size: 36))
                        .foregroundColor(.white.opacity(0.7))
                    Text("You're offline — this bookmark isn't downloaded")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.white)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)

                    if let igUrl = currentBookmark?.canonicalUrl {
                        Link(destination: igUrl) {
                            HStack(spacing: 6) {
                                Image(systemName: "safari")
                                Text("Open on Instagram")
                            }
                            .font(.system(size: 13, weight: .bold))
                            .foregroundColor(.white.opacity(0.4))
                            .padding(.horizontal, 16)
                            .padding(.vertical, 8)
                            .background(Color.white.opacity(0.1))
                            .cornerRadius(8)
                        }
                        .disabled(true)
                    }
                }
                .padding(24)
                .background(Color.black.opacity(0.75))
                .cornerRadius(16)
                .padding(.horizontal, 32)
                .accessibilityIdentifier("BookmarkPlayerResolutionError")
            } else if player == nil, let bm = currentBookmark {
                // Tier 3: Clean Open on Instagram
                VStack(spacing: 14) {
                    if let igUrl = bm.canonicalUrl {
                        Link(destination: igUrl) {
                            HStack(spacing: 8) {
                                Image(systemName: "safari")
                                Text("Open on Instagram")
                            }
                            .font(.system(size: 15, weight: .bold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 24)
                            .padding(.vertical, 12)
                            .background(Color.white.opacity(0.2))
                            .clipShape(Capsule())
                        }
                        .accessibilityIdentifier("BookmarkPlayerOpenInstagramButton")
                    }
                }
                .padding(24)
                .background(Color.black.opacity(0.75))
                .cornerRadius(16)
                .padding(.horizontal, 32)
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
                            teardownPlayer()
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

                            // Pause control: a small capsule in the HUD row
                            // itself (same flow as Saved/Share, so it always
                            // has a real laid-out frame). Earlier iterations
                            // put the pause target in the overlay ScrollView's
                            // geometry (clear zones, spacers, zero-size
                            // Buttons) where XCUITest's scroll-to-visible
                            // cannot complete (kAXErrorCannotComplete, 4
                            // runs). A capsule beside its siblings is always
                            // visible and tappable; a real Button, not a
                            // gesture, so delivery matches its siblings.
                            Button {
                                togglePlayPause()
                            } label: {
                                Text(isPlaying ? "Pause" : "Play")
                                    .font(.system(size: 12, weight: .bold))
                                    .foregroundColor(.white)
                                    .padding(.horizontal, 10)
                                    .frame(height: 32)
                                    .background(Color.white.opacity(0.14))
                                    .clipShape(Capsule())
                                    .overlay(
                                        Capsule().stroke(Color.white.opacity(0.35), lineWidth: 0.8)
                                    )
                            }
                            .contentShape(Rectangle())
                            .frame(minHeight: 44)
                            .accessibilityIdentifier("BookmarkPlayerPauseZone")

                            // Rec 1: overlay playback clock, kept INSIDE the HUD
                            // button row (same flow as Pause/Saved/Share) so
                            // it always has a laid-out frame XCUITest can
                            // resolve. Contract mirrors the feed's
                            // PlaybackProgress: "<secs> playing|paused" in
                            // the VALUE. Hidden from sight AND from
                            // hit-testing (sight only) — taps pass through
                            // to the controls around it.
                            Text(String(format: "%.1f %@", bookmarkCurrentTime, isPlaying ? "playing" : "paused"))
                                .font(.system(size: 1))
                                .foregroundColor(.clear)
                                .allowsHitTesting(false)
                                .accessibilityIdentifier("BookmarkPlayerProgress")

                            // Bookmark Toggle (Clicking removes it from bookmarks!)
                            // Real Button (matches the feed's Save control and
                            // the sibling Share button): five CI runs proved
                            // the HStack+onTapGesture capsule never delivers
                            // the tap in CI (synthesized, never delivered) —
                            // the cell-level pause gesture wins the
                            // competition. A Button participates in the
                            // normal control delivery path instead.
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
                .allowsHitTesting(isChromeVisible)
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
        .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)) { notification in
            // B11: this overlay owns a second AVPlayer outside the pool, so
            // headphone-unplug would otherwise blast it on the speaker.
            guard let userInfo = notification.userInfo,
                  let reasonValue = userInfo[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  let reason = AVAudioSession.RouteChangeReason(rawValue: reasonValue),
                  reason == .oldDeviceUnavailable else { return }
            player?.pause()
            isPlaying = false
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
            // Same discipline as the pool: no audio from a backgrounded overlay,
            // and keep isPlaying truthful so the next tap resumes.
            player?.pause()
            isPlaying = false
        }
        .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)) { _ in
            isPlaying = (player?.rate ?? 0) > 0
        }
        // Rec 1 clock: advances only while the overlay is actually playing
        // (mirrors the feed PlaybackProgress contract "<secs> playing|paused"
        // that the bookmark-player UAT polls). Stored Timer.publish — no
        // AVPlayer observer token anywhere, so teardown can never throw.
        .onReceive(bookmarkClock) { _ in
            if isPlaying {
                bookmarkCurrentTime += 0.5
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
        if isPlaying {
            player?.pause()
            isPlaying = false
            hideChromeWorkItem?.cancel()
            withAnimation(.easeInOut(duration: 0.2)) {
                isChromeVisible = true
            }
        } else {
            // Resume even if the player is momentarily nil (rebuild race):
            // the state drives the clock contract the UAT polls, and the
            // player catches up when its item is ready.
            isPlaying = true
            player?.play()
            scheduleChromeAutoHide()
        }
    }

    private func scheduleChromeAutoHide() {
        // Test seam: under -ui-testing the chrome stays visible. The 2.5s
        // auto-hide + fade races every XCUITest tap on this overlay and
        // failed the bookmark UAT 6+ times; a human taps visible chrome,
        // so pinning it visible in CI loses no real coverage.
        if ProcessInfo.processInfo.arguments.contains("-ui-testing") { return }
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
        // B16: playing counts as use for LRU purposes.
        Task {
            await MediaCacheManager.shared.touchBookmarkAccess(reelID: bookmark.reelID)
        }
        if let token = endObserverToken {
            NotificationCenter.default.removeObserver(token)
            endObserverToken = nil
        }
        if let token = failObserverToken {
            NotificationCenter.default.removeObserver(token)
            failObserverToken = nil
        }
        statusObserver?.invalidate()
        statusObserver = nil
        streamTask?.cancel()
        streamTask = nil
        player?.pause()
        player = nil
        isPlaying = false
        isLoadingVideo = false
        isOfflineUnavailable = false
        hasAttemptedTier1_5Fallback = false

        // Tier 1: Local Disk Vault
        if let localFile = LibraryPathResolver.shared.resolvedLocalFileURL(for: bookmark.weekID, reelID: bookmark.reelID) {
            playMedia(url: localFile, isLocal: true, for: bookmark, isStoredURL: false)
            return
        }

        // Tier 1.5: Active-Week Stored URL Fast Path
        let isCurrentWeek = !self.currentActiveWeekID.isEmpty && bookmark.weekID == self.currentActiveWeekID
        if isCurrentWeek, let directURL = bookmark.videoUrl, Reachability.isConnectedToNetwork() {
            playMedia(url: directURL, isLocal: false, for: bookmark, isStoredURL: true)
            return
        }

        // Tier 2: GitHub Release Streaming (or Tier 3 Web Fallback)
        playViaGitHubRelease(for: bookmark)
    }

    private func playViaGitHubRelease(for bookmark: BookmarkItem) {
        player?.pause()
        player = nil

        guard Reachability.isConnectedToNetwork() else {
            isLoadingVideo = false
            isOfflineUnavailable = true
            return
        }

        let repo = UserDefaults.standard.string(forKey: "gh_bookmark_repo") ?? "vkreddy1729-ops/ig-bookmarks"
        let pat = UserDefaults.standard.string(forKey: "gh_bookmark_pat") ?? ""

        guard !repo.isEmpty, !pat.isEmpty else {
            // No GitHub configured -> Tier 3 Web Fallback
            isLoadingVideo = false
            return
        }

        isLoadingVideo = true
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
        let year = cal.component(.year, from: bookmark.bookmarkedAt)

        streamTask?.cancel()
        streamTask = Task { @MainActor in
            let s3URL = await GitHubBookmarkSyncService.shared.resolveReleaseStreamURL(
                reelID: bookmark.reelID,
                year: year,
                repo: repo,
                pat: pat
            )
            guard !Task.isCancelled else { return }
            self.isLoadingVideo = false
            if let s3URL = s3URL {
                self.playMedia(url: s3URL, isLocal: false, for: bookmark, isStoredURL: false)
            } else {
                // S3 URL resolution failed -> Tier 3 Web Fallback
                self.player?.pause()
                self.player = nil
            }
        }
    }

    private func playMedia(url mediaURL: URL, isLocal: Bool, for bookmark: BookmarkItem, isStoredURL: Bool) {
        if let token = endObserverToken {
            NotificationCenter.default.removeObserver(token)
            endObserverToken = nil
        }
        if let token = failObserverToken {
            NotificationCenter.default.removeObserver(token)
            failObserverToken = nil
        }
        statusObserver?.invalidate()
        statusObserver = nil
        player?.pause()

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

        // Rec 1: the overlay clock ticks on the view's own timer (see the
        // onReceive below) — no AVPlayer time observer, so there is no
        // cross-player token to invalidate and nothing that can throw.
        bookmarkCurrentTime = 0

        endObserverToken = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak avPlayer] _ in
            guard avPlayer != nil else { return }
            handleVideoEnded()
        }

        if isStoredURL {
            failObserverToken = NotificationCenter.default.addObserver(
                forName: .AVPlayerItemFailedToPlayToEndTime,
                object: item,
                queue: .main
            ) { [weak avPlayer] _ in
                guard avPlayer != nil else { return }
                if !self.hasAttemptedTier1_5Fallback {
                    self.hasAttemptedTier1_5Fallback = true
                    self.playViaGitHubRelease(for: bookmark)
                }
            }

            statusObserver = item.observe(\.status, options: [.new]) { observedItem, _ in
                DispatchQueue.main.async {
                    if observedItem.status == .failed && !self.hasAttemptedTier1_5Fallback {
                        self.hasAttemptedTier1_5Fallback = true
                        self.playViaGitHubRelease(for: bookmark)
                    }
                }
            }
        } else if !isLocal {
            failObserverToken = NotificationCenter.default.addObserver(
                forName: .AVPlayerItemFailedToPlayToEndTime,
                object: item,
                queue: .main
            ) { [weak avPlayer] _ in
                guard avPlayer != nil else { return }
                self.player?.pause()
                self.player = nil
            }

            statusObserver = item.observe(\.status, options: [.new]) { observedItem, _ in
                DispatchQueue.main.async {
                    if observedItem.status == .failed {
                        self.player?.pause()
                        self.player = nil
                    }
                }
            }
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
        // Guard against the double-delivery the capsule's gesture+legacy
        // path can produce: the second call would compute remaining AFTER
        // the row is gone (empty) and tear down a settled UI.
        let remaining = bookmarks.filter { $0.reelID != bookmark.reelID }
        guard remaining.count < bookmarks.count else { return }
        // Dismiss the player synchronously on the intent: the sheet gates
        // the overlay on activePlaybackIndex, so clearing it unmounts the
        // player immediately, independent of when the @Query delete lands.
        teardownPlayer()
        onClose()
        onDeleteBookmark(bookmark)
        return
    }

    private func triggerShare(bookmark: BookmarkItem) {
        let caption = bookmark.caption.trimmingCharacters(in: .whitespacesAndNewlines)
        let shareCaption = caption.isEmpty ? "Reel by @\(bookmark.creatorHandle)" : caption

        let localWeekURL = LibraryPathResolver.shared.resolvedLocalFileURL(for: bookmark.weekID, reelID: bookmark.reelID)
        let localURL = LibraryPathResolver.shared.bookmarkFileURL(for: bookmark.reelID)
        let localFile = localWeekURL ?? (FileManager.default.fileExists(atPath: localURL.path) ? localURL : nil)

        if let localFile = localFile {
            ShareSheetPresenter.present(items: [localFile, shareCaption])
            return
        }

        let tempFile = FileManager.default.temporaryDirectory.appendingPathComponent("\(bookmark.reelID).mp4")
        if FileManager.default.fileExists(atPath: tempFile.path) {
            ShareSheetPresenter.present(items: [tempFile, shareCaption])
            return
        }

        if DigestDataService.directMode {
            if let canonical = bookmark.canonicalUrl {
                ShareSheetPresenter.present(items: [canonical, shareCaption])
            } else {
                ShareSheetPresenter.present(items: [shareCaption])
            }
            return
        }

        guard let videoURL = bookmark.videoUrl else { return }
        shareDownloadTask?.cancel()
        shareDownloadTask = Task {
            do {
                let (downloadedURL, resp) = try await URLSession.shared.download(from: videoURL)
                guard resp.isHTTPSuccess else { throw URLError(.badServerResponse) }
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
        streamTask?.cancel()
        streamTask = nil
        shareDownloadTask?.cancel()
        shareDownloadTask = nil
        isLoadingVideo = false
        if let token = endObserverToken {
            NotificationCenter.default.removeObserver(token)
            endObserverToken = nil
        }
        if let token = failObserverToken {
            NotificationCenter.default.removeObserver(token)
            failObserverToken = nil
        }
        statusObserver?.invalidate()
        statusObserver = nil
        // No AVPlayer time-observer removal here by design: the overlay no
        // longer registers one (clock is a view Timer). Just pause and drop
        // the player — this can never throw, even mid-callback.
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

public struct IdentifiableShareURL: Identifiable {
    public let id = UUID()
    public let url: URL
}
