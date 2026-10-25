# Audit & Remediation Report — Direct-Meta Stream (Stage 4d Verification Pass)

Date: 2026-10-25 (UTC) · Branch: `arch/direct-meta-stream` · Scope: `.workflow/AUDIT_LEDGER.json` (88 findings: 6 P0, 36 P1, 46 P2) + `git diff HEAD` (37 files, +3,229/−1,527)
Scale anchor: single-person personal use (Kedarnath Reddy) on iPhone, iOS 17+, Swift 6 strict concurrency.

Supersedes: the 2026-10-24 43-finding report previously in this file (preserved in git history).
Ledger status going in: all 88 findings marked IMPLEMENTED by the maker across Python Pipeline (32), Swift Engine (27), Swift UI (24), CI/CD (5).

## 0. Executive verdict — RELEASE BLOCKED (conditional path to GO)

**Result: 81 PASS / 6 FAIL / 1 PARTIAL across the 88 remediations. All 88 accounted for, zero unverified.**

- All 6 P0 remediations PASS. The safety-critical core (no-destructive-bootstrap, MIN-deploy floor, no force-unwrap crash, thumbnail-preserving rollover, 1-tap banner, stale-URL failure detection) is faithfully implemented.
- All 4 architectural invariants HOLD: byte-for-byte `data.json` preservation, zero-R2 in direct mode, MRT offline capability, container isolation.
- Python regression suite is fully green: **211/211 pytest pass**, independently re-run by the lead auditor in this pass (see §7).
- **6 FAILs block release.** Two are load-bearing: AUDIT-032 (Swift test target does not compile, reds the CI `xcodebuild test` gate and voids all Swift test evidence) and AUDIT-044 (production no-change deploy path crashes). Four are spec-compliance gaps: AUDIT-041, AUDIT-027, AUDIT-070, AUDIT-071. One PARTIAL (AUDIT-079) is a single missing sub-item.
- Swift verdicts rest on inspection plus lead-run `swiftc 6.0` compiler probes (Linux). Full `xcodebuild` proof is delegated to CI per §8 gate (f) — same residual as the prior pass, explicitly carried, not silently dropped.

**Release approval: NOT GRANTED.** The branch is approved to ship once (a) the 7 fix prescriptions in §5 land, (b) `build-ipa.yml` is green on the branch (proving 032 fixed and no further Swift breakage), and (c) the §8 ship gates pass. No other work gates release.

**Certification score: 7.6 / 10 — NOT CERTIFIED (blocked).** Math: 10.0 − 0.8 (032: uncompilable test target blocks all Swift verification + CI) − 0.5 (044: production no-change deploy crash) − 0.3 (041: silent Xcode drift, fails open) − 0.2 × 3 (027/070/071: dead spec promises / dead gates) − 0.1 (079: one missing sub-item) − 0.1 (residual: no `xcodebuild` run possible on Linux). The 0.9 gap to the prior pass's 9.1 is new-finding coverage plus two genuinely load-bearing defects, not regression: prior PASS verdicts were not overturned.

## 1. Method & evidence standard

- Five parallel domain auditors inspected every cited implementation and test body against the ledger remediation specs over `git diff HEAD` / `git diff origin/arch/direct-meta-stream`: Python Pipeline (32 findings), Swift Engine (27), Swift UI (24), CI/CD (5), plus a cross-cutting P0 + invariants auditor (6 P0s + INV-1..4 + concurrency).
- An adversarial critic cross-checked coverage (32+27+24+5 = 88, overlap reconciled) and spot-re-opened the highest-risk bodies. Two critic claims were corrected at lead level (see §6): the AUDIT-032 "4 compile errors" count (3 confirmed, 1 refuted by compiler) and the severity roll-up (041 is P1 per ledger, not "unmapped").
- Lead-auditor triangulation (this pass, all observed first-hand):
  - Full-file reads of every FAIL/PARTIAL site: `site_builder.py` imports + no-change block, `build-ipa.yml` Xcode step, `REQUIREMENTS.md` §5, `DigestManifest.swift` init, `DownloadAllSheet.swift`, `GitHubBookmarkSyncService.swift` sync signature, `GitHubSyncModal.swift` validation/a11y/progress, `MediaCacheManager.swift` actor + `CacheError` nesting + ledger member, and all four AUDIT-032 test sites.
  - `swiftc 6.0` (Linux, Swift 6.0 RELEASE) typecheck probes in `/tmp` (kept out of the repo): verbatim-signature replica probes for the `runDate:` and `Equatable` claims, a pattern-exact actor-nesting probe for the bare-`CacheError` claim, positive controls for all three, and a sensitivity control proving the generic-constraint probe can detect non-conformance. Full transcripts in §7.
  - Independent pytest re-runs: `tests/test_direct_stream_mode.py` (29 passed), plus the full regression set named by the audit (211 passed total across 12 files, zero failures).
  - Repo-wide symbol grep to prove absence claims (`currentTotalBookmarkBytes`, `manifestAgeHours`, CI `grep` gate, contract comments).
