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
