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
}