- Evidence tiers used in §4: **L1** = lead re-verified with observed output in this pass; **L2** = domain-auditor inspected bodies (child evidence, bodies opened, verdict recorded). Swift PASS verdicts are L2 + L1-probed where noted; none rest on uninspected summaries.
- Residual limitation (non-blocking, carried explicitly): `xcodebuild` cannot run on Linux (no UIKit/SwiftUI/SwiftData SDKs), so Swift compile verdicts rest on `swiftc 6.0` frontend diagnostics plus pattern-exact probes — same Swift major as pinned Xcode 16.2. Final compile proof is delegated to CI gate (f) in §8. Concurrency behavior (022/025/034) is verified by serialization reasoning only, never under live concurrent load.

## 2. Remediation breakdown by domain and severity

| Domain | Total | PASS | FAIL | PARTIAL |
|---|---|---|---|---|
| python_pipeline | 32 | 31 | 044 | — |
| swift_engine | 27 | 23 | 027, 032, 070, 071 | — |
| swift_ui | 24 | 23 | — | 079 |
| ci_cd | 5 | 4 | 041 | — |
| **Total** | **88** | **81** | **6** | **1** |

| Severity | Total | PASS | FAIL | PARTIAL |
|---|---|---|---|---|
| P0 | 6 | 6 | — | — |
| P1 | 36 | 33 | 027, 032, 041 | — |
| P2 | 46 | 42 | 044, 070, 071 | 079 |
| **Total** | **88** | **81** | **6** | **1** |

P0 detail (all PASS, L1 re-verified): 001 destructive-bootstrap deleted, clone failure returns False (`site_builder.py:844-851`); 002 `ceil(0.6*TOP)` floor gate pre-overlay + `_deploy_only` counts direct-manifest items (`site_builder.py:801-808`, `main.py:3254-3270`); 003 throwing repo/URL guards, zero force-unwraps, invalid-repo test (`GitHubBookmarkSyncService.swift:179-193`, `DirectStreamTests.swift:1031`); 004 rollover cleanup restricted to `.mp4`, `.thumb.jpg` untouched (`MediaCacheManager.swift:275-281`); 005 banner embedded, new-week alert, dead params removed (`InstagramDigestApp.swift:350-357,578-587,662`); 006 reachability-gated stored-URL branch + fail/status observers falling back to resolver, teardown invalidates (`BookmarksSheet.swift:982,992,1081-1096,1199-1204`).

## 3. FAIL / PARTIAL detail (all L1 re-verified)

**AUDIT-032 [P1, swift_engine] — FAIL, release blocker.** `Tests/InstagramDigestTests/DirectStreamTests.swift` does not compile; any single error reds the whole test target and the CI `xcodebuild test` gate, voiding all Swift test evidence (including 030/066/069 bodies, unexecutable until fixed).
1. `:258-259` pass `runDate:` to `DigestManifest(...)`; the real init (`DigestManifest.swift:32-39`) has no such parameter (`run_date` exists only as a `CodingKeys` case, `:51`). Compiler: `error: extra argument 'runDate' in call` on a signature-verbatim probe.
2. `:1189` catches `let CacheError.sizeMismatch`; `CacheError` is nested in `public actor MediaCacheManager` (`MediaCacheManager.swift:6,861`) and the test file references it once, bare, with zero qualified uses — unqualified lookup cannot resolve a nested type across files. Compiler: `error: cannot find 'CacheError' in scope` on a pattern-exact probe.
3. `:1307` awaits `manager.currentTotalBookmarkBytes`; repo-wide grep proves no such member exists in `Sources/` — the real ledger member is actor-isolated `totalBookmarkBytes` (`MediaCacheManager.swift:58`, used correctly at `BookmarksSheet.swift:421`).
4. (Claimed 4th error REFUTED — see §6.) Fix: drop `runDate:` (or add the param), qualify `MediaCacheManager.CacheError`, use `totalBookmarkBytes`, re-run `xcodebuild test`.

