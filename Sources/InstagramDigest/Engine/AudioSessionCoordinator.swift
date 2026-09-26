import Foundation
import AVFoundation
import UIKit

/// Coordinates system audio session, interruptions, route changes, and hardware resets.
@MainActor
public final class AudioSessionCoordinator: Sendable {
    public static let shared = AudioSessionCoordinator()

    private var interruptionDepth: Int = 0
    private var wasPlayingBeforeInterruption: Bool = false
    private var isSessionActive: Bool = false

    private init() {
        setupObservers()
    }

    /// Pre-configures the session category and mode lazily
    public func configureAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            // .allowBluetooth / .allowAirPlay are playAndRecord-only: with
            // .playback they make setCategory throw and leave .soloAmbient
            // (muted by the silent switch). A2DP + AirPlay are automatic here.
            try session.setCategory(.playback, mode: .moviePlayback, options: [.mixWithOthers])
        } catch {
            // Non-fatal at runtime; a regression here is caught by
            // EngineTests.testAudioSessionUsesPlaybackCategory in CI.
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
        } catch {
            // B8: deactivation often throws mid-interruption while the
            // session is effectively inactive either way. The flag must
            // clear regardless, or the next activateSession() early-returns
            // and video plays without audio.
        }
        isSessionActive = false
    }

    // MARK: - Notifications & Observers

    private func setupObservers() {
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil,
            queue: .main
        ) { @MainActor [weak self] notification in
            self?.handleInterruption(notification: notification)
        }

        NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { @MainActor [weak self] notification in
            self?.handleRouteChange(notification: notification)
        }

        NotificationCenter.default.addObserver(
            forName: AVAudioSession.mediaServicesWereResetNotification,
            object: nil,
            queue: .main
        ) { @MainActor [weak self] notification in
            self?.handleMediaServicesReset(notification: notification)
        }

        NotificationCenter.default.addObserver(
            forName: AVAudioSession.silenceSecondaryAudioHintNotification,
            object: nil,
            queue: .main
        ) { @MainActor [weak self] notification in
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
            if interruptionDepth == 0 {
                wasPlayingBeforeInterruption = AVPlayerPool.shared.isPlaying
                if wasPlayingBeforeInterruption {
                    AVPlayerPool.shared.pause()
                }
            }
            interruptionDepth += 1
        case .ended:
            interruptionDepth = max(0, interruptionDepth - 1)
            guard interruptionDepth == 0 else { return }
            guard wasPlayingBeforeInterruption else { return }
            wasPlayingBeforeInterruption = false

            if let optionsValue = userInfo[AVAudioSessionInterruptionOptionKey] as? UInt {
                let options = AVAudioSession.InterruptionOptions(rawValue: optionsValue)
                // Never restart audio from the background (ghost audio); the
                // pool's foreground handler restores playback on return.
                if options.contains(.shouldResume),
                   UIApplication.shared.applicationState == .active {
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

        // On headphone unplug / Bluetooth disconnect, pause immediately and prevent accidental speaker resume
        if reason == .oldDeviceUnavailable {
            wasPlayingBeforeInterruption = false
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
