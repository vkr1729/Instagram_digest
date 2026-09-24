import Foundation

/// Pure business rules for watched status calculation and predecessor marking.
public enum WatchedRules {
    /// Computes the unrecorded reel IDs for all predecessors from index 0 up to (targetIndex - 1).
    /// Items already present in `alreadyWatched` are excluded.
    public static func unrecordedPredecessorIDs(
        items: [ReelItem],
        targetIndex: Int,
        alreadyWatched: Set<String>
    ) -> [String] {
        guard targetIndex > 0, !items.isEmpty else { return [] }
        let clampedTarget = min(targetIndex, items.count)
        var result: [String] = []
        for i in 0..<clampedTarget {
            let predID = items[i].id
            if !alreadyWatched.contains(predID) {
                result.append(predID)
            }
        }
        return result
    }

    /// Stable content fingerprint for the per-week watch timer. Any content
    /// refresh (new week, re-rank, add/remove) resets the timer; an identical
    /// manifest across launches restores the accumulated time.
    /// Uses FNV-1a over reel ids: Swift.Hasher is process-seeded and would
    /// differ every launch, wiping the timer on each cold start.
    public static func watchContentFingerprint(items: [ReelItem]) -> String {
        var hash: UInt64 = 14_695_976_037_393_838_691
        for id in items.map(\.id) {
            for byte in id.utf8 {
                hash ^= UInt64(byte)
                hash &*= 1_099_511_628_211
            }
            hash ^= 0xFF
            hash &*= 1_099_511_628_211
        }
        return "\(items.count)#\(String(hash, radix: 16))"
    }

    /// True when the stored fingerprint no longer matches the fresh manifest
    /// (or was never stored): the timer must restart from zero.
    public static func shouldResetWatchTime(storedFingerprint: String?, freshFingerprint: String) -> Bool {
        storedFingerprint != freshFingerprint
    }

    /// Pure rule for resolving startup resume index given week IDs, saved reel ID, saved index, and items.
    /// Resets to 0 if weekID differs from previous week (weekly rollover rule).
    /// Otherwise prioritizes savedReelID match, followed by savedIndex fallback, clamped to [0, items.count - 1].
    public static func resolveResumeIndex(
        currentWeekID: String,
        previousWeekID: String?,
        savedReelID: String?,
        savedIndex: Int?,
        items: [ReelItem]
    ) -> Int {
        guard !items.isEmpty else { return 0 }
        if let prev = previousWeekID, !prev.isEmpty, prev != currentWeekID {
            return 0
        }
        if let id = savedReelID, let matchIdx = items.firstIndex(where: { $0.id == id }) {
            return matchIdx
        }
        if let idx = savedIndex, idx >= 0 && idx < items.count {
            return idx
        }
        return 0
    }
}