**AUDIT-044 [P2, python_pipeline] — FAIL, release blocker.** `site_builder.py:951` calls bare `time.time()` but the module has no top-level `import time` (imports at `:7-17`; only local `import time as _time` at `:994`/`:1093`). On the no-change path with a configured `DIRECT_PAGES_MANIFEST_URL`, the `NameError` is swallowed by the GET `except` handler (`:964-965`, mislogged as a GET failure), execution falls through to `git commit` against an empty diff with `check=True` (`:974-978`), which raises `CalledProcessError` — the no-change path can never return `True` in production. Production tests mask it (`DIRECT_PAGES_MANIFEST_URL=''`, `:966-972` early-`True` branch). Fix: use `_time` (import at the top of the no-change block) AND restructure the fall-through to skip commit/push straight to the poll loop per spec; add a regression test with a configured URL on the no-change path.

**AUDIT-041 [P1, ci_cd] — FAIL.** `build-ipa.yml:53` attempts `Xcode_16.2 || Xcode_16.1` but the spec's enforcement gate is missing: no `xcodebuild -version | grep -q 'Xcode 16'` check, and the terminal `|| true` fails open — a runner lacking both keeps the silent-drift defect the finding was raised to kill; the 16.1 fallback is itself an unpinned minor. Fix: add the `grep -q 'Xcode 16' || { echo; exit 1; }` gate, drop the trailing `|| true`.

**AUDIT-070 [P2, swift_engine] — FAIL.** No `manifestAgeHours` helper exists anywhere (repo-wide grep: zero matches) and `generatedAt` is consumed only once (`InstagramDigestApp.swift:437`, feeding a freshness caption, never passed to the sheet or a gate) — the spec's >48h stale-manifest gate ("Week links likely expired — re-sync from workstation") cannot fire; users can bulk-download an expired week. Fix: add the pure helper + gate before queue start per spec.

**AUDIT-027 [P2, swift_engine] — FAIL (docs leg).** Neither spec option was taken: no minimal Release-Asset upload exists (zero release-upload code), `video_asset_name` is still written (`migrate_r2_bookmarks_to_gh.py:166`; DTO field `GitHubBookmarkSyncService.swift:13,92,332`), and `REQUIREMENTS.md:62-65` still mandates Single Repo + Release Assets with no "resolver-only video restore; Release Assets deferred" amendment — the docs promise what the code does not do. Fix (cheapest): amend REQUIREMENTS per spec option (b) and stop writing the field.

**AUDIT-071 [P2, swift_engine] — FAIL.** Zero contract markers: no twin file+line comment on either manifest-URL constant (`DigestDataService.swift` has no twin/contract comment) and no CI assertion (no `grep`/`data_direct.json`/`contract` match under `.github/`) — an env-override drift still silently breaks iOS. Fix: add both one-line comments + the one-line CI `grep -q 'Instagram_digest/data_direct.json' ... || exit 1` per spec.

**AUDIT-079 [P2, swift_ui] — PARTIAL (4/5 sub-items).** Done: empty-PAT disable + repo `owner/name` validation with inline hint, a11y ids (`GitHubRepoField`, `GitHubPATField`, `GitHubSyncButton`), `isSyncing` token with delayed-dismiss guard (`GitHubSyncModal.swift:12,33-38,57,68,96-112,138-160`); modal shows only an indeterminate `ProgressView` (`:97`). Missing: the progress closure — `syncWithGitHub(repo:pat:localBookmarks:context:)` (`GitHubBookmarkSyncService.swift:170-175`) exposes no `(downloaded/total)` hook, so per-thumbnail progress cannot be wired. Fix: add the engine progress callback first, then wire it into the modal's `ProgressView`.

## 4. Full 88-finding verdict table

Tier: L1 = lead re-verified with observed output; L2 = domain-auditor inspected bodies. Swift compile/behavior legs are inspection + `swiftc` probes; final proof delegated to CI gate (f).

