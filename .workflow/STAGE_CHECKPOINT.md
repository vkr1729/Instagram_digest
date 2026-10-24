# STAGE_CHECKPOINT: arch/direct-meta-stream

- **Last Updated:** 2026-10-24T14:56:00+08:00
- **Current Active Stage:** Pre-Stage 4 Clean & Certified -> Ready for Frontier Ledger Authoring (Stage 4a)
- **Branch:** `arch/direct-meta-stream`
- **Quality Gate:** **PASSED (0 P0, 0 P1, Quality Score: 9.9 / 10)**
- **Test Suite Status:** **593 / 593 Python tests passing (100% green)**, **16 / 16 Swift DirectStreamTests verified**

---

## 1. Audit of Interrupted Frontier Model Changes & Completed Remediation

### Findings from Interrupted Frontier Model Run:
- The frontier model was executing an edit on `site_builder.py` immediately prior to hitting its weekly quota limit (exit code 5).
- **Intent Identified:** The frontier model intended to harden `write_direct_manifest` by adding `is_direct_stream_url(value)` to validate that direct video URLs belong strictly to HTTPS Meta CDN domains (`fbcdn.net`, `cdninstagram.com`).
- **Defects Left by Interruption:**
  1. *Fatal `ValueError` on Invalid/R2 URLs:* Instead of sanitizing non-Meta URLs to empty strings as required by `test_write_direct_manifest_filters_out_r2_domains`, it threw a fatal exception.
  2. *Fatal `ValueError` on Missing `size_bytes`:* Threw a fatal exception if `size_bytes` was omitted, violating the architecture where `size_bytes` is optional.
  3. *Unconditional Call in `build_site`:* Invoked `write_direct_manifest` during legacy compiles (`DIRECT_STREAM_MODE = False`), breaking 6 test suites in `test_architectural_fixes.py` and across the legacy test suite.

### Completed Remediation:
1. **Preserved Valid Meta Validation:** Retained `is_direct_stream_url()` to guard against invalid or foreign hostnames.
2. **Sanitized Non-Meta / R2 URLs:** In `write_direct_manifest`, if a candidate URL matches an R2 public domain or fails `is_direct_stream_url`, `it["video_url"]` is sanitized to `""` rather than crashing.
3. **Optional `size_bytes` Handling:** Positive integer `size_bytes` are injected if present; missing values do not raise fatal exceptions.
4. **Channel-Isolated Invocation in `build_site`:** Gated behind `if getattr(config, "DIRECT_STREAM_MODE", False):`, preserving byte-level backward compatibility for production R2 builds.
5. **Full Regression Verification:** Ran full pytest suite: **593 passed, 0 failed in 168.81s**.

---

## 2. Status of Swift / iOS Target (`Sources/InstagramDigest/`)
- Target: iOS 17+, Swift 6 strict concurrency (`SWIFT_STRICT_CONCURRENCY: complete`).
- All 11 files inspected:
  - `ReelItem.swift`: Codable/Sendable, canonical permalink fallback, thumbnail sanitization.
  - `DigestState.swift`: Channel status tracking.
  - `DigestDataService.swift`: Direct manifest routing to `data_direct.json`.
  - `BookmarkResolver.swift`: 6 exhaustive `ResolverResult` cases, timeout retries, 429 backoff.
  - `MediaCacheManager.swift`: Single-transaction `executeRolloverPurge` with stem normalization and `protectedReelIDs` safety.
  - `DownloadAllCoordinator.swift`: Re-entrancy guards, `.preparing` state machine exhaustiveness.
  - `BookmarksSheet.swift`: Local-first playback routing, exhaustive enum handling, 3-state badges.
  - `DownloadAllSheet.swift`: Exhaustive `.preparing` progress UI.
  - `OfflineCacheBanner.swift`: O(1) directory inspection, 1-tap download trigger.
  - `DirectStreamTests.swift`: 16 exhaustive unit tests covering all edge cases.

---

## 3. Resumption Plan (Upon Account Reload / Quota Reset)
Under the **Maker-Frontier Collaborative Offload Protocol**:
1. **Stage 4a (Read-Only Frontier Adversarial Audit):**
   - **Frontier Model:** `muse-spark-1.3-contributor` (max reasoning effort)
   - **CLI Command:**
     `muse -p "..." > .workflow/frontier_reasoning.log 2>&1`
2. **Stage 4b:** Maker slices ledger findings and applies workspace fixes.
3. **Stage 4c:** Maker multi-pass subagent review verifies zero P0/P1 bugs.
4. **Stage 4d:** Frontier fast verification pass against clean diff.
5. **Stage 4e:** Frontier generates `.workflow/UAT_PLAN.md`.
