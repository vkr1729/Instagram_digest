import UIKit
import UIKit.UIGestureRecognizerSubclass

/// Custom UIPanGestureRecognizer that fails immediately when vertical dominance is detected,
/// allowing parent UICollectionView vertical paging to scroll without interference.
public final class SeekPanGestureRecognizer: UIPanGestureRecognizer {
    private var initialTouchLocation: CGPoint = .zero
    private var isDisambiguated: Bool = false

    public override init(target: Any?, action: Selector?) {
        super.init(target: target, action: action)
        maximumNumberOfTouches = 1
    }

    public override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        super.touchesBegan(touches, with: event)
        if let touch = touches.first {
            // IOS-P2-18: window space, matching the handler (which uses
            // view.window). Collection-view space is shifted by contentOffset,
            // so vertical scroll inflated Δy and falsely failed seeks.
            initialTouchLocation = touch.location(in: view?.window ?? view)
        }
        isDisambiguated = false
    }

    public override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        super.touchesMoved(touches, with: event)
        guard !isDisambiguated, let touch = touches.first else { return }

        let current = touch.location(in: view?.window ?? view)
        let deltaX = abs(current.x - initialTouchLocation.x)
        let deltaY = abs(current.y - initialTouchLocation.y)

        // If vertical dominance is detected before horizontal lock (|Δy| * 1.4 >= |Δx| before |Δx| crosses 18pt),
        // fail immediately so collection view vertical paging handles the touch.
        if Self.shouldFailSeekForVerticalDominance(deltaX: deltaX, deltaY: deltaY) {
            state = .failed
            isDisambiguated = true
            return
        } else if deltaX >= 18.0 {
            // Horizontal dominance established: lock horizontal gesture
            isDisambiguated = true
        }
    }

    public override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        super.touchesEnded(touches, with: event)
        isDisambiguated = false
    }

    public override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        super.touchesCancelled(touches, with: event)
        isDisambiguated = false
    }

    public override func reset() {
        super.reset()
        isDisambiguated = false
    }

    /// Testable vertical-dominance rule: fail when |Δy|*1.4 >= |Δx| before |Δx| crosses 18pt.
    public static func shouldFailSeekForVerticalDominance(deltaX: CGFloat, deltaY: CGFloat) -> Bool {
        let ax = abs(deltaX)
        let ay = abs(deltaY)
        if ax < 18.0 {
            return ay * 1.4 >= ax
        }
        return false
    }

    /// Testable seek-fraction math: clamp(initial + dx/width) to [0,1].
    public static func seekFraction(initialFraction: Double, deltaX: CGFloat, viewWidth: CGFloat) -> Double {
        guard viewWidth > 0 else { return initialFraction }
        return max(0.0, min(1.0, initialFraction + Double(deltaX / viewWidth)))
    }
}