| ID | Domain | Sev | Verdict | Evidence / proof pointer |
|---|---|---|---|---|
| 001 | python | P0 | PASS (L1) | Clone-fail returns False, no orphan/init; tests assert `git init` never invoked |
| 002 | python | P0 | PASS (L1) | `ceil(0.6*TOP)` floor pre-overlay; `_deploy_only` counts direct-manifest items |
| 003 | engine | P0 | PASS (L1) | Throwing repo/URL guards, zero force-unwraps; invalid-repo throw test |
| 004 | engine | P0 | PASS (L1) | Rollover cleanup `.mp4`-only; `.thumb.jpg` preserved |
| 005 | UI | P0 | PASS (L1) | Banner embedded, new-week alert, dead params removed |
| 006 | UI | P0 | PASS (L1) | Stored-URL reachability gate + fail/status observers → resolver fallback |
| 007 | python | P1 | PASS (L2) | `isinstance(str)` candidate guard; non-string drop-not-crash test |
| 008 | python | P1 | PASS (L2) | `_pick_playable` validity-ordered selection at all 4 sites |
| 009 | python | P1 | PASS (L2) | `--expand` direct-mode MIN gate; no expired-reel deletion |
| 010 | python | P1 | PASS (L2) | Sync shrink MIN-gated when `deploy=False` |
| 011 | python | P1 | PASS (L2) | R2 `video_url` clobber removed before direct writer |
| 012 | python | P1 | PASS (L2) | Thumbnail prune no longer deletes production thumbs on unreadable remote |
| 013 | python | P1 | PASS (L2) | Playwright-fallback CDN URL threaded through, not discarded |
| 014 | python | P1 | PASS (L2) | `~/.env` override surfaced; startup mode logged |
| 015 | python | P1 | PASS (L2) | 3 direct-mode tests execute production filter code |
| 016 | python | P1 | PASS (L2) | Bootstrap test asserts failure, not the destructive outcome |
| 017 | python | P1 | PASS (L2) | Migration skips failed downloads; size-validated uploads |
| 018 | python | P1 | PASS (L2) | Migration carries thumbnails; restore contract holds |
| 019 | python | P1 | PASS (L2) | Healthcheck Pages gate covers `data_direct.json` |
| 020 | engine | P1 | PASS (L2) | Duplicate-tap guard covers `.downloading`, not just `.preparing` |
| 021 | engine | P1 | PASS (L2) | Preflight excludes pinned Bookmarks + protected files |
| 022 | engine | P1 | PASS (L2) | 403/410 refresh serialized; sticky pause preserved (no live-load proof) |
| 023 | engine | P1 | PASS (L2) | Direct-mode remove defers bound (playing) file like legacy |
| 024 | engine | P1 | PASS (L2) | Direct add records `.cached` only after copy success |
| 025 | engine | P1 | PASS (L2) | True LRU, playing-video protection, Unlimited honored (no live-load proof) |
| 026 | engine | P1 | PASS (L2) | Resolver preserves cancellation/programmer error; `.offline` narrowed |
| 027 | engine | P1 | FAIL (L1) | §3: no upload, field still written, REQUIREMENTS unamended |
| 028 | engine | P1 | PASS (L2) | Isolated-copies vs plan conflict resolved per spec |
| 029 | engine | P1 | PASS (L2) | Rollover resume awaited; suspension race closed |
| 030 | engine | P1 | PASS (L2) | Ledger decremented only after verified deletion |
| 031 | engine | P1 | PASS (L2) | Thumbnail PUT carries SHA; remote delete path exists |
| 032 | engine | P1 | FAIL (L1) | §3: 3 compiler-confirmed errors; test target uncompilable |
| 033 | UI | P1 | PASS (L2) | Share-sheet identity stabilized (no per-pass UUID) |
| 034 | UI | P1 | PASS (L2) | Badges observe coordinator; live-update (no race-load proof) |
| 035 | UI | P1 | PASS (L2) | `onAppear` preserves finished state / green badge |
| 036 | UI | P1 | PASS (L2) | Live failed-count in banner + sheet progress |
| 037 | UI | P1 | PASS (L2) | Offline playlist observes connectivity; restores on reconnect |
| 038 | UI | P1 | PASS (L2) | Cloudflare Owner-Key dialog + remote calls excised |
| 039 | UI | P1 | PASS (L2) | Share path iPad-safe, no temp leak, failure surfaced |
| 040 | CI/CD | P1 | PASS (L1) | `python-tests` job: 3.12, pip cache, `pytest tests/test_direct_stream_mode.py -q` |
| 041 | CI/CD | P1 | FAIL (L1) | §3: `|| true` fails open, no `grep 'Xcode 16'` gate |
| 042 | CI/CD | P1 | PASS (L1) | XcodeGen `curl -sfSL` + pinned sha256 verified vs upstream 2.42.0 |
| 043 | python | P2 | PASS (L2) | Poll verifies URL validity, not just `run_date` |
| 044 | python | P2 | FAIL (L1) | §3: `NameError` + fall-through-to-empty-commit; no-change path crashes |
| 045 | python | P2 | PASS (L2) | Quorum uses ceiling floor for small manifests |
| 046 | python | P2 | PASS (L2) | Writer drops id-less items, canonical URL, sane `size_bytes` |
| 047 | python | P2 | PASS (L2) | R2/Worker vars no longer leak into direct HTML |
| 048 | python | P2 | PASS (L2) | Health session check green in cookie-free mode |
| 049 | python | P2 | PASS (L2) | Manifest URL unfrozen, Cobalt URL validated, sentinel live |
| 050 | python | P2 | PASS (L2) | Deploy defaults resolved at call time, not import |
| 051 | python | P2 | PASS (L2) | Migration paths/counts/tags parameterized; docstring current |
| 052 | python | P2 | PASS (L2) | Test batch file + data dir share one tmp tree |
| 053 | python | P2 | PASS (L2) | Zero-playable path cites real outbox; resume set pre-drop |
| 054 | python | P2 | PASS (L2) | Expand log arithmetic + leftover from post-filter list |
| 055 | python | P2 | PASS (L2) | `storage_r2` import lazy; zero-cloud runs without boto3 |
| 056 | python | P2 | PASS (L2) | `is_direct_stream_url` strips padding before verdict |
| 057 | python | P2 | PASS (L2) | Reconcile scans stranded outbox on unreadable digest |
| 058 | python | P2 | PASS (L2) | `.env.example` covers new auth fallbacks |
| 059 | python | P2 | PASS (L2) | Writer strips `local_url`, no `video_cdn_url` duplication |
| 060 | engine | P2 | PASS (L2) | `ReelItem` rejects `r2_url`/`local_url` fallback |
| 061 | engine | P2 | PASS (L2) | Fallback URLs sanitize hostile ids |
| 062 | engine | P2 | PASS (L2) | Fallback thumb name matches pipeline writer |
| 063 | engine | P2 | PASS (L2) | Resolver endpoint validated, not snapshotted raw |
| 064 | engine | P2 | PASS (L2) | Resolver HTTPS check case-insensitive + host-checked |
| 065 | engine | P2 | PASS (L2) | `fetchManifest` refuses explicit R2/worker URL in directMode |
| 066 | engine | P2 | PASS (L2) | `promotePartFile` upper bound + playability check |
| 067 | engine | P2 | PASS (L2) | Resume keyed by `(weekID,reelID)`; bookmark filter fixed |
| 068 | engine | P2 | PASS (L2) | Redundant `??` removed; tasks/prefetch cleaned; date fallback sane |
| 069 | engine | P2 | PASS (L2) | Strict API-key/cancellation/empty-manifest assertions |
| 070 | engine | P2 | FAIL (L1) | §3: no `manifestAgeHours`, `generatedAt` never gated |
| 071 | engine | P2 | FAIL (L1) | §3: no twin comments, no CI grep gate |
| 072 | UI | P2 | PASS (L2) | Spec-banned finished-state copy removed from banner |
| 073 | UI | P2 | PASS (L2) | Dead header position/count params removed |
| 074 | UI | P2 | PASS (L2) | `loadManifest` tasks serialized; Retry-spam race closed |
| 075 | UI | P2 | PASS (L2) | Banner count scan single-tasked; stale-write race closed |
| 076 | UI | P2 | PASS (L2) | 9:16 cells adaptive; redundant modifier removed |
| 077 | UI | P2 | PASS (L2) | Detox single source of truth; covers feed + bookmarks |
| 078 | UI | P2 | PASS (L2) | Sort no longer refires on Settings open/close |
| 079 | UI | P2 | PARTIAL (L1) | §3: 4/5 done; engine progress closure missing |
| 080 | UI | P2 | PASS (L2) | Empty bookmarks state offers GitHub-restore CTA |
| 081 | UI | P2 | PASS (L2) | Settings persistence unified (no keystroke/Done split) |
| 082 | UI | P2 | PASS (L2) | Import/export banners mutually aware; errors clearable |
| 083 | UI | P2 | PASS (L2) | Grid title reflects filtered subset |
| 084 | UI | P2 | PASS (L2) | Sheet chaining completion-driven, not fixed-delay |
| 085 | UI | P2 | PASS (L2) | Share downloads structured + cancellable |
| 086 | UI | P2 | PASS (L2) | Singleton observation + badge-input + Reachability hygiene |
| 087 | CI/CD | P2 | PASS (L1) | `.gitignore` covers `*.xcodeproj/`, `*.xcresult/` (check-ignore confirmed) |
| 088 | CI/CD | P2 | PASS (L1) | Sim boot tolerates booted device; IPA step fail-fast on empty path |

