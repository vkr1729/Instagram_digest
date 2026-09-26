# Recommendations — Instagram Digest

Enhancements ranked by value to the single owner. Each: problem → proposal →
effort (S/M/L) → why it pays off at personal scale. App-side items are
verifiable through the existing macOS GitHub Actions workflow (unit +
XCUITest in `.github/workflows/build-ipa.yml`) — no local Xcode required.
Fewer moving parts beats more features throughout; several items here also
absorb bugs from `REVIEW_AND_BUGFIXES.md` so the fix and the value land
together.

---

## 1. Restore trust in the app verification suite (M)

**Problem:** The XCUITest suite currently cannot pass on a clean runner
(REVIEW A1), and where it does run, most assertions can never fail —
existence-only checks (A14), self-cancelling comparisons (A13), `if`-gated
assertions (A16), and unit tests that exercise re-implemented copies of
production logic (A15). CI is red for the wrong reason and green for the
wrong reason. With no local Xcode, this workflow is the *only* feedback
loop between an edit and the phone.

**Proposal:** One coherent pass: (a) fix the owner-key alert collision
(A1), (b) convert navigation/filter/paging tests to state-transition
assertions on `ReelRankBadge.label` and chip `isSelected` accessibility
values, (c) replace the mirrored unit-test logic with extracted production
statics (`resolveSpatialZone`, `shouldFailSeekForVerticalDominance`), (d)
un-gate the EngineTests assertions with bounded waits, (e) de-hardcode the
300-reel manifest test, (f) select the simulator by UDID with `bootstatus`,
`timeout-minutes`, and result bundles on both test steps (P2-22).

**Why at personal scale:** This is the whole QA department. A suite that
actually gates behavior means every future fix (including items 3–5 below)
lands verified instead of "probably fine". No new infrastructure — same
workflow, same two test bundles.

## 2. Bookmark round-trip protection: sheet → player → unsave (M)

**Problem:** The most stateful surface in the app — bookmark toggle,
Bookmarks sheet, BookmarkPlayerOverlay, unsave — has zero test coverage
(A19); the accessibility identifiers and the `-ui-testing-seed-mindful`
hook already exist but no test uses them. Bookmarking is also where the
worst data-consistency bug lives (A4).

**Proposal:** Three XCUITests: `testBookmarkPersistsIntoBookmarksSheet`
(save → dismiss owner-key alert → chip → assert `BookmarkGridItem_0`),
`testBookmarkPlayerUnsaveClosesWhenLastBookmarkRemoved`, and a
tap-to-pause/resume test asserting an exposed `isPlaying` accessibility
value on `FeedCollectionView`. Ship alongside the A4 fix.

**Why at personal scale:** Bookmarks are the personal archive — the one
thing whose loss is unrecoverable from the weekly pipeline. One green
round-trip test is cheap insurance; the alternative is discovering silent
bookmark breakage weeks later.

## 3. Empty-digest resilience: never a black screen (S)

**Problem:** A zero-item manifest renders a permanent black screen with no
controls, and the empty fetch overwrites the on-disk cache so even offline
launch stays black (A12).

**Proposal:** The `else` branch with tray icon + "No reels in this week's
digest yet" + Retry button (diff in REVIEW A12), plus guard
`DigestDataService` so an empty manifest never replaces the cached/bundled
one.

**Why at personal scale:** One bad publish shouldn't turn the phone app
into a brick with no recovery path except waiting for next Friday. Two
small edits, one UI test.

## 4. Trustworthy Download All: partial-failure honesty + real resume (S)

**Problem:** The sheet claims "Completed" at 100% while some reels
permanently failed (A9), resume data is deleted at download start so a
crash restarts every in-flight reel from 0 bytes (A11), and a stale
`suspensionSource` can silently freeze the queue (A2). Net effect: the
offline library can be quietly incomplete exactly when it's needed (no
signal, travel, poor reception).

