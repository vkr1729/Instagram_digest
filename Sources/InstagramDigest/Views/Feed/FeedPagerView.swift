import UIKit
import SwiftUI

/// Full-screen UICollectionView paging wrapper implementing vertical feed scrolling,
/// modern CellRegistration, immediate layer detach on prepareForReuse, and 80ms settle debounce.
public struct FeedPagerView: UIViewControllerRepresentable {
    public let reels: [ReelItem]
    @Binding public var currentIndex: Int
    public var onPageChanged: (Int) -> Void
    public var onScrollEnded: (TimeInterval) -> Void
    public var onForwardScrollPast: (ReelItem) -> Void
    public var onTogglePlayPause: () -> Void
    public var onSeekPreview: (Double?) -> Void
    public var onSeekCommit: (Double) -> Void
    public var onToggleLatched2x: () -> Void
    public var onTriggerBookmark: () -> Void
    public var onTriggerShare: () -> Void
    public var lastScrollEndTime: TimeInterval
    public var currentProgress: Double

    public init(
        reels: [ReelItem],
        currentIndex: Binding<Int>,
        onPageChanged: @escaping (Int) -> Void,
        onScrollEnded: @escaping (TimeInterval) -> Void,
        onForwardScrollPast: @escaping (ReelItem) -> Void,
        onTogglePlayPause: @escaping () -> Void = {},
        onSeekPreview: @escaping (Double?) -> Void = { _ in },
        onSeekCommit: @escaping (Double) -> Void = { _ in },
        onToggleLatched2x: @escaping () -> Void = {},
        onTriggerBookmark: @escaping () -> Void = {},
        onTriggerShare: @escaping () -> Void = {},
        lastScrollEndTime: TimeInterval = 0,
        currentProgress: Double = 0.0
    ) {
        self.reels = reels
        self._currentIndex = currentIndex
        self.onPageChanged = onPageChanged
        self.onScrollEnded = onScrollEnded
        self.onForwardScrollPast = onForwardScrollPast
        self.onTogglePlayPause = onTogglePlayPause
        self.onSeekPreview = onSeekPreview
        self.onSeekCommit = onSeekCommit
        self.onToggleLatched2x = onToggleLatched2x
        self.onTriggerBookmark = onTriggerBookmark
        self.onTriggerShare = onTriggerShare
        self.lastScrollEndTime = lastScrollEndTime
        self.currentProgress = currentProgress
    }

    public func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    public func makeUIViewController(context: Context) -> FeedCollectionViewController {
        let layout = UICollectionViewFlowLayout()
        layout.scrollDirection = .vertical
        layout.minimumLineSpacing = 0
        layout.minimumInteritemSpacing = 0

        let vc = FeedCollectionViewController(collectionViewLayout: layout)
        vc.coordinator = context.coordinator
        context.coordinator.viewController = vc
        return vc
    }

    public func updateUIViewController(_ uiViewController: FeedCollectionViewController, context: Context) {
        context.coordinator.parent = self
        uiViewController.coordinator = context.coordinator
        uiViewController.updateReelsIfNeeded(reels, targetIndex: currentIndex)
        uiViewController.scrollToCurrentIndexIfNeeded(currentIndex)
        uiViewController.updateCurrentIndex(currentIndex)
    }

    // MARK: - Coordinator

    @MainActor
    public final class Coordinator: NSObject, UICollectionViewDataSource, UICollectionViewDelegate, UICollectionViewDelegateFlowLayout, UICollectionViewDataSourcePrefetching, UIGestureRecognizerDelegate {
        var parent: FeedPagerView
        weak var viewController: FeedCollectionViewController?
        private var settleWorkItem: DispatchWorkItem?
        public var lastActiveIndex: Int = 0
        private(set) var cellRegistration: UICollectionView.CellRegistration<FeedCell, ReelItem>!

        private let hapticGenerator = UIImpactFeedbackGenerator(style: .medium)
        private var suppressNextTap: Bool = false
        private var lastSwipeTime: TimeInterval = 0
        private var seekStartLocationX: CGFloat = 0
        private var initialSeekFraction: Double = 0
        private var currentSeekFraction: Double = 0