## 5. Architectural invariant confirmation (all HOLD, L1/L2)

- **INV-1 — 100% byte-for-byte production `data.json` preservation: HOLD.** SHA-before + existence gate (`site_builder.py:853-858`), SHA-after abort (`:923-929`); overlay writes only `data_direct.json`/thumbs/`.nojekyll`; the `git-init` path at `:1068` is legacy-only (direct early-returns at `:1046`). Python byte-identity tests green (`tests/test_direct_stream_mode.py:806-868`).
- **INV-2 — Zero R2 storage/bandwidth in direct mode: HOLD.** Every `storage_r2` call site is unreachable in direct mode: `_publish_digest` (`main.py:389-390`), reconcile early-return (`:624-625`), 5E upload pool inside the non-direct `else` (`:2303-2347`), expand (`:3057`/`:3071`), estimate (`:3218`), purges (`:2561-2563`). Poisoned-R2 hermetic sync/expand/build tests green. Non-destructive-refactor invariant respected: `storage_r2.py` etc. retained, bypassed by toggle.
- **INV-3 — MRT offline capability: HOLD.** Disk-cache fallback (`DigestDataService.swift:110-122`), bundle fallback (`:124-136`), local-file-first playback (`AVPlayerPool.swift:487-489`, `resolvedLocal ?? remote`); honest offline badges for expired/older bookmarks (006, 037).
- **INV-4 — Container isolation: HOLD.** Application Support roots (`LibraryPathResolver.swift:18-27`), traversal guard (`:38-49`); `/tmp` only for transient share-staging with cleanup (`InstagramDigestApp.swift:1062,1196`).
- **Concurrency isolation: HOLD by inspection + probes (no live-load proof).** `@MainActor` on `DownloadAllCoordinator`, `BookmarkController`, `GitHubBookmarkSyncService`; `Sendable` value types; `weak-self` Task/observer closures; observer invalidation in teardown. Runtime behavior of the 403-refresh/MainActor fixes (022/025/034) verified by serialization reasoning only. Residual: exercise under concurrent load on device (UAT §6 soak) and keep `SWIFT_STRICT_CONCURRENCY=complete` clean in CI.

