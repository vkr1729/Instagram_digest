import SwiftUI
import SwiftData
import AVFoundation
import Network

@main
struct InstagramDigestApp: App {
    let modelContainer: ModelContainer

    init() {
        LibraryPathResolver.shared.ensureApplicationSupportExists()

        let isUITesting = ProcessInfo.processInfo.arguments.contains("-ui-testing")

        if isUITesting {
            try? FileManager.default.removeItem(at: LibraryPathResolver.shared.mediaCacheBaseURL)
            try? LibraryPathResolver.shared.ensureDirectoryExists(at: LibraryPathResolver.shared.mediaCacheBaseURL)
        }

        do {
            let schema = Schema([
                WatchedEvent.self,
                BookmarkItem.self,
                DailyProgress.self,
                AppState.self
            ])
            let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: isUITesting)
            let container = try ModelContainer(for: schema, configurations: [config])
            self.modelContainer = container

            // Wire container to MediaCacheManager actor
            Task {
                await MediaCacheManager.shared.setModelContainer(container)
                await MediaCacheManager.shared.reconcileBookmarkStorageLedger()

                // Wire download suspension handler for Free Local Storage
                await MediaCacheManager.shared.setDownloadSuspensionHandler { suspend in
                    await MainActor.run {
                        if suspend {
                            DownloadAllCoordinator.shared.suspendQueue()
                        } else {
                            DownloadAllCoordinator.shared.resumeQueue()
                        }
                    }
                }
            }

            // Pre-configure audio session
            Task { @MainActor in
                AudioSessionCoordinator.shared.configureAudioSession()
            }

            // Reset download state in UI testing
            if isUITesting {
                Task { @MainActor in
                    DownloadAllCoordinator.shared.cancelAll()
                }
            }

            // Seed mindful progress milestone in UI testing if requested
            if isUITesting && ProcessInfo.processInfo.arguments.contains("-ui-testing-seed-mindful") {
                let context = ModelContext(container)
                let formatter = DateFormatter()
                formatter.dateFormat = "yyyy-MM-dd"
                formatter.calendar = Calendar(identifier: .gregorian)
                formatter.locale = Locale(identifier: "en_US_POSIX")
                let todayStr = formatter.string(from: Date())
                let daily = DailyProgress(dateString: todayStr, viewedCount: 50, snoozeUntil: nil)
                context.insert(daily)
                try? context.save()
            }
        } catch {
            fatalError("Failed to initialize SwiftData ModelContainer: \(error)")
        }
    }

    var body: some Scene {
        WindowGroup {
            FeedMainView()
                .modelContainer(modelContainer)
                .preferredColorScheme(.dark)
        }
    }
}

/// Main Feed screen strictly conforming to locked Mock 2 (Mobile PWA Standard).
/// Hosts cursive Instagram brand logo, Jump-to-N pill, Grid, Download, Bookmarks chip,
/// 7-story category circles bar, uncropped video pager, bottom HUD with creator handle,
/// WhatsApp share, Gold bookmark button, 2-line caption, and auto-immersive playback.
struct FeedMainView: View {
    @Environment(\.modelContext) private var modelContext
    @ObservedObject private var pool = AVPlayerPool.shared

    @State private var manifest: DigestManifest?
    @State private var allReels: [ReelItem] = []
    @State private var isLoading: Bool = true
    @State private var errorMessage: String?

    @State private var activeIndex: Int = 0
    @State private var selectedCategoryId: String = "all"
    @State private var lastScrollEndTime: TimeInterval = 0
    @State private var seekPreviewFraction: Double? = nil
    @State private var showBookmarkPop: Bool = false
    @State private var isCaptionExpanded: Bool = false
    @State private var isChromeVisible: Bool = true

    // Modals state
    @State private var showGridSheet: Bool = false
    @State private var showDownloadSheet: Bool = false
    @State private var showBookmarksSheet: Bool = false
    @State private var showJumpModal: Bool = false
    @State private var showMindfulModal: Bool = ProcessInfo.processInfo.arguments.contains("-ui-testing-seed-mindful")
    @State private var showShareSheet: Bool = false
    @State private var shareItems: [Any] = []

    // Watched reels and dynamic bookmarks query for instant HUD reflection
    @State private var watchedReelIDs: Set<String> = []
    @Query private var savedBookmarks: [BookmarkItem]

