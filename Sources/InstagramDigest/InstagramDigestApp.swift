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
                let daily = DailyProgress(dateString: todayStr, viewedCount: MindfulDailyModalView.dailyLimit, snoozeUntil: nil)
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
/// Hosts cursive Instagram brand logo, Grid, Download, Bookmarks chip,
/// 7-story category circles bar, uncropped video pager, bottom HUD with creator handle,
/// WhatsApp share, Gold bookmark button, 2-line caption, and auto-immersive playback.
/// Grid selection and last-reel resume replace the retired Jump-to-N pill.
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
    @State private var showMindfulModal: Bool = ProcessInfo.processInfo.arguments.contains("-ui-testing-seed-mindful")
    // Set while a category switch resets activeIndex to 0 so the transient
    // reset is not persisted as the user's resume position.
    @State private var suppressNextResumeSave: Bool = false
    // Captured when a sheet pauses playback so dismiss without selection can
    // resume instead of stranding the feed paused.
    @State private var wasPlayingBeforeSheet: Bool = false
    @State private var showOwnerKeyAlert: Bool = false
    @State private var ownerKeyInput: String = ""
    @State private var activeBookmarkTasks: [String: Task<Void, Never>] = [:]

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
                        saveLastActiveReel(index: newIndex)
                        pool.setCurrentIndex(newIndex)
                        if !ProcessInfo.processInfo.arguments.contains("-ui-testing") {
                            withAnimation(.easeInOut(duration: 0.25)) {
                                isChromeVisible = !pool.isPlaying
                            }
                        }
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
                            return
                        }
                        pool.togglePlayPause()
                        if !ProcessInfo.processInfo.arguments.contains("-ui-testing") {
                            withAnimation(.easeInOut(duration: 0.25)) {
                                isChromeVisible = !pool.isPlaying
                            }
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
                    onTriggerBookmark: {
                        toggleBookmarkCurrentReel()
                    },
                    onTriggerShare: {
                        triggerShareCurrentReel()
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
                    isChromeVisible: isChromeVisible,
                    seekFractionPreview: seekPreviewFraction,
                    progress: pool.currentProgress,
                    duration: pool.currentDuration,
                    currentTime: pool.currentTime,
                    showBookmarkPop: showBookmarkPop,
                    isCaptionExpanded: isCaptionExpanded,
                    onTogglePlayPause: {
                        pool.togglePlayPause()
                        if !ProcessInfo.processInfo.arguments.contains("-ui-testing") {
                            withAnimation(.easeInOut(duration: 0.25)) {
                                isChromeVisible = !pool.isPlaying
                            }
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

                // 3. Top Navigation Header & Story Category Circles Bar (Mock 2 Locked)
                VStack(spacing: 0) {
                    HeaderBarView(
                        currentIndex: activeIndex,
                        totalCount: pool.currentItems.count,
                        bookmarkCount: savedBookmarks.count,
                        onTapGrid: {
                            wasPlayingBeforeSheet = pool.isPlaying
                            pool.pause()
                            showGridSheet = true
                        },
                        onTapOffline: {
                            wasPlayingBeforeSheet = pool.isPlaying
                            pool.pause()
                            showDownloadSheet = true
                        },
                        onTapBookmarks: {
                            wasPlayingBeforeSheet = pool.isPlaying
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
                            // The reset to 0 is a view-filter artifact, not a
                            // watched position: suppress its resume persist so
                            // it cannot overwrite the saved full-list position.
                            suppressNextResumeSave = true
                            activeIndex = 0
                        }
                    )

                    Spacer()
                }
                .opacity(isChromeVisible ? 1.0 : 0.0)
                .allowsHitTesting(isChromeVisible)
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
        .onChange(of: activeIndex) { _, newIndex in
            if suppressNextResumeSave {
                suppressNextResumeSave = false
                return
            }
            saveLastActiveReel(index: newIndex)
        }
        .onChange(of: showGridSheet) { _, isPresented in
            resumeFeedAfterSheet(isPresented: isPresented)
        }
        .onChange(of: showDownloadSheet) { _, isPresented in
            resumeFeedAfterSheet(isPresented: isPresented)
        }
        .onChange(of: showBookmarksSheet) { _, isPresented in
            resumeFeedAfterSheet(isPresented: isPresented)
        }
        .onChange(of: pool.isPlaying) { _, isPlaying in
            guard !ProcessInfo.processInfo.arguments.contains("-ui-testing") else { return }
            withAnimation(.easeInOut(duration: 0.25)) {
                isChromeVisible = !isPlaying
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
            saveLastActiveReel(index: activeIndex)
        }
        .task {
            loadManifest()
        }
        .onAppear {
            setupPoolCallbacks()
        }
        .alert("Link Cloudflare Owner Key", isPresented: $showOwnerKeyAlert) {
            TextField("Enter Owner Key", text: $ownerKeyInput)
            Button("Save & Sync") {
                let trimmed = ownerKeyInput.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    UserDefaults.standard.set(trimmed, forKey: "digest_owner_key")
                    if !pool.currentItems.isEmpty, activeIndex >= 0, activeIndex < pool.currentItems.count {
                        let reel = pool.currentItems[activeIndex]
                        Task {
                            _ = try? await DigestDataService.shared.saveRemoteBookmark(reel: reel)
                        }
                    }
                }
            }
            Button("Later", role: .cancel) {}
        } message: {
            Text("Enter your Cloudflare Worker OWNER_KEY to forward bookmarked reels directly to your private Telegram chat.")
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
                let appState = (try? self.modelContext.fetch(stateDescriptor))?.first
                var resumeIndex = 0
                var isNewWeek = false

                if let state = appState {
                    if !state.currentWeekID.isEmpty && state.currentWeekID != fetched.weekId {
                        isNewWeek = true
                        let oldWeek = state.currentWeekID
                        state.currentWeekID = fetched.weekId
                        state.lastActiveReelID = nil
                        do {
                            try self.modelContext.save()
                        } catch {
                            self.modelContext.rollback()
                        }
                        UserDefaults.standard.removeObject(forKey: "lastActiveReelID_\(oldWeek)")
                        UserDefaults.standard.removeObject(forKey: "lastActiveIndex_\(oldWeek)")
                        UserDefaults.standard.removeObject(forKey: "lastActiveReelID_global")
                        UserDefaults.standard.removeObject(forKey: "lastActiveIndex_global")
                        Task {
                            await MediaCacheManager.shared.purgeOldWeekDirectory(oldWeekID: oldWeek)
                        }
                    } else {
                        if state.currentWeekID.isEmpty {
                            state.currentWeekID = fetched.weekId
                            do {
                                try self.modelContext.save()
                            } catch {
                                self.modelContext.rollback()
                            }
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

                if isNewWeek {
                    // Reset watch history on weekly refresh: delete previous week's watched events
                    let allWatchedDescriptor = FetchDescriptor<WatchedEvent>()
                    if let oldEvents = try? self.modelContext.fetch(allWatchedDescriptor) {
                        for ev in oldEvents where ev.weekID != fetched.weekId {
                            self.modelContext.delete(ev)
                        }
                        try? self.modelContext.save()
                    }
                    self.watchedReelIDs.removeAll()
                    resumeIndex = 0
                } else {
                    let savedID = appState?.lastActiveReelID
                        ?? UserDefaults.standard.string(forKey: "lastActiveReelID_\(fetched.weekId)")
                        ?? UserDefaults.standard.string(forKey: "lastActiveReelID_global")
                    let savedIdx = (UserDefaults.standard.object(forKey: "lastActiveIndex_\(fetched.weekId)") as? Int)
                        ?? (UserDefaults.standard.object(forKey: "lastActiveIndex_global") as? Int)

                    resumeIndex = WatchedRules.resolveResumeIndex(
                        currentWeekID: fetched.weekId,
                        previousWeekID: appState?.currentWeekID,
                        savedReelID: savedID,
                        savedIndex: savedIdx,
                        items: fetched.items
                    )
                }

                self.activeIndex = resumeIndex
                if !fetched.items.isEmpty {
                    self.pool.setReels(fetched.items, weekID: fetched.weekId, startIndex: resumeIndex)
                    if !ProcessInfo.processInfo.arguments.contains("-ui-testing") {
                        withAnimation(.easeInOut(duration: 0.25)) {
                            self.isChromeVisible = !self.pool.isPlaying
                        }
                    }
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
                        existing.viewedCount = MindfulDailyModalView.dailyLimit
                        existing.snoozeUntil = nil

                    } else {
                        let daily = DailyProgress(dateString: todayStr, viewedCount: MindfulDailyModalView.dailyLimit, snoozeUntil: nil)
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
        pool.onAutoAdvanceToNext = {
            // Read the index from the pool (source of truth), not captured view
            // state, so rapid scrolls between setup and fire can't target stale N+1.
            let nextIndex = AVPlayerPool.shared.currentIndex + 1
            if nextIndex < pool.currentItems.count {
                jumpToReel(at: nextIndex)
            }
        }
    }

    private func saveLastActiveReel(index: Int) {
        guard !pool.currentItems.isEmpty, index >= 0, index < pool.currentItems.count else { return }
        let reel = pool.currentItems[index]
        guard let weekID = manifest?.weekId else { return }

        let stateDescriptor = FetchDescriptor<AppState>()
        if let appState = (try? modelContext.fetch(stateDescriptor))?.first {
            appState.lastActiveReelID = reel.id
            try? modelContext.save()
        }
        UserDefaults.standard.set(reel.id, forKey: "lastActiveReelID_\(weekID)")
        UserDefaults.standard.set(index, forKey: "lastActiveIndex_\(weekID)")
        UserDefaults.standard.set(reel.id, forKey: "lastActiveReelID_global")
        UserDefaults.standard.set(index, forKey: "lastActiveIndex_global")
    }

    /// Resumes feed playback after a sheet is dismissed without selection.
    /// Grid selection already resumes via jumpToReel; this covers Done/Close.
    private func resumeFeedAfterSheet(isPresented: Bool) {
        guard !isPresented else { return }
        guard wasPlayingBeforeSheet else { return }
        wasPlayingBeforeSheet = false
        // All three sheets are mutually exclusive, so any dismiss resumes.
        if !showGridSheet && !showDownloadSheet && !showBookmarksSheet {
            pool.play()
            if !ProcessInfo.processInfo.arguments.contains("-ui-testing") {
                withAnimation(.easeInOut(duration: 0.25)) {
                    isChromeVisible = false
                }
            }
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

        // Check Mindful daily-reels threshold (single product constant)
        if daily.viewedCount >= MindfulDailyModalView.dailyLimit {
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
        saveLastActiveReel(index: targetIndex)
        pool.setCurrentIndex(targetIndex)
        wasPlayingBeforeSheet = true
        if !ProcessInfo.processInfo.arguments.contains("-ui-testing") {
            withAnimation(.easeInOut(duration: 0.25)) {
                isChromeVisible = false
            }
        }
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

            activeBookmarkTasks[reelID]?.cancel()
            activeBookmarkTasks[reelID] = Task {
                await MediaCacheManager.shared.deleteBookmarkFile(reelID: reelID)
                await MediaCacheManager.shared.reconcileBookmarkStorageLedger()
                guard !Task.isCancelled else { return }
                _ = try? await DigestDataService.shared.deleteRemoteBookmark(reelID: reelID)
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

            let rID = reel.id
            let wID = weekID
            let sBytes = reel.sizeBytes ?? 0
            activeBookmarkTasks[rID]?.cancel()
            activeBookmarkTasks[rID] = Task {
                if isLocal {
                    _ = try? await MediaCacheManager.shared.keepBookmarkOffline(
                        weekID: wID,
                        reelID: rID,
                        fallbackSizeBytes: sBytes
                    )
                }
                guard !Task.isCancelled else { return }
                // Remote Cloudflare Worker sync -> Telegram forwarding
                _ = try? await DigestDataService.shared.saveRemoteBookmark(reel: reel)
            }
            triggerBookmarkPopAnimation()

            // Prompt user if owner key is not yet configured on this device
            let currentKey = UserDefaults.standard.string(forKey: "digest_owner_key") ?? ""
            if currentKey.isEmpty {
                ownerKeyInput = ""
                showOwnerKeyAlert = true
            }
        }
    }

    @State private var bookmarkPopGeneration: UInt64 = 0

    private func triggerBookmarkPopAnimation() {
        bookmarkPopGeneration &+= 1
        let generation = bookmarkPopGeneration
        withAnimation(.spring(response: 0.3, dampingFraction: 0.6)) {
            showBookmarkPop = true
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            // A rapid second toggle must not be hidden early by the first timer.
            guard generation == bookmarkPopGeneration else { return }
            withAnimation(.easeOut(duration: 0.2)) {
                showBookmarkPop = false
            }
        }
    }

    private func triggerShareCurrentReel() {
        guard !pool.currentItems.isEmpty, activeIndex >= 0, activeIndex < pool.currentItems.count else { return }
        let reel = pool.currentItems[activeIndex]
        let weekID = manifest?.weekId ?? "default_week"

        let caption = reel.caption.trimmingCharacters(in: .whitespacesAndNewlines)
        let shareCaption = caption.isEmpty ? "Reel by @\(reel.creatorHandle)" : caption

        // Prefer any on-disk copy (feed week directory OR isolated bookmark
        // directory) so the share sheet attaches the real .mp4, not just a link.
        if let resolved = LibraryPathResolver.shared.resolvedLocalFileURL(for: weekID, reelID: reel.id),
           FileManager.default.fileExists(atPath: resolved.path) {
            ShareSheetPresenter.present(items: [resolved, shareCaption])
            return
        }

        let tempFile = FileManager.default.temporaryDirectory.appendingPathComponent("\(reel.id).mp4")
        if FileManager.default.fileExists(atPath: tempFile.path) {
            ShareSheetPresenter.present(items: [tempFile, shareCaption])
            return
        }

        // Asynchronously download video to temp file, then present share sheet with video attached
        Task {
            do {
                let (downloadedURL, _) = try await URLSession.shared.download(from: reel.videoUrl)
                if FileManager.default.fileExists(atPath: tempFile.path) {
                    try? FileManager.default.removeItem(at: tempFile)
                }
                try FileManager.default.moveItem(at: downloadedURL, to: tempFile)
                await MainActor.run {
                    ShareSheetPresenter.present(items: [tempFile, shareCaption])
                }
            } catch {
                // Fallback to video URL + caption if download fails
                await MainActor.run {
                    ShareSheetPresenter.present(items: [reel.videoUrl, shareCaption])
                }
            }
        }
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
        // No row yet today means zero reels watched — never a pre-seeded limit.
        return (try? modelContext.fetch(descriptor))?.first?.viewedCount ?? 0
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
    private let lock = NSLock()
    private var _isConnected: Bool = true
    public var isConnected: Bool {
        lock.lock()
        defer { lock.unlock() }
        return _isConnected
    }

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            guard let self = self else { return }
            self.lock.lock()
            self._isConnected = (path.status == .satisfied)
            self.lock.unlock()
        }
        monitor.start(queue: queue)
    }

    public static func isConnectedToNetwork() -> Bool {
        shared.isConnected
    }
}

/// Native system share sheet presenter that invokes UIActivityViewController directly from the topmost UIViewController,
/// avoiding SwiftUI .sheet nested presentation bugs that result in blank action sheets.
@MainActor
public enum ShareSheetPresenter {
    public static func present(items: [Any]) {
        guard let scene = UIApplication.shared.connectedScenes.first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene,
              let window = scene.windows.first(where: { $0.isKeyWindow }) ?? scene.windows.first,
              let rootVC = window.rootViewController else { return }

        var topVC = rootVC
        while let presented = topVC.presentedViewController {
            topVC = presented
        }

        // Re-entrancy guard: a rapid double-tap on Share must not attempt a
        // second present over the already-presented activity sheet (UIKit
        // logs "Attempt to present ... while already presenting" and drops
        // the second sheet).
        if topVC is UIActivityViewController || topVC.presentedViewController is UIActivityViewController {
            return
        }

        let activityVC = UIActivityViewController(activityItems: items, applicationActivities: nil)
        if let popover = activityVC.popoverPresentationController {
            popover.sourceView = topVC.view
            popover.sourceRect = CGRect(x: topVC.view.bounds.midX, y: topVC.view.bounds.midY, width: 0, height: 0)
            popover.permittedArrowDirections = []
        }
        topVC.present(activityVC, animated: true)
    }
}


