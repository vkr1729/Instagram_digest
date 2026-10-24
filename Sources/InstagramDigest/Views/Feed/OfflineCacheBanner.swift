import SwiftUI

/// Compact offline cache status banner for direct stream mode.
/// Displays download progress, preparing status, or a 1-tap call-to-action to download the week for offline underground MRT viewing.
public struct OfflineCacheBanner: View {
    @ObservedObject private var coordinator = DownloadAllCoordinator.shared
    public let reels: [ReelItem]
    public let weekID: String
    public let onTap: () -> Void

    @State private var downloadedCount: Int = 0

    public init(reels: [ReelItem], weekID: String, onTap: @escaping () -> Void) {
        self.reels = reels
        self.weekID = weekID
        self.onTap = onTap
    }

    public var body: some View {
        Button(action: onTap) {
            HStack(spacing: 6) {
                switch coordinator.state {
                case .preparing:
                    ProgressView()
                        .progressViewStyle(CircularProgressViewStyle(tint: .cyan))
                        .scaleEffect(0.8)
                    Text("Preparing cache...")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.cyan)

                case .downloading(let completed, let total, _):
                    ProgressView(value: coordinator.overallProgress)
                        .progressViewStyle(LinearProgressViewStyle(tint: .cyan))
                        .frame(width: 40)
                    Text("Downloading: \(completed)/\(total)")
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .foregroundColor(.cyan)

                case .failed(let msg):
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 11))
                        .foregroundColor(.orange)
                    Text("Download failed: \(msg)")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.orange)
                        .lineLimit(1)

                case .completed:
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 11))
                        .foregroundColor(.green)
                    if reels.count > 0 {
                        Text("All \(reels.count) Reels Cached Offline")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.white.opacity(0.85))
                    } else {
                        Text("All Reels Cached Offline")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.white.opacity(0.85))
                    }

                case .completedWithFailures(let ready, let failed):
                    Image(systemName: "exclamationmark.circle.fill")
                        .font(.system(size: 11))
                        .foregroundColor(.orange)
                    Text("Cached Offline (\(ready)/\(ready + failed), \(failed) failed)")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.white.opacity(0.85))

                case .paused:
                    Image(systemName: "pause.circle.fill")
                        .font(.system(size: 11))
                        .foregroundColor(.yellow)
                    Text("Download paused — tap to resume")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.yellow)

                case .idle:
                    if downloadedCount >= reels.count && !reels.isEmpty {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 11))
                            .foregroundColor(.green)
                        Text("All \(reels.count) Reels Cached Offline")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.white.opacity(0.85))
                    } else {
                        Image(systemName: "arrow.down.circle.fill")
                            .font(.system(size: 12))
                            .foregroundColor(.cyan)
                        Text("⚡ Download Week for Offline (\(downloadedCount)/\(reels.count))")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(.white)
                    }
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(Color.black.opacity(0.55))
            .clipShape(Capsule())
            .overlay(
                Capsule().stroke(Color.white.opacity(0.18), lineWidth: 0.8)
            )
        }
        .contentShape(Rectangle())
        .accessibilityIdentifier("DirectStreamOfflineBanner")
        .task(id: coordinator.state) {
            await updateDownloadedCount()
        }
        .task(id: weekID) {
            await updateDownloadedCount()
        }
        .task(id: reels.count) {
            await updateDownloadedCount()
        }
    }

    private func updateDownloadedCount() async {
        let currentWeekID = weekID
        let currentReels = reels
        let count = await Task.detached(priority: .userInitiated) { () -> Int in
            let weekDir = LibraryPathResolver.shared.weekDirectoryURL(for: currentWeekID)
            guard let fileURLs = try? FileManager.default.contentsOfDirectory(at: weekDir, includingPropertiesForKeys: [.fileSizeKey]) else {
                return 0
            }
            let existingFiles = Set(fileURLs.compactMap { url -> String? in
                guard url.pathExtension == "mp4" else { return nil }
                let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                return size > 0 ? url.deletingPathExtension().lastPathComponent : nil
            })
            return currentReels.filter { existingFiles.contains(LibraryPathResolver.sanitizeComponent($0.id)) }.count
        }.value
        await MainActor.run {
            self.downloadedCount = count
        }
    }
}
