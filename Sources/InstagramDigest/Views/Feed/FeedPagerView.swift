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
        uiViewController.updateReelsIfNeeded(reels)
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
            let location = sender.location(in: view)
            let width = view.bounds.width
            let height = view.bounds.height
            guard width > 0, height > 0 else { return }

            let normX = location.x / width
            let normY = location.y / height

            switch sender.state {
            case .began:
                if normX > 0.65 && normY <= 0.65 {
                    suppressNextTap = true
                    hapticGenerator.impactOccurred()
                    hapticGenerator.prepare()
                    parent.onToggleLatched2x()
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
            let location = sender.location(in: view)
            let width = view.bounds.width
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
            return collectionView.dequeueConfiguredReusableCell(using: cellRegistration, for: indexPath, item: reel)
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

        private func handleScrollSettle(_ scrollView: UIScrollView) {
            settleWorkItem?.cancel()
            let workItem = DispatchWorkItem { [weak self] in
                guard let self = self else { return }
                guard !self.parent.reels.isEmpty else { return }
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
                }
            }
            settleWorkItem = workItem
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.08, execute: workItem)

        }

        // MARK: - Prefetching

        public func collectionView(_ collectionView: UICollectionView, prefetchItemsAt indexPaths: [IndexPath]) {
            // Memory-efficient image prefetching
        }
    }
}

public final class FeedCollectionViewController: UICollectionViewController {
    var coordinator: FeedPagerView.Coordinator?
    private var currentReels: [ReelItem] = []
    private var lastScrolledIndex: Int = -1
    private var currentAttachedIndex: Int = -1
    private var notificationTokens: [NSObjectProtocol] = []

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
                self?.reattachPlayerToVisibleCell()
            }
        )

        notificationTokens.append(
            NotificationCenter.default.addObserver(
                forName: UIApplication.willEnterForegroundNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                self?.reattachPlayerToVisibleCell()
            }
        )
    }

    deinit {
        for token in notificationTokens {
            NotificationCenter.default.removeObserver(token)
        }
    }

    public func reattachPlayerToVisibleCell() {
        for cell in collectionView.visibleCells {
            if let indexPath = collectionView.indexPath(for: cell),
               let feedCell = cell as? FeedCell,
               indexPath.item == currentAttachedIndex {
                feedCell.playerContainerView?.isHidden = false
                if let pc = feedCell.playerContainerView {
                    AVPlayerPool.shared.attachLayer(pc.playerLayer, forSlotIndex: 1)
                }
            }
        }
    }

    /// Only reloads data when reels collection identity actually changes (prevents reloadData churn)
    public func updateReelsIfNeeded(_ newReels: [ReelItem]) {
        if currentReels != newReels {
            self.currentReels = newReels
            self.currentAttachedIndex = -1
            // Reset so scrollToCurrentIndexIfNeeded(0) fires on category switch
            // even when the previous list was already parked at index 0.
            self.lastScrolledIndex = -1
            collectionView.reloadData()
        }
    }

    /// Reconfigures visible cells on index change to attach/detach player layers without reloadData churn
    public func updateCurrentIndex(_ newIndex: Int) {
        guard newIndex != currentAttachedIndex, newIndex >= 0, newIndex < currentReels.count else { return }
        self.currentAttachedIndex = newIndex

        for cell in collectionView.visibleCells {
            if let indexPath = collectionView.indexPath(for: cell),
               let feedCell = cell as? FeedCell,
               indexPath.item < currentReels.count {
                let isCurrent = indexPath.item == newIndex
                let reel = currentReels[indexPath.item]
                feedCell.configure(reel: reel, isCurrent: isCurrent)
            }
        }
    }

    public func scrollToCurrentIndexIfNeeded(_ index: Int) {
        guard index != lastScrolledIndex, index >= 0, index < currentReels.count else { return }
        lastScrolledIndex = index
        coordinator?.lastActiveIndex = index
        let indexPath = IndexPath(item: index, section: 0)
        collectionView.scrollToItem(at: indexPath, at: .centeredVertically, animated: false)
        updateCurrentIndex(index)
    }

    public override func viewWillTransition(to size: CGSize, with coordinator: UIViewControllerTransitionCoordinator) {
        super.viewWillTransition(to: size, with: coordinator)
        coordinator.animate { _ in
            self.collectionView.collectionViewLayout.invalidateLayout()
        }
    }
}

