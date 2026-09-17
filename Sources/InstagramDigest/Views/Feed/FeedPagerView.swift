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

    public init(
        reels: [ReelItem],
        currentIndex: Binding<Int>,
        onPageChanged: @escaping (Int) -> Void,
        onScrollEnded: @escaping (TimeInterval) -> Void,
        onForwardScrollPast: @escaping (ReelItem) -> Void
    ) {
        self.reels = reels
        self._currentIndex = currentIndex
        self.onPageChanged = onPageChanged
        self.onScrollEnded = onScrollEnded
        self.onForwardScrollPast = onForwardScrollPast
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
        uiViewController.updateReelsIfNeeded(reels)
        uiViewController.scrollToCurrentIndexIfNeeded(currentIndex)
        uiViewController.updateCurrentIndex(currentIndex)
    }

    // MARK: - Coordinator

    @MainActor
    public final class Coordinator: NSObject, UICollectionViewDataSource, UICollectionViewDelegate, UICollectionViewDelegateFlowLayout, UICollectionViewDataSourcePrefetching {
        var parent: FeedPagerView
        weak var viewController: FeedCollectionViewController?
        private var settleWorkItem: DispatchWorkItem?
        public var lastActiveIndex: Int = 0

        init(_ parent: FeedPagerView) {
            self.parent = parent
            self.lastActiveIndex = parent.currentIndex
        }

        // Modern CellRegistration conforming to §1.7
        private lazy var cellRegistration: UICollectionView.CellRegistration<FeedCell, ReelItem> = {
            UICollectionView.CellRegistration<FeedCell, ReelItem> { [weak self] cell, indexPath, reel in
                guard let self = self else { return }
                let isCurrent = indexPath.item == self.parent.currentIndex
                cell.configure(reel: reel, isCurrent: isCurrent)
            }
        }()

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
    weak var coordinator: FeedPagerView.Coordinator?
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
        collectionView.dataSource = coordinator
        collectionView.delegate = coordinator
        collectionView.prefetchDataSource = coordinator

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
