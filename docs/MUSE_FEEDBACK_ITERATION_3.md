muse: workspace root: /home/kedarnath-reddy-vallaboina/Instagram_digest_ios (cwd default)
muse: Agent delegation: auto unavailable: workspace is untrusted.
muse: retrying meta model stream in 1000ms (attempt 2/10)
## Verdict: ARCHITECTURAL CONTRACT VERIFIED: READY FOR IMPLEMENTATION

Reviewed [IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md](/home/kedarnath-reddy-vallaboina/Instagram_digest_ios/docs/IOS_DECISIONS_AND_IMPLEMENTATION_PLAN.md) (Hardened Revision 3) against all 14 Iteration 2 requirements. Every item is faithfully incorporated; I found no material conflicts and no dropped behaviors.

Verification per item:

1. **LivePinSet (R1.1)** — Verified. §1.2 serializes all deletions in `MediaCacheManager`, states the exact formula (`localStatus == .cached ∧ file exists ∪ activeReelIDs`), tolerates missing files, and never deletes bound files. §1.3 adds the LRU never-evicts-bound rule.
2. **Ephemeral stream fallback (R1.2)** — Verified. §1.3: no disk writes, no LRU churn, no `localStatus` change; re-entry only via per-row "Keep Offline" through LRU admission.
3. **Storage ledger (R1.3/R1.4)** — Verified. §1.3: in-memory `totalBookmarkBytes` reconciled once on launch, gauge counts cached bookmark MP4 bytes only (excludes `.part` + week cache), and `freeBookmarkStorage()` follows the exact 5-step suspend/delete/flip/reset/resume sequence with the button disabled in-flight.
4. **Long-press vs. scroll (R2.1)** — Verified. §1.5: `allowableMovement: 10`, post-fire both pans require long-press failure, `suppressNextTap` set on fire — plus 500ms/350ms tap guards, which don't conflict.
5. **SeekPan subclass (R2.2)** — Verified. §1.5 + file map: fails fast on `|Δy|×1.4 ≥ |Δx|` before 18pt, no simultaneous recognition with collection pan, commit strictly on `.ended` with local/remote tolerances.
6. **Pre-warmed haptics (R2.3)** — Verified. §1.5: single persistent `.medium` generator, `prepare()` on hold-arm + after each impact + once on feed appear, `@MainActor` callbacks.
7. **Dismissible sheet (R3.1)** — Verified. §1.6: dismissible, continues while foregrounded, suspends on background. Consistent with the foreground-only contract.
8. **Flash contention (R3.2)** — Verified. §1.6: concurrency 3 idle / 1 playing, 10s download-suspend watchdog before any strike, `.serious`/`.critical` thermal degradation, `.utility` serial session, atomic promotion.
9. **Storage preflight (R3.3)** — Verified. §1.6: `URLVolumeAvailableCapacityForKey`, manifest total (or N×20MB) + 1GB headroom, refuse-with-alert, persisted `resumeData`.
10. **Restored Iteration 1 (R4.1)** — All verified: full `AudioSessionCoordinator` machine (§1.4), dual-branch `automaticallyWaitsToMinimizeStalling` (§1.4), faststart contract (§1.1), watched rules + 50/day modal (§1.5), grid pipeline + share default (§1.5/§1.7), no mute pill (§1.5), memory-warning pool collapse (§1.4).
11. **Info.plist (R4.2)** — Verified. §1.4 + file map: no `UIBackgroundModes: audio`, foreground-only.
12. **CollectionView coordinator (R4.3)** — Verified. §1.7 + §1.4: `CellRegistration`, `prepareForReuse` detach, `scrollViewDidEndDecelerating` + 80ms debounce, `AVQueuePlayer` × 3, prefetching.
13. **sizeBytes (R4.4)** — Verified. §1.1 + §1.6 + file map: optional field, graceful skip if absent, 2-strike in-memory ladder.
14. **SwiftData/platform (R4.5)** — Verified. §1.2 + §3: main-read/bg-write, debounced save, fetch-or-insert dedupe, Application Support URL, CloudKit off, per-file protection, `complete` concurrency.

### Final Phase 1 tips (minor polish, non-blocking)

- **Concurrency state table:** §1.6 ties concurrency 3 to "sheet visible and idle." Define it purely on playback state instead (playing → 1, idle/paused → 3, regardless of sheet visibility) so the dismissed-but-downloading case is unambiguous.
- **`freeBookmarkStorage` ∩ LivePinSet:** explicitly filter purge candidates against `activeReelIDs`; skip both deletion and row-flip for bound IDs (partial purge) rather than deleting a playing file.
- **`suppressNextTap`:** implement as atomic check-and-clear in the tap handler.
- **Wording:** rephrase "continues in the background while foregrounded" to "continues after dismissal while foregrounded"; rename "Watched Marking (3 Triggers)" to "4 Rules" since jump-to-N is a fourth bullet.
- **Zone routing:** bookmark hold (center-lower) and 2.0x latch (upper-right) both use 0.5s holds — route the long-press fire explicitly by touch zone.
- **"Keep Offline" admission:** define as evict-oldest-non-bound until fit; if everything remaining is bound, refuse with an alert.
- **First code to write:** `LibraryPathResolver` + `MediaCacheManager` actor (LivePinSet, ledger, LRU) — everything else builds on it.
