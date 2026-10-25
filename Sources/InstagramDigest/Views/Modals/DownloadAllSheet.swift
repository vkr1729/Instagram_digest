import SwiftUI
import SwiftData

/// Dismissible bulk download manager sheet with device storage preflight and adaptive concurrency status.
public struct DownloadAllSheet: View {
    @StateObject private var coordinator = DownloadAllCoordinator.shared
    public let reels: [ReelItem]
    public let weekID: String
    public let generatedAt: Date?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var modelContext

    @State private var preflightResult: (isSufficient: Bool, requiredBytes: Int64, availableBytes: Int64)?
    @State private var unwatchedOnly: Bool = true
    @State private var watchedIDs: Set<String> = []
    @State private var census: (cached: Int, total: Int)? = nil
    @State private var cachedPendingIDs: Set<String> = []

    public init(reels: [ReelItem], weekID: String, generatedAt: Date? = nil) {
        self.reels = reels
        self.weekID = weekID
        self.generatedAt = generatedAt
        self._cachedPendingIDs = State(initialValue: Set(reels.map { $0.id }))
    }

    public var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                VStack(spacing: 24) {
                    // Header Icon
                    ZStack {
                        Circle()
                            .fill(Color.white.opacity(0.1))
                            .frame(width: 80, height: 80)

                        Image(systemName: "arrow.down.circle.fill")
                            .font(.system(size: 40))
                            .foregroundColor(.cyan)
                    }
                    .padding(.top, 16)

                    // Title & Description
                    VStack(spacing: 8) {
                        Text("Download Digest")
                            .font(.system(size: 22, weight: .bold))
                            .foregroundColor(.white)

                        Text("Download the complete weekly digest for seamless offline playback.")
                            .font(.system(size: 14))
                            .foregroundColor(.white.opacity(0.7))
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 24)
                    }