## 6. Discrepancies found and resolved at lead level

1. **AUDIT-032 "4 compile errors" → 3 confirmed, 1 refuted.** The critic's 4th claim (`XCTAssertEqual(err, .emptyManifest)` at `:249` fails for lack of `Equatable`) is wrong: payload-less Swift enums implicitly conform to `Equatable`. Isolated `swiftc 6.0` probe with a verbatim `Error, LocalizedError` enum compiles clean (exit 0, zero diagnostics), while a control struct with a closure member correctly errors (`requires that 'NotEq' conform to 'Equatable'`), proving the probe detects non-conformance. All three positive controls (no-`runDate` call, `Equatable` enum, qualified `CacheError`) compile clean. FAIL verdict stands — one error is enough to red the target — but the evidence record is corrected.
2. **Severity roll-up corrected.** The synthesis left 041 "unmapped"; the ledger records 041 as P1, so P1 is 33/36 PASS (FAIL 027/032/041) and P2 is 42/46 + 1 PARTIAL (FAIL 044/070/071). Totals unchanged: 81/6/1.
3. **"No Swift toolchain on Linux" corrected.** `swiftc` 6.0 (Swift 6.0 RELEASE) is installed; all compiler claims above were executed, not inferred. What remains Linux-impossible is only full `xcodebuild` (UIKit/SwiftUI/SwiftData SDKs) — e.g. the real `DigestManifest.swift` cannot typecheck on Linux because corelibs-foundation exposes `URLResponse` as `AnyObject` (observed: `non-nominal type 'URLResponse' cannot be extended`), hence verbatim-signature probes.
4. **AUDIT-044 mechanics made precise.** The defect is not "returns False": the `NameError` is swallowed by the GET handler and execution falls through to `git commit` on an empty diff with `check=True`, raising `CalledProcessError`. Observable outcome is the same class (no-change path never succeeds with a configured URL) but the fix must also restructure the fall-through, not just the import.