**Proposal:** Bundle the three fixes: partial-failure `.failed("N of M
downloads failed — tap Retry.")` state (the Retry UI already exists), keep
`.dat` resume data until atomic promotion, and reset `suspensionSource` in
`cancelAll()`/`startDownloadAll()`. Optionally expose the failed count on
the Offline icon badge.

**Why at personal scale:** "Did my offline library actually download?" is
the one question the app must answer honestly. All three fixes are small
and testable via the existing unit-test target (state machine) + one
XCUITest.

## 5. "Download unwatched only" for Download All (S)

**Problem:** Download All fetches all ~250 reels even though the 1.5 GB
bookmark cap and phone storage are the binding constraints — and much of
the batch may already be watched (watch state is already tracked per reel).

**Proposal:** A single toggle on the Download All sheet (default: on) that
filters the batch to reels not yet recorded in watch progress
(`WatchedRules` already computes this). Progress totals respect the
filtered count.

**Why at personal scale:** Cuts storage and bandwidth roughly in half on
typical weeks with one boolean — no new subsystem, reuses the existing
watch-state data. Verifiable with a unit test on the filter plus the
existing sheet XCUITest identifiers.

## 6. Silent-week sentinel on the pipeline (S)

**Problem:** A weekly run can abort without a loud notification — the
follow-cooldown exit sends no alert at all (P1-7) and
`run_weekly.sh` deliberately stays quiet for exit 2 on that assumption.
The failure-alerts test only guards `_run_full_sync`'s source, so new
silent-exit sites escape it.

**Proposal:** Fix P1-7 (add `_alert_sync_abort` at the cooldown return)
and extend `tests/test_failure_alerts.py` with an AST-level invariant:
every `return 2` reachable in the weekly path must be preceded (in its
branch) by `_alert_sync_abort`. Keep the existing weekly self-audit email
as the second net.

**Why at personal scale:** The worst ops failure mode is waking up to a
week that silently didn't ship. One test turns that entire class of bug
into a red CI instead of a silent skip.

## 7. In-app playback health signal (M)

**Problem:** When R2 links rot or the worker hiccups, the app degrades
silently — reels stall, fall back to remote, or black-frame (see A10's
stall ladder), and the user just experiences "the app is flaky tonight"
with no way to tell whether it's the phone, the network, or the pipeline.

**Proposal:** Count per-reel stall/fallback/decode-failure events in
`AVPlayerPool` (the failure ladder already detects each of these), roll
them into a small `PlaybackHealth` counter, and surface one line in the
Offline/Bookmarks sheet: "3 reels degraded this week" with the reel ranks
listed. Expose the count as an accessibility value so an XCUITest can
assert it.

**Why at personal scale:** Turns vague flakiness into a concrete,
reportable signal ("reel #042's file is broken") that can be fed back into
the pipeline's `check_media_urls` script — diagnosis without a laptop.
Small counters and one label; no logging/metrics infrastructure.

## 8. Sleep timer for night viewing (S)

**Problem:** The app is a bedtime briefing by design (mindful limits,
watch timers already exist), but there's no way to say "stop when I fall
asleep" — playback continues, watch-time inflates, and the audio-interruption
fix (A8) shows audio lifecycle is where the rough edges live.

**Proposal:** A "Stop at end of reel / 15 / 30 min" option in the Mindful
modal: pauses the pool at the chosen boundary (end-of-item handler and a
single timer — both already exist for the watch timer) and dims the HUD.
Reuse the `DailyProgress.snoozeUntil` patterns and existing modal
identifiers.

**Why at personal scale:** Direct quality-of-life for the app's core use
moment, ~one view + one timer, and testable via the existing
`-ui-testing-seed-mindful` hook that currently no test exercises.

---

### Deliberately not proposed

- Multi-device sync, accounts, or a backend for bookmarks (the Cloudflare
  owner-key channel already covers off-device backup).
- A dashboard redesign or mobile-friendly dashboard (the phone has a real
  app; the dashboard is a laptop surface).
- Any new scheduled job, daemon, or monitoring stack — items 6 and 7 make
  the existing email + in-app surfaces smarter instead of adding watchers.