                    // Storage Preflight Gauge
                    if let preflight = preflightResult {
                        VStack(spacing: 8) {
                            HStack {
                                Text("Storage Check")
                                    .font(.system(size: 13, weight: .semibold))
                                    .foregroundColor(.white.opacity(0.8))
                                Spacer()
                                Text(preflight.isSufficient ? "Sufficient" : "Insufficient")
                                    .font(.system(size: 13, weight: .bold))
                                    .foregroundColor(preflight.isSufficient ? .green : .red)
                            }

                            HStack {
                                Text("Required: \(formatMB(preflight.requiredBytes))")
                                    .font(.system(size: 12))
                                    .foregroundColor(.white.opacity(0.6))
                                Spacer()
                                Text("Available: \(formatMB(preflight.availableBytes))")
                                    .font(.system(size: 12))
                                    .foregroundColor(.white.opacity(0.6))
                            }
                        }
                        .padding(16)
                        .background(Color.white.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                        .padding(.horizontal, 20)
                    }

                    // Download Progress Section
                    // Rec 5: skip already-watched reels (default on). Cuts the
                    // batch roughly in half on typical weeks with one toggle.
                    if coordinator.state == .idle, !watchedIDs.isEmpty {
                        Toggle(isOn: $unwatchedOnly) {
                            Text("Download unwatched only (\(effectiveReels.count) of \(reels.count))")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundColor(.white.opacity(0.85))
                        }
                        .tint(.cyan)
                        .padding(.horizontal, 20)
                        .accessibilityIdentifier("UnwatchedOnlyToggle")
                    }
                    VStack(spacing: 12) {
                        switch coordinator.state {
                        case .idle:
                            if isAllDownloaded {
                                HStack {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundColor(.green)
                                    Text("All reels downloaded and ready offline!")
                                        .font(.system(size: 15, weight: .semibold))
                                        .foregroundColor(.white)
                                }
                            } else if pendingReels.isEmpty {
                                HStack(spacing: 8) {
                                    Image(systemName: "line.3.horizontal.decrease.circle")
                                        .foregroundColor(.cyan)
                                    Text("Nothing pending under current filter (\(census?.cached ?? 0)/\(census?.total ?? 0) cached on disk)")
                                        .font(.system(size: 14, weight: .medium))
                                        .foregroundColor(.white.opacity(0.85))
                                }
                            } else {
                                Text("\(pendingReels.count) reels ready to download (\(census?.cached ?? (reels.count - pendingReels.count)) already cached)")
                                    .font(.system(size: 15, weight: .medium))
                                    .foregroundColor(.white.opacity(0.8))
                            }

                        case .preparing:
                            HStack(spacing: 8) {
                                ProgressView().tint(.cyan)
                                Text("Preparing storage & rollover...")
                                    .font(.system(size: 15, weight: .medium))
                                    .foregroundColor(.cyan)
                            }

                        case .downloading(let completed, let total, let currentReel):
                            VStack(spacing: 8) {
                                ProgressView(value: coordinator.overallProgress)
                                    .tint(.cyan)

                                HStack {
                                    Text("Downloaded \(completed) of \(total)")
                                        .font(.system(size: 13, weight: .semibold))
                                        .foregroundColor(.white)
                                    if coordinator.failedInBatch > 0 {
                                        Text("(\(coordinator.failedInBatch) failed)")
                                            .font(.system(size: 11, weight: .semibold))
                                            .foregroundColor(.orange)
                                    }
                                    Spacer()
                                    Text("\(Int(coordinator.overallProgress * 100))%")
                                        .font(.system(size: 13, weight: .bold, design: .monospaced))
                                        .foregroundColor(.cyan)
                                }

                                HStack {
                                    if let reelID = currentReel {
                                        Text("Active: \(reelID)")
                                            .font(.system(size: 11))
                                            .foregroundColor(.white.opacity(0.5))
                                    }
                                    Spacer()
                                    // Concurrency speed badge
                                    Text(coordinator.activeConcurrency == 1 ? "1x (Playback active)" : "3x (High speed)")
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundColor(coordinator.activeConcurrency == 1 ? .yellow : .green)
                                }
                            }

                        case .completed:
                            HStack {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundColor(.green)
                                Text("All reels downloaded and ready offline!")
                                    .font(.system(size: 15, weight: .semibold))
                                    .foregroundColor(.white)
                            }

                        case .completedWithFailures(let ready, let failed):
                            HStack {
                                Image(systemName: "exclamationmark.circle.fill")
                                    .foregroundColor(.orange)
                                Text("Cached \(ready) of \(ready + failed) reels (\(failed) failed)")
                                    .font(.system(size: 15, weight: .semibold))
                                    .foregroundColor(.white)
                            }

                        case .paused:
                            Text("Downloads paused")
                                .font(.system(size: 15, weight: .medium))
                                .foregroundColor(.yellow)

                        case .failed(let reason):
                            Text(reason)
                                .font(.system(size: 14))
                                .foregroundColor(.red)
                                .multilineTextAlignment(.center)
                        }
                    }
                    .padding(20)
                    .background(Color.white.opacity(0.06))
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .padding(.horizontal, 20)

                    Spacer()

                    // Action Buttons
                    VStack(spacing: 12) {
                        switch coordinator.state {
                        case .idle:
                            if isAllDownloaded || pendingReels.isEmpty {
                                Button {
                                    dismiss()
                                } label: {
                                    Text("Done")
                                        .font(.system(size: 16, weight: .bold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 16)
                                        .background(Color.white)
                                        .foregroundColor(.black)
                                        .clipShape(RoundedRectangle(cornerRadius: 14))
                                }
                                .accessibilityIdentifier("DownloadDoneButton")
                            } else {
                                Button {
                                    coordinator.resetIfFinished()
                                    coordinator.startDownloadAll(
                                        reels: pendingReels, weekID: weekID,
                                        excludeWatchedIDs: unwatchedOnly ? watchedIDs : [],
                                        generatedAt: generatedAt)
                                } label: {
                                    Text("Start Download")
                                        .font(.system(size: 16, weight: .bold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 16)
                                        .background(Color.white)
                                        .foregroundColor(Color.black)
                                        .clipShape(RoundedRectangle(cornerRadius: 14))
                                }
                                .disabled(pendingReels.isEmpty)
                                .accessibilityIdentifier("StartDownloadButton")
                            }

                        case .preparing:
                            Button {} label: {
                                ProgressView()
                                    .tint(.black)
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 16)
                                    .background(Color.white.opacity(0.8))
                                    .clipShape(RoundedRectangle(cornerRadius: 14))
                            }
                            .disabled(true)

                        case .downloading:
                            HStack(spacing: 12) {
                                Button {
                                    if coordinator.isSuspended {
                                        coordinator.resumeQueue()
                                    } else {
                                        coordinator.suspendQueue()
                                    }
                                } label: {
                                    Text(coordinator.isSuspended ? "Resume" : "Pause")
                                        .font(.system(size: 15, weight: .semibold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 14)
                                        .background(Color.white.opacity(0.15))
                                        .foregroundColor(.white)
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                }

                                Button {
                                    coordinator.cancelAll()
                                } label: {
                                    Text("Cancel")
                                        .font(.system(size: 15, weight: .semibold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 14)
                                        .background(Color.red.opacity(0.2))
                                        .foregroundColor(.red)
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                }
                            }

                        case .completed:
                            Button {
                                dismiss()
                            } label: {
                                Text("Done")
                                    .font(.system(size: 16, weight: .bold))
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 16)
                                    .background(Color.white)
                                    .foregroundColor(.black)
                                    .clipShape(RoundedRectangle(cornerRadius: 14))
                            }
                            .accessibilityIdentifier("DownloadDoneButton")

                        case .completedWithFailures:
                            VStack(spacing: 12) {
                                Button {
                                    coordinator.retryFailedItems(reels: effectiveReels, weekID: weekID)
                                } label: {
                                    Text("Retry Failed")
                                        .font(.system(size: 16, weight: .bold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 16)
                                        .background(effectiveReels.isEmpty ? Color.white.opacity(0.3) : Color.white)
                                        .foregroundColor(effectiveReels.isEmpty ? Color.black.opacity(0.5) : Color.black)
                                        .clipShape(RoundedRectangle(cornerRadius: 14))
                                }
                                .disabled(effectiveReels.isEmpty)
                                .accessibilityIdentifier("DownloadRetryButton")

                                Button {
                                    dismiss()
                                } label: {
                                    Text("Done")
                                        .font(.system(size: 15, weight: .semibold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 14)
                                        .background(Color.white.opacity(0.15))
                                        .foregroundColor(.white)
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                }
                                .accessibilityIdentifier("DownloadDoneButton")
                            }

                        case .paused:
                            // B9: pause used to strand the sheet on Done with
                            // no way back. Paused gets its own Resume + Cancel.
                            VStack(spacing: 12) {
                                Button {
                                    coordinator.resumeQueue()
                                } label: {
                                    Text("Resume")
                                        .font(.system(size: 15, weight: .semibold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 14)
                                        .background(Color.white.opacity(0.15))
                                        .foregroundColor(.white)
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                }
                                .accessibilityIdentifier("DownloadResumeButton")

                                Button {
                                    coordinator.cancelAll()
                                } label: {
                                    Text("Cancel")
                                        .font(.system(size: 15, weight: .semibold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 14)
                                        .background(Color.red.opacity(0.2))
                                        .foregroundColor(.red)
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                }
                            }

                        case .failed:
                            VStack(spacing: 12) {
                                Button {
                                    coordinator.startDownloadAll(
                                        reels: effectiveReels, weekID: weekID,
                                        excludeWatchedIDs: unwatchedOnly ? watchedIDs : [],
                                        generatedAt: generatedAt)
                                } label: {
                                    Text("Retry")
                                        .font(.system(size: 16, weight: .bold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 16)
                                        .background(effectiveReels.isEmpty ? Color.white.opacity(0.3) : Color.white)
                                        .foregroundColor(effectiveReels.isEmpty ? Color.black.opacity(0.5) : Color.black)
                                        .clipShape(RoundedRectangle(cornerRadius: 14))
                                }
                                .disabled(effectiveReels.isEmpty)
                                .accessibilityIdentifier("DownloadRetryButton")

                                Button {
                                    dismiss()
                                } label: {
                                    Text("Done")
                                        .font(.system(size: 15, weight: .semibold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 14)
                                        .background(Color.white.opacity(0.15))
                                        .foregroundColor(.white)
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                }
                                .accessibilityIdentifier("DownloadDoneButton")
                            }
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.bottom, 20)
                }
            }
            .navigationTitle("Download All")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Close") {
                        dismiss()
                    }
                    .foregroundColor(.white)
                    .accessibilityIdentifier("DownloadCloseButton")
                }
            }
            .onAppear {
                if let list = try? modelContext.fetch(FetchDescriptor<WatchedEvent>()) {
                    watchedIDs = Set(list.filter { $0.weekID == weekID }.map { $0.reelID })
                }
            }
            .task {
                await refreshCensusAndPending()
            }
            .onChange(of: unwatchedOnly) { _, _ in
                preflightResult = coordinator.preflightStorage(reels: pendingReels, weekID: weekID)
                Task {
                    await refreshCensusAndPending()
                }
            }
            .onChange(of: coordinator.state) { _, newState in
                if isTerminalState(newState) {
                    Task {
                        await refreshCensusAndPending()
                    }
                }
            }
        }
    }

    private func isTerminalState(_ state: DownloadAllCoordinator.CoordinatorState) -> Bool {
        switch state {
        case .idle, .completed, .completedWithFailures, .failed:
            return true
        case .preparing, .downloading, .paused:
            return false
        }
    }

    private func refreshCensusAndPending() async {
        let snapshotReels = reels
        let currentWeekID = weekID
        guard !snapshotReels.isEmpty && !currentWeekID.isEmpty else {
            census = (0, 0)
            cachedPendingIDs = []
            preflightResult = (true, 0, 0)
            return
        }

        let (newCensus, pendingIDs) = await Task.detached(priority: .userInitiated) { () -> ((cached: Int, total: Int), Set<String>) in
            let c = LibraryPathResolver.shared.cacheCensus(reels: snapshotReels, weekID: currentWeekID)
            let pending = Set(snapshotReels.compactMap { r in
                LibraryPathResolver.shared.isLocalFileAvailable(for: currentWeekID, reelID: r.id) ? nil : r.id
            })
            return (c, pending)
        }.value

        await MainActor.run {
            self.census = newCensus
            self.cachedPendingIDs = pendingIDs
            self.preflightResult = coordinator.preflightStorage(reels: self.pendingReels, weekID: self.weekID)
        }
    }

    private func formatMB(_ bytes: Int64) -> String {
        String(format: "%.1f MB", Double(bytes) / 1_000_000)
    }

    private var isAllDownloaded: Bool {
        guard let c = census, c.total > 0 else { return false }
        return c.cached == c.total
    }

    private var effectiveReels: [ReelItem] {
        unwatchedOnly ? WatchedRules.unwatchedItems(items: reels, alreadyWatched: watchedIDs) : reels
    }

    private var pendingReels: [ReelItem] {
        effectiveReels.filter { cachedPendingIDs.contains($0.id) }
    }
}