## 7. Observed evidence transcripts (lead)

pytest (this pass, Linux, repo `.venv`):
- `.venv/bin/python -m pytest tests/test_direct_stream_mode.py -q` → `29 passed in 0.73s`
- `... test_direct_stream_mode.py test_architectural_fixes.py test_notifier.py test_site_builder.py test_cookie_free_uat.py -q` → `95 passed in 16.72s`
- `... test_upload_ordering.py test_sync_resume.py test_expand_resume.py test_durability.py test_teardown_final.py test_fable_audit_fixes.py test_stalled_job_safeguards.py -q` → `116 passed in 13.07s`
- Total: 95 + 116 = **211 passed, 0 failed** — matches the audit's regression claim exactly (29 direct-stream + 182 surrounding).

swiftc probes (this pass, `/tmp/audit032_*.swift`, kept out of the repo):
- Signature-verbatim probe → `/tmp/audit032_sig_probe.swift:22:59: error: extra argument 'runDate' in call` (replica of test `:258`).
- Actor-nesting probe → `/tmp/audit032_nested_probe.swift:14:15: error: cannot find 'CacheError' in scope` (replica of test `:1189`).
- Exhaustive grep: `currentTotalBookmarkBytes` — zero matches in `Sources/`; real member `totalBookmarkBytes` at `MediaCacheManager.swift:58` (replica of test `:1307` failure).
- Equatable isolation probe → clean (exit 0, no diagnostics); sensitivity control → `error: ... requires that 'NotEq' conform to 'Equatable'`; positive-control file → clean.
All greps above ran against the working tree on `arch/direct-meta-stream` in its HEAD state for this pass.

## 8. Ship gates and final certification

Release is approved when ALL of the following hold; (a)-(e) are code fixes, (f)-(h) are verification:

- (a) AUDIT-032 fixed: `DirectStreamTests.swift` compiles; `:258-259`, `:1189`, `:1307` corrected.
- (b) AUDIT-044 fixed: no-change path with configured URL verified by a new regression test (marker written, `True` returned, no commit attempted).
- (c) AUDIT-041 fixed: `grep -q 'Xcode 16'` gate present, trailing `|| true` removed.
- (d) AUDIT-027/070/071 fixed per §3 prescriptions (docs amendment + field removal; stale-gate helper + wiring; twin comments + CI grep).
- (e) AUDIT-079 completed: engine progress callback added and wired to the modal `ProgressView`.
- (f) `build-ipa.yml` green on the branch (unit + UI tests + release gates) — the authoritative close-out for all Swift compile/concurrency verdicts.
- (g) Full pytest regression re-run green (≥211 passed) with `data/` pollution check clean (no new files, `data.json` byte-identical).
- (h) Stage 4e UAT (`.workflow/UAT_PLAN.md`) executed: automated matrix green + on-device checklist signed off by the user.

**Final certification: 7.6 / 10 — NOT CERTIFIED, RELEASE BLOCKED.** The safety core (6/6 P0s, 4/4 invariants, 211/211 pytest) is genuinely strong, but a release cannot be certified while the Swift test target does not compile, the production no-change deploy path crashes, and the toolchain pin fails open. On gates (a)-(h) passing, this report's machinery re-scores to ≥9.0 and the lead recommends GO without further audit.

— Lead Auditor / Independent QA Lead, Stage 4d, 2026-10-25.

