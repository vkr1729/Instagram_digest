import UIKit
import SwiftUI

/// UIKit gesture stack coordinating single-tap play/pause (with 2.0x latch reset),
/// long-press spatial zones, seek pan scrubbing, and pre-warmed haptics with zero scroll interference.
public struct FeedGestureOverlay: UIViewRepresentable {
    public var onTogglePlayPause: () -> Void
    public var onSeekPreview: (Double?) -> Void
    public var onSeekCommit: (Double) -> Void
    public var onToggleLatched2x: () -> Void
    public var onTriggerBookmark: () -> Void
    public var onTriggerShare: () -> Void
    public var lastScrollEndTime: TimeInterval
    public var currentProgress: Double

    public init(
        lastScrollEndTime: TimeInterval = 0,
        currentProgress: Double = 0.0,
        onTogglePlayPause: @escaping () -> Void,
        onSeekPreview: @escaping (Double?) -> Void,
        onSeekCommit: @escaping (Double) -> Void,
        onToggleLatched2x: @escaping () -> Void,
        onTriggerBookmark: @escaping () -> Void,
        onTriggerShare: @escaping () -> Void
    ) {
        self.lastScrollEndTime = lastScrollEndTime
        self.currentProgress = currentProgress
        self.onTogglePlayPause = onTogglePlayPause
        self.onSeekPreview = onSeekPreview
        self.onSeekCommit = onSeekCommit
        self.onToggleLatched2x = onToggleLatched2x
        self.onTriggerBookmark = onTriggerBookmark
        self.onTriggerShare = onTriggerShare
    }

    public func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    public func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.backgroundColor = .clear
        view.isUserInteractionEnabled = true
        view.accessibilityIdentifier = "FeedGestureOverlayView"

        let coordinator = context.coordinator
        coordinator.setupGestures(in: view)
        return view
    }

    public func updateUIView(_ uiView: UIView, context: Context) {
        context.coordinator.parent = self
    }

    // MARK: - Coordinator

    @MainActor
    public final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var parent: FeedGestureOverlay

        public private(set) var tapGesture: UITapGestureRecognizer!
        public private(set) var longPressGesture: UILongPressGestureRecognizer!
        public private(set) var seekPanGesture: SeekPanGestureRecognizer!

        private let hapticGenerator = UIImpactFeedbackGenerator(style: .medium)
        private var suppressNextTap: Bool = false
        private var lastSwipeTime: TimeInterval = 0
        private var seekStartLocationX: CGFloat = 0
        private var initialSeekFraction: Double = 0
        private var currentSeekFraction: Double = 0

        init(_ parent: FeedGestureOverlay) {
            self.parent = parent
            super.init()
            hapticGenerator.prepare()
        }

        func setupGestures(in view: UIView) {
            // 1. Single tap play/pause (resets latched 2.0x speed)
            tapGesture = UITapGestureRecognizer(target: self, action: #selector(handleTap(_:)))
            tapGesture.numberOfTapsRequired = 1
            tapGesture.delegate = self
            view.addGestureRecognizer(tapGesture)

            // 2. Long press with 0.5s hold and 10pt allowable movement
            longPressGesture = UILongPressGestureRecognizer(target: self, action: #selector(handleLongPress(_:)))
            longPressGesture.minimumPressDuration = 0.5
            longPressGesture.allowableMovement = 10.0
            longPressGesture.delegate = self
            view.addGestureRecognizer(longPressGesture)

            // 3. Custom SeekPanGestureRecognizer failing on vertical dominance
            seekPanGesture = SeekPanGestureRecognizer(target: self, action: #selector(handleSeekPan(_:)))
            seekPanGesture.delegate = self
            view.addGestureRecognizer(seekPanGesture)

            // Pre-fire & Post-fire disambiguation:
            // Both tap and seek pan require long-press to fail!
            tapGesture.require(toFail: longPressGesture)
            seekPanGesture.require(toFail: longPressGesture)
        }

        // MARK: - Tap Handler

        @objc private func handleTap(_ sender: UITapGestureRecognizer) {
            // Atomic check-and-clear suppressNextTap flag
            if suppressNextTap {
                suppressNextTap = false
                return
            }

            // Guard against taps within 500ms of scroll end and 350ms of swipe
            let now = ProcessInfo.processInfo.systemUptime
            if now - parent.lastScrollEndTime < 0.50 || now - lastSwipeTime < 0.35 {
                return
            }

            // Reset latched 2.0x if active per §1.5 contract: "Single tap toggles play/pause; resets latched 2.0x speed"
            if AVPlayerPool.shared.isLatched2x {
                AVPlayerPool.shared.setLatched2x(false)
            }

            parent.onTogglePlayPause()
        }

        // MARK: - Long Press Spatial Zones

        @objc private func handleLongPress(_ sender: UILongPressGestureRecognizer) {
            guard let view = sender.view else { return }
            let location = sender.location(in: view)
            let width = view.bounds.width
            let height = view.bounds.height
            guard width > 0, height > 0 else { return }

            let normX = location.x / width
            let normY = location.y / height

            switch sender.state {
            case .began:
                // Spatial zone routing: only set suppressNextTap if a valid action fires!
                if normX > 0.65 && normY <= 0.65 {
                    // Upper-Right: 2.0x latched boost
                    suppressNextTap = true
                    hapticGenerator.impactOccurred()
                    hapticGenerator.prepare()
                    parent.onToggleLatched2x()

                } else if normX > 0.65 && normY > 0.65 {
                    // Lower-Right: Share sheet
                    suppressNextTap = true
                    hapticGenerator.impactOccurred()
                    hapticGenerator.prepare()
                    parent.onTriggerShare()

                } else if normX >= 0.35 && normX <= 0.65 && normY > 0.65 {
                    // Center-Lower: Bookmark toggle
                    suppressNextTap = true
                    hapticGenerator.impactOccurred()
                    hapticGenerator.prepare()
                    parent.onTriggerBookmark()
                } else {
                    // Dead zone: do NOT suppress next tap
                    suppressNextTap = false
                }

            case .ended, .cancelled:
                // Atomic check-and-clear is handled on tap; no arbitrary timers
                break
            default:
                break
            }
        }

        // MARK: - Seek Pan Handler

        @objc private func handleSeekPan(_ sender: SeekPanGestureRecognizer) {
            guard let view = sender.view else { return }
            let location = sender.location(in: view)
            let width = view.bounds.width
            guard width > 0 else { return }

            switch sender.state {
            case .began:
                seekStartLocationX = location.x
                initialSeekFraction = parent.currentProgress
                currentSeekFraction = initialSeekFraction
                parent.onSeekPreview(currentSeekFraction)

            case .changed:
                let deltaX = location.x - seekStartLocationX
                // Map horizontal delta relative to view width
                let fractionChange = Double(deltaX / width)
                let targetFraction = max(0.0, min(1.0, initialSeekFraction + fractionChange))
                currentSeekFraction = targetFraction
                parent.onSeekPreview(targetFraction)

            case .ended:
                parent.onSeekCommit(currentSeekFraction)
                parent.onSeekPreview(nil)
                lastSwipeTime = ProcessInfo.processInfo.systemUptime

            case .cancelled, .failed:
                parent.onSeekPreview(nil)
            default:
                break
            }
        }

        // MARK: - UIGestureRecognizerDelegate

        public func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            // Seek pan and collection view pan NEVER recognize simultaneously
            if gestureRecognizer is SeekPanGestureRecognizer || otherGestureRecognizer is SeekPanGestureRecognizer {
                return false
            }
            return false
        }
    }
}