        init(_ parent: FeedPagerView) {
            self.parent = parent
            self.lastActiveIndex = parent.currentIndex
            super.init()
            hapticGenerator.prepare()
            self.cellRegistration = UICollectionView.CellRegistration<FeedCell, ReelItem> { [weak self] cell, indexPath, reel in
                guard let self = self else { return }
                let isCurrent = indexPath.item == self.parent.currentIndex
                cell.configure(reel: reel, isCurrent: isCurrent)
            }
        }

        // MARK: - Gesture Handlers

        @objc func handleTap(_ sender: UITapGestureRecognizer) {
            if suppressNextTap {
                suppressNextTap = false
                return
            }

            let now = ProcessInfo.processInfo.systemUptime
            if now - parent.lastScrollEndTime < 0.50 || now - lastSwipeTime < 0.35 {
                return
            }

            if AVPlayerPool.shared.isLatched2x {
                AVPlayerPool.shared.setLatched2x(false)
            }

            parent.onTogglePlayPause()
        }

        @objc func handleLongPress(_ sender: UILongPressGestureRecognizer) {
            guard let view = sender.view else { return }
            let targetView = view.window ?? view.superview ?? view
            let location = sender.location(in: targetView)
            let width = targetView.bounds.width
            let height = targetView.bounds.height
            guard width > 0, height > 0 else { return }

            // Clamp to the visible viewport so rounding/safe-area overshoot
            // can never push a 2x touch into the Share zone (or vice versa).
            let normX = min(max(location.x / width, 0.0), 1.0)
            let normY = min(max(location.y / height, 0.0), 1.0)

            switch sender.state {
            case .began:
                if normX > 0.65 && normY <= 0.65 {
                    // Mid & upper right for 2x
                    suppressNextTap = true
                    hapticGenerator.impactOccurred()
                    hapticGenerator.prepare()
                    parent.onToggleLatched2x()

                } else if normX > 0.65 && normY > 0.65 {
                    // Lower right for Share
                    suppressNextTap = true
                    hapticGenerator.impactOccurred()
                    hapticGenerator.prepare()
                    parent.onTriggerShare()

                } else if normX >= 0.30 && normX <= 0.65 && normY > 0.65 {
                    // Lower middle for Bookmark
                    suppressNextTap = true
                    hapticGenerator.impactOccurred()
                    hapticGenerator.prepare()
                    parent.onTriggerBookmark()

                } else {
                    suppressNextTap = false
                }

            case .ended, .cancelled:
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
                    self?.suppressNextTap = false
                }
            default:
                break
            }
        }

