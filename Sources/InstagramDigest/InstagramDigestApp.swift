import SwiftUI
import SwiftData
import AVFoundation

@main
struct InstagramDigestApp: App {
    let modelContainer: ModelContainer

    init() {
        do {
            let schema = Schema([
                WatchedEvent.self,
                BookmarkItem.self,
                DailyProgress.self,
                AppState.self
            ])
            let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)
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

/// Main Feed screen hosting header navigation, feed pager, gesture overlays, and modals.
struct FeedMainView: View {
    @Environment(\.modelContext) private var modelContext
    @ObservedObject private var pool = AVPlayerPool.shared

    @State private var manifest: DigestManifest?
    @State private var isLoading: Bool = true
    @State private var errorMessage: String?

    @State private var activeIndex: Int = 0
    @State private var lastScrollEndTime: TimeInterval = 0
    @State private var seekPreviewFraction: Double? = nil
    @State private var showBookmarkPop: Bool = false

    // Modals state
    @State private var showGridSheet: Bool = false
    @State private var showDownloadSheet: Bool = false
    @State private var showBookmarksSheet: Bool = false
    @State private var showMindfulModal: Bool = false
    @State private var showShareSheet: Bool = false
    @State private var shareItems: [Any] = []

    // Watched reels and bookmarks set for instant HUD reflection
    @State private var watchedReelIDs: Set<String> = []
    @State private var bookmarkedReelIDs: Set<String> = []

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
            } else if let items = manifest?.items, !items.isEmpty {
                let currentReel = items[activeIndex]
                let isCurrentBookmarked = bookmarkedReelIDs.contains(currentReel.id)

                // 1. Vertical Pager Container (UICollectionView with modern CellRegistration)
                FeedPagerView(
                    reels: items,
                    currentIndex: $activeIndex,
                    onPageChanged: { newIndex in
                        pool.setCurrentIndex(newIndex)
                    },
                    onScrollEnded: { uptime in
                        lastScrollEndTime = uptime
                    },
                    onForwardScrollPast: { departedReel in
                        recordWatched(reel: departedReel)
                    }
                )
                .ignoresSafeArea()

                // 2. Full-Bleed Live Overlay (Seek HUD, Progress, Pills, Bookmark Pop)
                ReelCardOverlayView(
                    reel: currentReel,
                    isLatched2x: pool.isLatched2x,
                    isBookmarked: isCurrentBookmarked,
                    seekFractionPreview: seekPreviewFraction,
                    progress: pool.currentProgress,
                    duration: pool.currentDuration,
                    currentTime: pool.currentTime,
                    showBookmarkPop: showBookmarkPop
                )
                .ignoresSafeArea()
                .allowsHitTesting(false) // Passes gestures to overlay below

                // 3. Gesture Overlay Stack
                FeedGestureOverlay(
                    lastScrollEndTime: lastScrollEndTime,
                    currentProgress: pool.currentProgress,
                    onTogglePlayPause: {
                        if pool.isLatched2x {
                            pool.setLatched2x(false)
                        }
                        pool.togglePlayPause()
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
                    onTriggerBookmark: {
                        toggleBookmarkCurrentReel()
                    },
                    onTriggerShare: {
                        triggerShareCurrentReel()
                    }
                )
                .ignoresSafeArea()

                // 4. Top Navigation Header
                VStack {
                    HStack(spacing: 12) {
                        // Playback Speed Cycler Pill
                        Button {
                            pool.cyclePlaybackRate()
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "gauge.with.dots.needle.50percent")
                                    .font(.system(size: 11))
                                Text(String(format: "%.2fx", pool.effectiveRate))
                                    .font(.system(size: 13, weight: .bold, design: .monospaced))
                            }
                            .foregroundColor(pool.isLatched2x ? .yellow : .white)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(.ultraThinMaterial)
                            .clipShape(Capsule())
                        }

                        Spacer()

                        // Jump Grid Button
                        Button {
                            showGridSheet = true
                        } label: {
                            Image(systemName: "square.grid.3x3.fill")
                                .font(.system(size: 15))
                                .foregroundColor(.white)
                                .padding(8)
                                .background(.ultraThinMaterial)
                                .clipShape(Circle())
                        }

                        // Download All Button
                        Button {
                            showDownloadSheet = true
                        } label: {
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.system(size: 15))
                                .foregroundColor(.white)
                                .padding(8)
                                .background(.ultraThinMaterial)
                                .clipShape(Circle())
                        }

                        // Bookmarks Button
                        Button {
                            showBookmarksSheet = true
                        } label: {
                            Image(systemName: "bookmark.fill")
                                .font(.system(size: 15))
                                .foregroundColor(.white)
                                .padding(8)
                                .background(.ultraThinMaterial)
                                .clipShape(Circle())
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 10)

                    Spacer()
                }

                // 5. Mindful 50-reels Modal Overlay
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
        }
        .sheet(isPresented: $showGridSheet) {
            if let items = manifest?.items {
                GridView(
                    reels: items,
                    currentIndex: activeIndex,
                    watchedReelIDs: watchedReelIDs,
                    onSelectReel: { targetIndex in
                        jumpToReel(at: targetIndex)
                    }
                )
            }
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
                refreshBookmarks()
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

    /// Jump-to-N: Marks all predecessors (0 through targetIndex - 1) as watched in a single batch
    private func jumpToReel(at targetIndex: Int) {
        guard let items = manifest?.items, targetIndex >= 0, targetIndex < items.count else { return }
        let weekID = manifest?.weekId ?? "default_week"

        // 1. Single query for existing watched items in this week
        let descriptor = FetchDescriptor<WatchedEvent>(
            predicate: #Predicate { $0.weekID == weekID }
        )
        let alreadyWatched = Set((try? modelContext.fetch(descriptor))?.map { $0.reelID } ?? [])

        var unrecordedCount = 0
        for i in 0..<targetIndex {
            let predID = items[i].id
            if !alreadyWatched.contains(predID) {
                let event = WatchedEvent(reelID: predID, weekID: weekID)
                modelContext.insert(event)
                watchedReelIDs.insert(predID)
                unrecordedCount += 1
            }
        }

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
            // Single batched save for all predecessors with rollback protection
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

    private func refreshBookmarks() {
        let descriptor = FetchDescriptor<BookmarkItem>()
        if let list = try? modelContext.fetch(descriptor) {
            self.bookmarkedReelIDs = Set(list.map { $0.reelID })
        }
    }

    // MARK: - Bookmarks & Sharing

    private func toggleBookmarkCurrentReel() {
        guard let items = manifest?.items, activeIndex >= 0, activeIndex < items.count else { return }
        let reel = items[activeIndex]
        let weekID = manifest?.weekId ?? "default_week"

        let descriptor = FetchDescriptor<BookmarkItem>(
            predicate: #Predicate { $0.reelID == reel.id }
        )

        if let existing = try? modelContext.fetch(descriptor), let item = existing.first {
            // Unbookmark: delete row and delete physical file via actor (avoids orphaned files)
            let reelID = item.reelID
            modelContext.delete(item)
            do {
                try modelContext.save()
            } catch {
                modelContext.rollback()
            }
            bookmarkedReelIDs.remove(reelID)

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
            bookmarkedReelIDs.insert(reel.id)

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
        guard let items = manifest?.items, activeIndex >= 0, activeIndex < items.count else { return }
        let reel = items[activeIndex]
        let weekID = manifest?.weekId ?? "default_week"

        let text = "Check out this reel by @\(reel.creatorHandle) from Instagram Digest"

        // Clause: local file shared only when fully offline and available
        let localFile = LibraryPathResolver.shared.localFileURL(for: weekID, reelID: reel.id)
        if FileManager.default.fileExists(atPath: localFile.path) && !Reachability.isConnectedToNetwork() {
            shareItems = [text, localFile]
        } else {
            shareItems = [text, reel.videoUrl]
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

/// Simple reachability helper for offline detection
enum Reachability {
    static func isConnectedToNetwork() -> Bool {
        // Assume connected on modern iOS unless explicitly in airplane mode
        true
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
