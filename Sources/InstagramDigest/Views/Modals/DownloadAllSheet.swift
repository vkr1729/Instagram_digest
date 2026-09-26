import SwiftUI
import SwiftData

/// Dismissible bulk download manager sheet with device storage preflight and adaptive concurrency status.
public struct DownloadAllSheet: View {
    @ObservedObject var coordinator = DownloadAllCoordinator.shared
    public let reels: [ReelItem]
    public let weekID: String
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var modelContext

    @State private var preflightResult: (isSufficient: Bool, requiredBytes: Int64, availableBytes: Int64)?
    @State private var unwatchedOnly: Bool = true
    @State private var watchedIDs: Set<String> = []

    public init(reels: [ReelItem], weekID: String) {
        self.reels = reels
        self.weekID = weekID
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
                        .onChange(of: unwatchedOnly) { _, _ in
                            preflightResult = coordinator.preflightStorage(reels: effectiveReels, weekID: weekID)
                        }
                    }
                    VStack(spacing: 12) {
                        switch coordinator.state {
                        case .idle:
                            Text("\(effectiveReels.count) reels ready to download")
                                .font(.system(size: 15, weight: .medium))
                                .foregroundColor(.white.opacity(0.8))

                        case .downloading(let completed, let total, let currentReel):
                            VStack(spacing: 8) {
                                ProgressView(value: coordinator.overallProgress)
                                    .tint(.cyan)

                                HStack {
                                    Text("Downloaded \(completed) of \(total)")
                                        .font(.system(size: 13, weight: .semibold))
                                        .foregroundColor(.white)
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
                            Button {
                                coordinator.startDownloadAll(
                                    reels: effectiveReels, weekID: weekID,
                                    excludeWatchedIDs: unwatchedOnly ? watchedIDs : [])
                            } label: {
                                Text("Start Download")
                                    .font(.system(size: 16, weight: .bold))
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 16)
                                    .background(Color.white)
                                    .foregroundColor(.black)
                                    .clipShape(RoundedRectangle(cornerRadius: 14))
                            }
                            .accessibilityIdentifier("StartDownloadButton")

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
                                        excludeWatchedIDs: unwatchedOnly ? watchedIDs : [])
                                } label: {
                                    Text("Retry")
                                        .font(.system(size: 16, weight: .bold))
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 16)
                                        .background(Color.white)
                                        .foregroundColor(.black)
                                        .clipShape(RoundedRectangle(cornerRadius: 14))
                                }
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
                preflightResult = coordinator.preflightStorage(reels: effectiveReels, weekID: weekID)
            }
        }
    }

    private func formatMB(_ bytes: Int64) -> String {
        String(format: "%.1f MB", Double(bytes) / 1_000_000)
    }

    private var effectiveReels: [ReelItem] {
        unwatchedOnly ? WatchedRules.unwatchedItems(items: reels, alreadyWatched: watchedIDs) : reels
    }
}