        @objc func handleSeekPan(_ sender: SeekPanGestureRecognizer) {
            guard let view = sender.view else { return }
            let targetView = view.window ?? view.superview ?? view
            let location = sender.location(in: targetView)
            let width = targetView.bounds.width
            guard width > 0 else { return }

            switch sender.state {
            case .began:
                seekStartLocationX = location.x
                initialSeekFraction = parent.currentProgress
                currentSeekFraction = initialSeekFraction
                parent.onSeekPreview(initialSeekFraction)

            case .changed:
                let deltaX = location.x - seekStartLocationX
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
            if gestureRecognizer is SeekPanGestureRecognizer || otherGestureRecognizer is SeekPanGestureRecognizer {
                return false
            }
            return false
        }

        // MARK: - UICollectionViewDataSource

        public func collectionView(_ collectionView: UICollectionView, numberOfItemsInSection section: Int) -> Int {
            parent.reels.count
        }

        public func collectionView(_ collectionView: UICollectionView, cellForItemAt indexPath: IndexPath) -> UICollectionViewCell {
            guard indexPath.item < parent.reels.count else {
                return UICollectionViewCell()
            }
            let reel = parent.reels[indexPath.item]
            let cell = collectionView.dequeueConfiguredReusableCell(using: cellRegistration, for: indexPath, item: reel)
            viewController?.reattachPlayerToVisibleCells()
            return cell
        }

        // MARK: - UICollectionViewDelegateFlowLayout

        public func collectionView(_ collectionView: UICollectionView, layout collectionViewLayout: UICollectionViewLayout, sizeForItemAt indexPath: IndexPath) -> CGSize {
            collectionView.bounds.size
        }

        // MARK: - Scroll Settle Detection (80ms debounce)

        public func scrollViewDidEndDecelerating(_ scrollView: UIScrollView) {
            handleScrollSettle(scrollView)
        }

        public func scrollViewDidEndDragging(_ scrollView: UIScrollView, willDecelerate decelerate: Bool) {
            if !decelerate {
                handleScrollSettle(scrollView)
            }
        }

        public func scrollViewDidEndScrollingAnimation(_ scrollView: UIScrollView) {
            handleScrollSettle(scrollView)
        }

        public func scrollViewDidScroll(_ scrollView: UIScrollView) {
            viewController?.reattachPlayerToVisibleCells()
        }

        private func handleScrollSettle(_ scrollView: UIScrollView) {
            settleWorkItem?.cancel()
            let workItem = DispatchWorkItem { [weak self] in
                guard let self = self else { return }
                guard !self.parent.reels.isEmpty else { return }
                if let vc = self.viewController, vc.pendingInitialScrollIndex != nil {
                    return
                }
                let height = scrollView.bounds.height
                guard height > 0 else { return }

                let page = Int(round(scrollView.contentOffset.y / height))
                let clampedPage = max(0, min(self.parent.reels.count - 1, page))

                let uptime = ProcessInfo.processInfo.systemUptime
                self.parent.onScrollEnded(uptime)

                if clampedPage != self.parent.currentIndex {
                    // Watched Rule 1: Forward scroll-away
                    if clampedPage > self.lastActiveIndex && self.lastActiveIndex < self.parent.reels.count {
                        let departedReel = self.parent.reels[self.lastActiveIndex]
                        self.parent.onForwardScrollPast(departedReel)
                    }

                    self.lastActiveIndex = clampedPage
                    self.parent.currentIndex = clampedPage
                    self.parent.onPageChanged(clampedPage)
                    self.viewController?.updateCurrentIndex(clampedPage)
                } else {
                    self.viewController?.reattachPlayerToVisibleCells()
                }
            }
            settleWorkItem = workItem
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.08, execute: workItem)

        }

        // MARK: - Prefetching

        public func collectionView(_ collectionView: UICollectionView, prefetchItemsAt indexPaths: [IndexPath]) {
        }
    }
}

public final class FeedCollectionViewController: UICollectionViewController {
    var coordinator: FeedPagerView.Coordinator?
    private var currentReels: [ReelItem] = []
    private var lastScrolledIndex: Int = -1
    private var currentAttachedIndex: Int = -1
    private var notificationTokens: [NSObjectProtocol] = []
    private var lastLayoutHeight: CGFloat = 0
    public var pendingInitialScrollIndex: Int?

