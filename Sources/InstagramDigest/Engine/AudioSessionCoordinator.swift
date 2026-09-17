import Foundation
import AVFoundation

/// Coordinates system audio session, interruptions, route changes, and hardware resets.
@MainActor
public final class AudioSessionCoordinator: Sendable {
    public static let shared = AudioSessionCoordinator()

    private var wasPlayingBeforeInterruption: Bool = false
    private var isSessionActive: Bool = false

    private init() {
        setupObservers()
    }

    /// Pre-configures the session category and mode lazily
    public func configureAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playback,
                mode: .moviePlayback,
                options: [.allowBluetooth, .allowBluetoothA2DP, .allowAirPlay]
            )
        } catch {
            // Non-fatal, default system audio category remains
        }
    }

    /// Activates audio session when playback begins
    public func activateSession() {
        guard !isSessionActive else { return }
        configureAudioSession()
        do {
            try AVAudioSession.sharedInstance().setActive(true)
            isSessionActive = true
        } catch {
            // Audio activation failure
        }
    }

    /// Deactivates audio session with notification to other apps
    public func deactivateSession() {
        guard isSessionActive else { return }
        do {
            try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            isSessionActive = false
        } catch {
            // Audio deactivation failure
        }
    }

    // MARK: - Notifications & Observers

    private func setupObservers() {
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            self?.handleInterruption(notification: notification)
        }

        NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            self?.handleRouteChange(notification: notification)
        }

        NotificationCenter.default.addObserver(
            forName: AVAudioSession.mediaServicesWereResetNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            self?.handleMediaServicesReset(notification: notification)
        }

        NotificationCenter.default.addObserver(
            forName: AVAudioSession.silenceSecondaryAudioHintNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            self?.handleSecondaryAudioHint(notification: notification)
        }
    }

    private func handleInterruption(notification: Notification) {
        guard let userInfo = notification.userInfo,
              let typeValue = userInfo[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: typeValue) else {
            return
        }

        switch type {
        case .began:
            wasPlayingBeforeInterruption = AVPlayerPool.shared.isPlaying
            if wasPlayingBeforeInterruption {
                AVPlayerPool.shared.pause()
            }
        case .ended:
            guard wasPlayingBeforeInterruption else { return }
            wasPlayingBeforeInterruption = false

            if let optionsValue = userInfo[AVAudioSessionInterruptionOptionKey] as? UInt {
                let options = AVAudioSession.InterruptionOptions(rawValue: optionsValue)
                if options.contains(.shouldResume) {
                    AVPlayerPool.shared.play()
                }
            }
        @unknown default:
            break
        }
    }

    private func handleRouteChange(notification: Notification) {
        guard let userInfo = notification.userInfo,
              let reasonValue = userInfo[AVAudioSessionRouteChangeReasonKey] as? UInt,
              let reason = AVAudioSession.RouteChangeReason(rawValue: reasonValue) else {
            return
        }

        // On headphone unplug / Bluetooth disconnect, pause immediately
        if reason == .oldDeviceUnavailable {
            if AVPlayerPool.shared.isPlaying {
                AVPlayerPool.shared.pause()
            }
        }
    }

    private func handleMediaServicesReset(notification: Notification) {
        // Media server crashed: capture saved position and force current slot rebuild
        let savedPosition = AVPlayerPool.shared.currentTime
        configureAudioSession()
        isSessionActive = false
        activateSession()
        AVPlayerPool.shared.rebuildCurrentSlot(restoringTo: savedPosition)
    }

    private func handleSecondaryAudioHint(notification: Notification) {
        // Secondary audio hint
    }
}