/// Custom UICollectionViewCell with direct PlayerContainerView attachment and immediate teardown on reuse
public final class FeedCell: UICollectionViewCell {
    public private(set) var playerContainerView: PlayerContainerView?
    private var thumbnailImageView: UIImageView?
    private var currentThumbnailURL: URL?

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
        // 1. Thumbnail Image View (backdrop)
        let iv = UIImageView()
        iv.contentMode = .scaleAspectFill
        iv.clipsToBounds = true
        iv.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(iv)

        // 2. Player Layer Container View
        let pc = PlayerContainerView()
        pc.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(pc)

        NSLayoutConstraint.activate([
            iv.topAnchor.constraint(equalTo: contentView.topAnchor),
            iv.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
            iv.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            iv.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),

            pc.topAnchor.constraint(equalTo: contentView.topAnchor),
            pc.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
            pc.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            pc.trailingAnchor.constraint(equalTo: contentView.trailingAnchor)
        ])

        self.thumbnailImageView = iv
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

        // Load thumbnail image if available with identity guard
        self.currentThumbnailURL = reel.thumbnailUrl
        if let thumbURL = reel.thumbnailUrl {
            ImagePipeline.shared.loadImage(from: thumbURL) { [weak self] img in
                guard self?.currentThumbnailURL == thumbURL else { return }
                self?.thumbnailImageView?.image = img
            }
        } else {
            thumbnailImageView?.image = nil
        }
    }

    public override func prepareForReuse() {
        super.prepareForReuse()
        // Immediate player layer detachment conforming strictly to §1.7
        currentThumbnailURL = nil
        playerContainerView?.playerLayer.player = nil
        playerContainerView?.isHidden = true
        thumbnailImageView?.image = nil
    }
}

/// URLCache & NSCache backed image decompression pipeline conforming to §1.7
public final class ImagePipeline: @unchecked Sendable {
    public static let shared = ImagePipeline()
    private let cache = NSCache<NSURL, UIImage>()
    private var memoryWarningToken: NSObjectProtocol?

    private init() {
        cache.countLimit = 150
        cache.totalCostLimit = 50 * 1024 * 1024 // 50 MB decoded image memory cache

        memoryWarningToken = NotificationCenter.default.addObserver(
            forName: UIApplication.didReceiveMemoryWarningNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.cache.removeAllObjects()
        }
    }

    deinit {
        if let token = memoryWarningToken {
            NotificationCenter.default.removeObserver(token)
        }
    }

    public func loadImage(from url: URL, completion: @escaping @MainActor (UIImage?) -> Void) {
        if let cached = cache.object(forKey: url as NSURL) {
            Task { @MainActor in
                completion(cached)
            }
            return
        }

        Task.detached(priority: .userInitiated) { [weak self] in
            guard let (data, _) = try? await URLSession.shared.data(from: url),
                  let rawImage = UIImage(data: data) else {
                Task { @MainActor in completion(nil) }
                return
            }

            // Thread-safe modern decompression
            let format = UIGraphicsImageRendererFormat()
            format.scale = 1.0
            format.opaque = true
            let renderer = UIGraphicsImageRenderer(size: rawImage.size, format: format)
            let decompressed = renderer.image { _ in
                rawImage.draw(at: .zero)
            }

            let cost = Int(rawImage.size.width * rawImage.scale * rawImage.size.height * rawImage.scale * 4)
            self?.cache.setObject(decompressed, forKey: url as NSURL, cost: cost)

            Task { @MainActor in
                completion(decompressed)
            }
        }
    }
}