    private var bookmarkedReelIDs: Set<String> {
        Set(savedBookmarks.map { $0.reelID })
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if isLoading {
                VStack(spacing: 16) {
                    ProgressView()
                        .tint(.white)
                    Text("Loading Weekly Digest...")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundColor(.white.opacity(0.8))
                }
                .accessibilityIdentifier("LoadingDigestView")
            } else if let error = errorMessage {
                VStack(spacing: 16) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 40))
                        .foregroundColor(.orange)
                    Text(error)
                        .font(.system(size: 15))
                        .foregroundColor(.white)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 32)
                    Button("Retry") {
                        loadManifest()
                    }
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(Color.white)
                    .foregroundColor(.black)
                    .clipShape(Capsule())
                }
            } else if !pool.currentItems.isEmpty {
                let safeIndex = min(max(0, activeIndex), pool.currentItems.count - 1)
                let currentReel = pool.currentItems[safeIndex]
                let isCurrentBookmarked = bookmarkedReelIDs.contains(currentReel.id)

                // 1. Vertical Pager Container (UICollectionView with native uncropped aspect containment)
                FeedPagerView(
                    reels: pool.currentItems,
                    currentIndex: $activeIndex,
                    onPageChanged: { newIndex in
                        isCaptionExpanded = false
                        pool.setCurrentIndex(newIndex)
                    },
                    onScrollEnded: { uptime in
                        lastScrollEndTime = uptime
                    },
                    onForwardScrollPast: { departedReel in
                        recordWatched(reel: departedReel)
                    },
                    onTogglePlayPause: {
                        if pool.isLatched2x {
                            pool.setLatched2x(false)
                        }
                        pool.togglePlayPause()
                        withAnimation(.easeInOut(duration: 0.25)) {
                            isChromeVisible = !pool.isPlaying
                        }
                    },
                    onSeekPreview: { fraction in
                        seekPreviewFraction = fraction
                    },
                    onSeekCommit: { fraction in
                        pool.commitSeek(to: fraction)
                        seekPreviewFraction = nil
                    },
                    onToggleLatched2x: {
                        pool.setLatched2x(!pool.isLatched2x)
                    },
                    lastScrollEndTime: lastScrollEndTime,
                    currentProgress: pool.currentProgress
                )
                .ignoresSafeArea()

                // 2. Full-Bleed Mock 2 Overlay (Natural Scrim, Progress, Creator, Buttons, Caption)
                ReelCardOverlayView(
                    reel: currentReel,
                    isLatched2x: pool.isLatched2x,
                    isBookmarked: isCurrentBookmarked,
                    seekFractionPreview: seekPreviewFraction,
                    progress: pool.currentProgress,
                    duration: pool.currentDuration,
                    currentTime: pool.currentTime,
                    showBookmarkPop: showBookmarkPop,
                    isCaptionExpanded: isCaptionExpanded,
                    onTogglePlayPause: {
                        pool.togglePlayPause()
                        withAnimation(.easeInOut(duration: 0.25)) {
                            isChromeVisible = !pool.isPlaying
                        }
                    },
                    onTriggerBookmark: {
                        toggleBookmarkCurrentReel()
                    },
                    onTriggerShare: {
                        triggerShareCurrentReel()
                    },
                    onToggleCaption: {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            isCaptionExpanded.toggle()
                        }
                    }
                )
                .ignoresSafeArea()
                .opacity(isChromeVisible ? 1.0 : 0.0)
                .animation(.easeInOut(duration: 0.25), value: isChromeVisible)

                // 3. Top Navigation Header & Story Category Circles Bar (Mock 2 Locked)
                VStack(spacing: 0) {
                    HeaderBarView(
                        currentIndex: activeIndex,
                        totalCount: pool.currentItems.count,
                        bookmarkCount: savedBookmarks.count,
                        onTapJump: {
                            pool.pause()
                            showJumpModal = true
                        },
                        onTapGrid: {
                            pool.pause()
                            showGridSheet = true
                        },
                        onTapOffline: {
                            pool.pause()
                            showDownloadSheet = true
                        },
                        onTapBookmarks: {
                            pool.pause()
                            showBookmarksSheet = true
                        }
                    )

                    StoryCategoryBarView(
                        totalCount: allReels.count,
                        selectedCategoryId: selectedCategoryId,
                        onSelectCategory: { newCat in
                            selectedCategoryId = newCat
                            guard let m = manifest else { return }
                            pool.filterByCategory(newCat, allReels: allReels, weekID: m.weekId)
                            activeIndex = 0
                        }
                    )

                    Spacer()
                }
                .opacity(isChromeVisible ? 1.0 : 0.0)
                .animation(.easeInOut(duration: 0.25), value: isChromeVisible)
            }

            // Mindful 50-reels Modal Overlay (Presented at top ZStack level)
            if showMindfulModal {
                MindfulDailyModalView(
                    viewedCount: getTodayViewedCount(),
                    onTakeABreak: {
                        pool.pause()
                        showMindfulModal = false
                    },
                    onSnoozeForToday: {
                        snoozeMindfulModalForToday()
                        showMindfulModal = false
                    },
                    onDismiss: {
                        showMindfulModal = false
                    }
                )
                .transition(.opacity)
                .zIndex(30)
            }
        }
        .sheet(isPresented: $showJumpModal) {
            JumpToReelModalView(
                totalCount: pool.currentItems.count,
                currentNumber: activeIndex + 1,
                onJump: { targetIndex in
                    jumpToReel(at: targetIndex)
                }
            )
        }
        .sheet(isPresented: $showGridSheet) {
            GridView(
                reels: pool.currentItems,
                currentIndex: activeIndex,
                watchedReelIDs: watchedReelIDs,
                onSelectReel: { targetIndex in
                    jumpToReel(at: targetIndex)
                }
            )
        }
        .sheet(isPresented: $showDownloadSheet) {
            if let m = manifest {
                DownloadAllSheet(reels: m.items, weekID: m.weekId)
            }
        }
        .sheet(isPresented: $showBookmarksSheet) {
            BookmarksSheet()
        }
        .sheet(isPresented: $showShareSheet) {
            ActivityViewController(activityItems: shareItems)
        }
        .task {
            loadManifest()
        }
        .onAppear {
            setupPoolCallbacks()
        }
    }

    // MARK: - Data Loading & Playback Setup

    private func loadManifest() {
        isLoading = true
        errorMessage = nil
        Task {
            do {
                let fetched = try await DigestDataService.shared.fetchManifest()
                self.manifest = fetched
                self.allReels = fetched.items
                self.isLoading = false

                // Check AppState for weekly rollover and purge stale week cache
                let stateDescriptor = FetchDescriptor<AppState>()
                if let appState = (try? self.modelContext.fetch(stateDescriptor))?.first {
                    if !appState.currentWeekID.isEmpty && appState.currentWeekID != fetched.weekId {
                        let oldWeek = appState.currentWeekID
                        appState.currentWeekID = fetched.weekId
                        do {
                            try self.modelContext.save()
                        } catch {
                            self.modelContext.rollback()
                        }
                        Task {
                            await MediaCacheManager.shared.purgeOldWeekDirectory(oldWeekID: oldWeek)
                        }
                    } else if appState.currentWeekID.isEmpty {
                        appState.currentWeekID = fetched.weekId
                        do {
                            try self.modelContext.save()
                        } catch {
                            self.modelContext.rollback()
                        }
                    }
                } else {
                    let newState = AppState(currentWeekID: fetched.weekId)
                    self.modelContext.insert(newState)
                    do {
                        try self.modelContext.save()
                    } catch {
                        self.modelContext.rollback()
                    }
                }

                if !fetched.items.isEmpty {
                    self.pool.setReels(fetched.items, weekID: fetched.weekId, startIndex: 0)
                }
                // Load historical watched state immediately after manifest arrives
                refreshWatchedReels()

                // Sync remote Cloudflare R2 bookmarks manifest (18 items)
                Task {
                    if let remoteBMs = try? await DigestDataService.shared.fetchRemoteBookmarks() {
                        await MediaCacheManager.shared.syncRemoteBookmarks(dtos: remoteBMs)
                    }
                }

                // Seed Mindful 50-reels modal in UI testing if requested
                if ProcessInfo.processInfo.arguments.contains("-ui-testing-seed-mindful") {
                    let todayStr = getTodayDateString()
                    let dailyDescriptor = FetchDescriptor<DailyProgress>(
                        predicate: #Predicate { $0.dateString == todayStr }
                    )
                    if let existing = (try? modelContext.fetch(dailyDescriptor))?.first {
                        existing.viewedCount = 50
                        existing.snoozeUntil = nil
                    } else {
                        let daily = DailyProgress(dateString: todayStr, viewedCount: 50, snoozeUntil: nil)
                        modelContext.insert(daily)
                    }
                    try? modelContext.save()
                    self.showMindfulModal = true
                }
            } catch {
                self.errorMessage = "Unable to connect to digest: \(error.localizedDescription)"
                self.isLoading = false
            }
        }
    }

    private func setupPoolCallbacks() {
        pool.onWatchedMilestone = { reel in
            recordWatched(reel: reel)
        }
    }

    // MARK: - Watched Rules Engine

    private func recordWatched(reel: ReelItem) {
        guard let weekID = manifest?.weekId else { return }

        let reelID = reel.id
        let compoundKey = "\(weekID)\u{1F}\(reelID)"
        let descriptor = FetchDescriptor<WatchedEvent>(
            predicate: #Predicate { $0.compoundKey == compoundKey }
        )

        if let existing = try? modelContext.fetch(descriptor), !existing.isEmpty {
            return
        }

        let event = WatchedEvent(reelID: reelID, weekID: weekID)
        modelContext.insert(event)
        watchedReelIDs.insert(reelID)

        let todayStr = getTodayDateString()
        let dailyDescriptor = FetchDescriptor<DailyProgress>(
            predicate: #Predicate { $0.dateString == todayStr }
        )

        let daily: DailyProgress
        if let existingDaily = try? modelContext.fetch(dailyDescriptor), let d = existingDaily.first {
            daily = d
            daily.viewedCount += 1
        } else {
            daily = DailyProgress(dateString: todayStr, viewedCount: 1)
            modelContext.insert(daily)
        }

        do {
            try modelContext.save()
        } catch {
            modelContext.rollback()
        }

        // Check Mindful 50-reels threshold
        if daily.viewedCount >= 50 {
            let isSnoozed: Bool
            if let snooze = daily.snoozeUntil {
                isSnoozed = Date() < snooze
            } else {
                isSnoozed = false
            }
            if !isSnoozed && !showMindfulModal {
                withAnimation {
                    showMindfulModal = true
                }
            }
        }
    }

    /// Jump-to-N: Marks all predecessors as watched in a single batch
    private func jumpToReel(at targetIndex: Int) {
        guard !pool.currentItems.isEmpty, targetIndex >= 0, targetIndex < pool.currentItems.count else { return }
        isCaptionExpanded = false
        let weekID = manifest?.weekId ?? "default_week"

        // 1. Single query for existing watched items in this week
        let descriptor = FetchDescriptor<WatchedEvent>(
            predicate: #Predicate { $0.weekID == weekID }
        )
        let alreadyWatched = Set((try? modelContext.fetch(descriptor))?.map { $0.reelID } ?? [])

        let unrecordedIDs = WatchedRules.unrecordedPredecessorIDs(
            items: pool.currentItems,
            targetIndex: targetIndex,
            alreadyWatched: alreadyWatched
        )

        for predID in unrecordedIDs {
            let event = WatchedEvent(reelID: predID, weekID: weekID)
            modelContext.insert(event)
            watchedReelIDs.insert(predID)
        }

        let unrecordedCount = unrecordedIDs.count

        if unrecordedCount > 0 {
            let todayStr = getTodayDateString()
            let dailyDescriptor = FetchDescriptor<DailyProgress>(
                predicate: #Predicate { $0.dateString == todayStr }
            )
            if let existingDaily = (try? modelContext.fetch(dailyDescriptor))?.first {
                existingDaily.viewedCount += unrecordedCount
            } else {
                let daily = DailyProgress(dateString: todayStr, viewedCount: unrecordedCount)
                modelContext.insert(daily)
            }
            do {
                try modelContext.save()
            } catch {
                modelContext.rollback()
            }
        }

        activeIndex = targetIndex
        pool.setCurrentIndex(targetIndex)
    }

    private func refreshWatchedReels() {
        guard let weekID = manifest?.weekId else { return }
        let descriptor = FetchDescriptor<WatchedEvent>(
            predicate: #Predicate { $0.weekID == weekID }
        )
        if let list = try? modelContext.fetch(descriptor) {
            self.watchedReelIDs = Set(list.map { $0.reelID })
        }
    }

    // MARK: - Bookmarks & Sharing

    private func toggleBookmarkCurrentReel() {
        guard !pool.currentItems.isEmpty, activeIndex >= 0, activeIndex < pool.currentItems.count else { return }
        let reel = pool.currentItems[activeIndex]
        let weekID = manifest?.weekId ?? "default_week"

        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.reelID == reel.id }
        )

        if let existing = try? modelContext.fetch(descriptor), let item = existing.first {
            let reelID = item.reelID
            modelContext.delete(item)
            do {
                try modelContext.save()
            } catch {
                modelContext.rollback()
            }

            Task {
                await MediaCacheManager.shared.deleteBookmarkFile(reelID: reelID)
                await MediaCacheManager.shared.reconcileBookmarkStorageLedger()
            }
            triggerBookmarkPopAnimation()
        } else {
            let isLocal = LibraryPathResolver.shared.isLocalFileAvailable(for: weekID, reelID: reel.id)
            let item = BookmarkItem(
                reelID: reel.id,
                weekID: weekID,
                creatorHandle: reel.creatorHandle,
                caption: reel.caption,
                rank: reel.rank,
                videoUrl: reel.videoUrl,
                thumbnailUrl: reel.thumbnailUrl,
                localStatus: isLocal ? .cached : .evicted,
                sizeBytes: reel.sizeBytes ?? 0
            )
            modelContext.insert(item)
            do {
                try modelContext.save()
            } catch {
                modelContext.rollback()
            }

            if isLocal {
                let rID = reel.id
                let wID = weekID
                let sBytes = reel.sizeBytes ?? 0
                Task {
                    _ = try? await MediaCacheManager.shared.keepBookmarkOffline(
                        weekID: wID,
                        reelID: rID,
                        fallbackSizeBytes: sBytes
                    )
                }
            }
            triggerBookmarkPopAnimation()
        }
    }

    private func triggerBookmarkPopAnimation() {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.6)) {
            showBookmarkPop = true
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            withAnimation(.easeOut(duration: 0.2)) {
                showBookmarkPop = false
            }
        }
    }

    private func triggerShareCurrentReel() {
        guard !pool.currentItems.isEmpty, activeIndex >= 0, activeIndex < pool.currentItems.count else { return }
        let reel = pool.currentItems[activeIndex]
        let weekID = manifest?.weekId ?? "default_week"

        let shareText = "Check out this reel by @\(reel.creatorHandle) on Instagram Digest: https://vkr1729.github.io/Instagram_digest/share/\(reel.id).html?v=3"
        // Encode as a single `text=` query value: ? & + must not leak through as delimiters
        let whatsappValueAllowed = CharacterSet.urlQueryAllowed.subtracting(CharacterSet(charactersIn: "?&+"))
        let encoded = shareText.addingPercentEncoding(withAllowedCharacters: whatsappValueAllowed) ?? ""

        // Try primary WhatsApp deep link
        if let waURL = URL(string: "whatsapp://send?text=\(encoded)"), UIApplication.shared.canOpenURL(waURL) {
            UIApplication.shared.open(waURL)
            return
        }

        // Fallback: System UIActivityViewController
        let localFile = LibraryPathResolver.shared.localFileURL(for: weekID, reelID: reel.id)
        if FileManager.default.fileExists(atPath: localFile.path) && !Reachability.isConnectedToNetwork() {
            shareItems = [shareText, localFile]
        } else {
            shareItems = [shareText, reel.videoUrl]
        }
        showShareSheet = true
    }

    // MARK: - Mindful Snooze & Date Helpers

    private func getTodayDateString() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        return formatter.string(from: Date())
    }

    private func getTodayViewedCount() -> Int {
        let todayStr = getTodayDateString()
        let descriptor = FetchDescriptor<DailyProgress>(
            predicate: #Predicate { $0.dateString == todayStr }
        )
        return (try? modelContext.fetch(descriptor))?.first?.viewedCount ?? 50
    }

    private func snoozeMindfulModalForToday() {
        let todayStr = getTodayDateString()
        let descriptor = FetchDescriptor<DailyProgress>(
            predicate: #Predicate { $0.dateString == todayStr }
        )
        if let daily = (try? modelContext.fetch(descriptor))?.first {
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = .current
            if let endOfDay = calendar.date(bySettingHour: 23, minute: 59, second: 59, of: Date()) {
                daily.snoozeUntil = endOfDay
                do {
                    try modelContext.save()
                } catch {
                    modelContext.rollback()
                }
            }
        }
    }
}

/// Network reachability helper backed by NWPathMonitor
public final class Reachability: @unchecked Sendable {
    public static let shared = Reachability()
    private let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "ReachabilityQueue")
    public private(set) var isConnected: Bool = true

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            self?.isConnected = (path.status == .satisfied)
        }
        monitor.start(queue: queue)
    }

    public static func isConnectedToNetwork() -> Bool {
        shared.isConnected
    }
}

/// UIActivityViewController wrapper for system share sheet
struct ActivityViewController: UIViewControllerRepresentable {
    let activityItems: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: activityItems, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