    public override func viewDidLoad() {
        super.viewDidLoad()
        collectionView.backgroundColor = .black
        collectionView.isPagingEnabled = true
        collectionView.showsVerticalScrollIndicator = false
        collectionView.contentInsetAdjustmentBehavior = .never
        collectionView.accessibilityIdentifier = "FeedCollectionView"
        collectionView.dataSource = coordinator
        collectionView.delegate = coordinator
        collectionView.prefetchDataSource = coordinator

        if let coord = coordinator {
            let tapGesture = UITapGestureRecognizer(target: coord, action: #selector(FeedPagerView.Coordinator.handleTap(_:)))
            tapGesture.numberOfTapsRequired = 1
            tapGesture.delegate = coord

            let longPressGesture = UILongPressGestureRecognizer(target: coord, action: #selector(FeedPagerView.Coordinator.handleLongPress(_:)))
            longPressGesture.minimumPressDuration = 0.5
            longPressGesture.allowableMovement = 10.0
            longPressGesture.delegate = coord

            let seekPanGesture = SeekPanGestureRecognizer(target: coord, action: #selector(FeedPagerView.Coordinator.handleSeekPan(_:)))
            seekPanGesture.delegate = coord

            tapGesture.require(toFail: longPressGesture)
            seekPanGesture.require(toFail: longPressGesture)

            collectionView.addGestureRecognizer(tapGesture)
            collectionView.addGestureRecognizer(longPressGesture)
            collectionView.addGestureRecognizer(seekPanGesture)
        }

        notificationTokens.append(
            NotificationCenter.default.addObserver(
                forName: AVPlayerPool.didEnterForegroundNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                self?.reattachPlayerToVisibleCells()
            }
        )

        notificationTokens.append(
            NotificationCenter.default.addObserver(
                forName: UIApplication.willEnterForegroundNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                self?.reattachPlayerToVisibleCells()
            }
        )
    }

    deinit {
        for token in notificationTokens {
            NotificationCenter.default.removeObserver(token)
        }
    }

    public override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        let boundsHeight = collectionView.bounds.height
        if boundsHeight > 0, let pending = pendingInitialScrollIndex, !currentReels.isEmpty {
            // Clamp: a stale pending index (e.g. category filter shortened the
            // list) must land on the nearest valid page, never keep a stale
            // offset beyond content.
            let clamped = max(0, min(pending, currentReels.count - 1))
            pendingInitialScrollIndex = nil
            lastScrolledIndex = clamped
            coordinator?.lastActiveIndex = clamped
            let targetOffsetY = CGFloat(clamped) * boundsHeight
            collectionView.setContentOffset(CGPoint(x: 0, y: targetOffsetY), animated: false)
            currentAttachedIndex = clamped
        } else if boundsHeight > 0, pendingInitialScrollIndex != nil, currentReels.isEmpty {
            // List is empty: retain pending until items arrive; do not drop it.
        } else if boundsHeight > 0, pendingInitialScrollIndex == nil {
            // Rotation / safe-area change: cell height changed, so re-align the
            // offset to the attached page instead of stranding between pages.
            if lastLayoutHeight > 0, abs(boundsHeight - lastLayoutHeight) > 0.5,
               currentAttachedIndex >= 0, currentAttachedIndex < currentReels.count,
               !collectionView.isDragging, !collectionView.isDecelerating {
                let expectedY = CGFloat(currentAttachedIndex) * boundsHeight
                if abs(collectionView.contentOffset.y - expectedY) > 1.0 {
                    collectionView.setContentOffset(CGPoint(x: 0, y: expectedY), animated: false)
                    lastScrolledIndex = currentAttachedIndex
                }
            }
        }
        if boundsHeight > 0 {
            lastLayoutHeight = boundsHeight
        }
        reattachPlayerToVisibleCells()
    }

    /// Multi-slot attachment: binds Slot 1 to current reel, Slot 2 to current + 1 (next), and Slot 0 to current - 1 (prev)
    /// Eliminates black screen blink and renders videos instantly with zero thumbnail image loading
    public func reattachPlayerToVisibleCells() {
        let current = currentAttachedIndex
        guard current >= 0, current < currentReels.count else { return }

        for cell in collectionView.visibleCells {
            guard let indexPath = collectionView.indexPath(for: cell),
                  let feedCell = cell as? FeedCell,
                  indexPath.item < currentReels.count else { continue }
            let itemIndex = indexPath.item
            if itemIndex == current {
                feedCell.playerContainerView?.isHidden = false
                if let pc = feedCell.playerContainerView {
                    AVPlayerPool.shared.attachLayer(pc.playerLayer, forSlotIndex: 1)
                }
            } else if itemIndex == current + 1 {
                feedCell.playerContainerView?.isHidden = false
                if let pc = feedCell.playerContainerView {
                    AVPlayerPool.shared.attachLayer(pc.playerLayer, forSlotIndex: 2)
                }
            } else if itemIndex == current - 1 {
                feedCell.playerContainerView?.isHidden = false
                if let pc = feedCell.playerContainerView {
                    AVPlayerPool.shared.attachLayer(pc.playerLayer, forSlotIndex: 0)
                }
            } else {
                feedCell.playerContainerView?.isHidden = true
                feedCell.playerContainerView?.playerLayer.player = nil
            }
        }
    }

    /// Only reloads data when reels collection identity actually changes (prevents reloadData churn)
    public func updateReelsIfNeeded(_ newReels: [ReelItem], targetIndex: Int? = nil) {
        if currentReels != newReels {
            self.currentReels = newReels
            self.currentAttachedIndex = targetIndex ?? -1
            // Reset so scrollToCurrentIndexIfNeeded(0) fires on category switch
            // even when the previous list was already parked at index 0.
            self.lastScrolledIndex = -1
            // Always refresh pending on list identity change (even for target
            // 0): otherwise a stale pending index from launch survives a
            // category switch and scrolls the new filtered list to the wrong
            // page. scrollToCurrentIndexIfNeeded clears it again when it can
            // scroll immediately; viewDidLayoutSubviews consumes the deferred
            // remainder.
            if let target = targetIndex {
                self.pendingInitialScrollIndex = target
            } else {
                self.pendingInitialScrollIndex = nil
            }
            collectionView.reloadData()
        }
    }

    /// Reconfigures visible cells on index change to attach/detach player layers without reloadData churn
    public func updateCurrentIndex(_ newIndex: Int) {
        guard newIndex >= 0, newIndex < currentReels.count else { return }
        self.currentAttachedIndex = newIndex
        reattachPlayerToVisibleCells()
    }

    public func scrollToCurrentIndexIfNeeded(_ index: Int) {
        guard index >= 0, index < currentReels.count else { return }
        let boundsHeight = collectionView.bounds.height
        if boundsHeight <= 0 || collectionView.numberOfItems(inSection: 0) == 0 {
            // Collection view has not performed layout or reload yet: defer scroll until viewDidLayoutSubviews
            self.pendingInitialScrollIndex = index
            self.currentAttachedIndex = index
            return
        }

        guard index != lastScrolledIndex else {
            // Already parked: no stale pending may linger, otherwise the
            // settle guard would keep swallowing genuine user scrolls.
            pendingInitialScrollIndex = nil
            return
        }
        let shouldAnimate = (lastScrolledIndex >= 0 && abs(index - lastScrolledIndex) == 1)
        lastScrolledIndex = index
        coordinator?.lastActiveIndex = index
        // Immediate scroll succeeded: consume any pending marker so the
        // settle guard re-arms for real user scrolls. Only the deferred
        // (bounds == 0) path may leave pending set.
        pendingInitialScrollIndex = nil
        let targetOffsetY = CGFloat(index) * boundsHeight
        collectionView.setContentOffset(CGPoint(x: 0, y: targetOffsetY), animated: shouldAnimate)
        updateCurrentIndex(index)
    }

    public override func viewWillTransition(to size: CGSize, with coordinator: UIViewControllerTransitionCoordinator) {
        super.viewWillTransition(to: size, with: coordinator)
        coordinator.animate { _ in
            self.collectionView.collectionViewLayout.invalidateLayout()
        }
    }
}

/// Custom UICollectionViewCell with direct PlayerContainerView attachment and zero thumbnail image loading
public final class FeedCell: UICollectionViewCell {
    public private(set) var playerContainerView: PlayerContainerView?

    public override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .black
        setupViews()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        backgroundColor = .black
        setupViews()
    }

    private func setupViews() {
        let pc = PlayerContainerView()
        pc.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(pc)

        NSLayoutConstraint.activate([
            pc.topAnchor.constraint(equalTo: contentView.topAnchor),
            pc.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
            pc.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            pc.trailingAnchor.constraint(equalTo: contentView.trailingAnchor)
        ])

        self.playerContainerView = pc
    }

    public func configure(reel: ReelItem, isCurrent: Bool) {
        if isCurrent {
            playerContainerView?.isHidden = false
            if let pc = playerContainerView {
                AVPlayerPool.shared.attachLayer(pc.playerLayer, forSlotIndex: 1)
            }
        } else {
            playerContainerView?.isHidden = true
            playerContainerView?.playerLayer.player = nil
        }
    }

    public override func prepareForReuse() {
        super.prepareForReuse()
        playerContainerView?.playerLayer.player = nil
        playerContainerView?.isHidden = true
    }
}
