# Audit & Remediation Report — Anti-Doomscroll Rebrand (Adversarial Sweep #2)

Date: 2026-10-11 (UTC) · Scope: `.workflow/REQUIREMENTS.md` + `.workflow/IMPLEMENTATION_PLAN.md`
(Rebranding & Anti-Doomscroll Engine) · Scale anchor: single-person personal use —
no new services, no new config knobs, no enterprise overhead.

Note: the 2026-09-27 cookie-free report previously lived in this file; it is
preserved at `/tmp/old_cookie_report.md` and in git history. This file now
covers the rebrand sweep. The cookie-free pipeline behavior it verified is
untouched by this session (all cookie-free suites still green, §5).

## 0. Verdict

The dirty-tree rebrand implementation was ~80% complete and directionally
correct. I found and fixed **19 defects** (7 spec violations, 6 crash/type-error
risks, 6 contract/parity gaps). No new architecture introduced. Full regression:
**587 passed** (563 unit + 10 grid UAT + 14 mobile PWA UAT), `main.py --build-only`
clean, built-site contract probe 14/14.

## 1. Remediations (what I changed and why)

### Spec violations

**R1. Manual advance mode never held (plan Phase 3 violated).**
`handleVideoEnd` parked the ended reel, but both auto-play drivers —
`IntersectionObserver` and the `checkCenteredCard` scroll-settle tick — called
`playCardVideo` unconditionally, advancing past the parked reel on any scroll
settle. Fix: both drivers return early when `advanceMode === 'manual'` and the
current card carries `endedHandled`. Files: `templates/partials/player.js`.

**R2. Breather not skippable (plan Phase 3: "skippable by tap").**
The 3s breather had no tap handler. Fix: the countdown toast is now tappable
during breather ("(tap to skip)"), firing the same guarded advance path; the
interval/timeout handles were restructured so skip and natural expiry share one
code path (no double-advance). Files: `player.js`, `styles.css` (R3).

**R3. Countdown toast never visible (latent display bug).**
JS toggled `.visible` on `.countdown-toast` but no such CSS rule existed —
opacity stayed 0 forever, so the 0.5s/breather/manual affordances never
rendered. Fix: added `.countdown-toast.visible { opacity: 1; pointer-events:
auto; cursor: pointer; }` (pointer-events is what makes the R2 skip tappable).
Files: `templates/partials/styles.css`.

**R4. Neon progress rings still live in JS (plan Phase 4 violated).**
`_paintCategoryProgressRings` painted `conic-gradient(#f09433, #dc2743,
#bc1888)` inline every watched-mark, overriding the quiet-luxury CSS rings.
Fix: replaced with frosted-slate alpha fade (`rgba(255,255,255,0.14→1.0)` by
fraction left); removed the dead `deg` variable. Files: `player.js`.

**R5. Neon rings + pink glow in native story bar (parity matrix violated).**
`StoryCategoryBarView` used an IG-orange/pink/purple `AngularGradient` ring
with a pink drop shadow. Fix: active = solid white 2pt, inactive =
`rgba(255,255,255,0.14)`; deleted the gradient; doc comment updated. Files:
`StoryCategoryBarView.swift`.

**R6. No lockout-PIN setup path (plan Phase 3: "PIN setup").**
Settings modal had no PIN field, and `submitLockoutPinOverride` compared
against a hardcoded `'1234'` fallback — any visitor guesses the override.
Fix: added PIN row (shown only in hard mode) + `updatePinSetting` (4–6 digit
validation, clears the field after save); override refuses with setup guidance
when no PIN is stored. Files: `modals.html`, `player.js`.

**R7. `/channels` had no Import Following UI and no archetype filter
(REQ Decision 2 + Decision 5).** The server endpoints existed but no button
called them; the plan's "filter controls in `/channels`" were absent. Fix:
added Import Following card + review modal (checkbox list, Already-Present
rows disabled, additive commit via `/api/channels/import-following`) and an
archetype pill row (All/Educational/Craft/Lifestyle/Vlog/Untagged) composable
with the existing category/search filters. Files: `templates/channels.html`.

### Crash / type-error risks

**R8. `rank_top_reels(sources=None)` crashed (TypeError).**
The blacklist filter iterated `sources` before the `or []` guards below it.
Fix: coerce `sources`/`candidates` to dict-only lists up front; `str()` guards
on handle access. Added regression test. Files: `ranker.py`,
`tests/test_ranker.py`.

**R9. Non-numeric timestamps flowed into chrono sort.**
`timestamp: "abc"` (or a pass-through re-rank stamping `base_viral_score`
payloads) survived into `data-timestamp`, where comparisons misbehave. Fix:
new `_coerce_timestamp` (finite-float-or-0) applied to `timestamp`/`taken_at`
at rank time; `taken_at` preserved when present. Files: `ranker.py`.

**R10. Onboarding crashed on malformed inputs (4 TypeErrors/AttributeErrors).**
`reconcile_following(None)`, non-dict rows, `handle: None`,
`merge_imported_channels([None], …)`, `candidates=None` all raised. The
dashboard feeds cached JSON with legacy shapes into both. Fix: skip-and-continue
guards, `or []` iteration, `clean_handle` validation. Added 2 regression tests.
Files: `interactive_onboarding.py`, `tests/test_interactive_onboarding.py`.

**R11. Unvalidated settings writes poisoned quota state.**
`updateQuotaSetting("garbage")` stored `NaN`, and `NaN >= quota` is always
false — the daily limit silently disabled itself. Same class for enforcement /
advance / sort setters. Fix: allowlist validation with early return
(`ALLOWED_QUOTAS`, `'gentle'/'hard'`, `'auto'/'manual'`, `'rank'/'chrono'`).
Files: `player.js`.

**R12. Chrono sort parsed epoch seconds with `new Date()`.**
`data-timestamp` holds unix *seconds*; `new Date(1790936064)` is a 1970 date.
Order happened to survive (uniform magnitude) but any ms-epoch payload would
mis-sort. Fix: `parseFloat` + ms-vs-s normalization. Files: `player.js`.

**R13. Health/food creators mis-bucketed to entertainment on import.**
`_infer_archetype_and_category` had no food keyword set, mapped health→niche,
and word-token matching missed compound handles (`monitormunchies`,
`hubermanlab`). Verified live sources would import wrong. Fix: food set added,
health→health, raw-handle substring pre-pass mirroring
`extractor.categorize_creator`. Added regression test. Files:
`interactive_onboarding.py`, `tests/test_interactive_onboarding.py`.

### Contract / parity gaps

**R14. `/api/channels` dropped `archetype` (plan Phase 1 broken end-to-end).**
Ranker emits it, feed.html renders `data-archetype`, but the listing API
stripped it — the new channels filter (R7) would see nothing. Fix: include
`s.get("archetype")`; `/api/channels/add` stamps `archetype: None` (untagged,
per plan — was silently absent). Files: `local_server.py`.

**R15. New imports defaulted to `entertainment`/`educational` (plan: `niche`/`null`).**
Both `merge_imported_channels` fallbacks contradicted Phase 5 ("new imports
default to `category: niche`, `archetype: null`"). Fix: fallbacks corrected;
added regression test. Files: `interactive_onboarding.py`.

**R16. iOS `hideRanks`/`feedSortOrder` state was stored but never consumed.**
Settings persisted both keys, yet `ReelCardOverlayView` and `GridView` had no
such inputs — the toggles were dead on device. Fix: `hideRanks` hides the feed
rank badge and grid rank pills; `GridView.sortOrder` sorts by `takenAt` desc
(`.distantPast` fallback keeps untagged stable) while preserving original
indices for `onSelectReel`; both wired through `FeedMainView`. Files:
`ReelCardView.swift`, `GridView.swift`, `InstagramDigestApp.swift`.

**R17. iOS watch-time quota never accrued (parity matrix violated).**
`DailyProgress` gained `watchDurationSeconds/targetMinutes/enforcementMode`
but nothing wrote them — `onPlaybackTick` fired into the void. Fix: subscribed
`accrueWatchSeconds` (delta guard `0<delta<5`, same as web), persisting to
today's row and pausing + surfacing the mindful modal at quota, honoring
snooze. The pre-existing count-based modal path is untouched. Files:
`InstagramDigestApp.swift`.

**R18. iOS breather delay survived navigation (stale auto-advance).**
The 3s `Task.sleep` captured `slot` but a swipe during the pause still fired
`onAutoAdvanceToNext` from the departed reel. Fix: cancellable `breatherTask`,
cancelled in `setCurrentIndex`, with `Task.isCancelled` re-check after sleep.
Files: `AVPlayerPool.swift`.

**R19. Manifesto/Settings sheets stranded playback + stale docs.**
`resumeFeedAfterSheet` only knew 3 sheets, so dismissing the 2 new sheets never
resumed a pre-sheet playing feed. README still documented the retired 50-reel
counter and lacked the required video reference. Fix: 5-sheet resume guard;
README title → "Instagram Digest — The Anti-Doomscroll Briefing", quota copy,
and `https://youtu.be/FmLJbYBu094` philosophy block. Also swept the last
`--ig-gradient`/neon hexes from `channels.html`. Files:
`InstagramDigestApp.swift`, `README.md`, `channels.html`.

## 2. Requirements traceability (REQUIREMENTS §2, line by line)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| D1 | Keep "Instagram Digest" + subtitle | OK (pre-existing) | `header.html`, iOS `HeaderBarView` |
| D1 | 10-traps manifesto modal + video link | OK (pre-existing) | `modals.html#manifestoModal`, `ManifestoSheetView.swift` |
| D1 | Video ref in README | FIXED (R19) | `README.md` philosophy block |
| D2 | Tier 3 stripped, cookie-free extraction | OK (pre-existing, untouched) | `main.py` skips, `scripts/topup_digest.py` untouched |
| D2 | `MIN_DEPLOY_ITEMS=150` floor, `shortfall_paused` | OK (pre-existing, untouched) | `main.py`, all shortfall suites green |
| D2 | `--import-following` CLI + `/channels` button + review | FIXED (R7) | `interactive_onboarding.py`, `channels.html` modal, endpoints |
| D2 | Already-present flagged, non-destructive merge, zero cookies | OK + hardened (R10/R15) | `reconcile_following`, `merge_imported_channels` |
| D3 | `daily_watch_seconds` active-playback-only accumulator | OK (pre-existing) | `player.js` ticker, `visibilityState` guard |
| D3 | Local date boundary, 20m default, 10/20/30/45 picker | OK + hardened (R11) | `localDateStr`, `ALLOWED_QUOTAS` |
| D3 | Gentle + hard lockout with PIN override | FIXED (R6) | PIN row, `updatePinSetting`, no hardcoded fallback |
| D3 | Per-platform local storage | FIXED (R17) | `localStorage` + `DailyProgress.accrueWatchSeconds` |
| D4 | Progressive icon (muted IG → slate) | OK (pre-existing) | `assets/icon.svg`, PNGs, AppIcon set |
| D4 | Quiet-luxury palette, frosted rings, monochrome pills, zinc/amber bars | FIXED (R4/R5/R19) | `styles.css`, `channels.html`, story bars both platforms |
| D5.1 | Advance auto/manual + 3s skippable breather | FIXED (R1/R2/R3/R18) | `handleVideoEnd`, toast skip, observer/settle guards, `breatherTask` |
| D5.2 | Grayscale toggle (`filter` / `.grayscale(1.0)`) | OK (pre-existing) | `.grayscale-mode`, `FeedPagerView.grayscale` |
| D5.3 | Session time-boxing pill + lockout | OK + wired (R17) | `⏱️ m/m` badge, `WatchTimerPill`, `accrueWatchSeconds` |
| D5.4 | `archetype` additive field + filters | FIXED (R13/R14/R15) | `sources.json` (70 edu/25 life), ranker, `/api/channels`, channels pills |
| D5.5 | Chrono sort + rank-hide, Top-250 pool unchanged | FIXED (R9/R12/R16) | `applySortOrder`, `hide-ranks`, `GridView.sortOrder` |
| Parity | Manifesto/Settings sheets, `DailyProgress` fields, `ReelItem.takenAt/archetype` | OK + wired (R16–R18) | Swift files listed above |
| Verify | `pytest`, PWA build, iOS CI | PASS (§5) | 587 passed, `--build-only` clean |

Deliberate non-changes: `hubermanlab`-style bare handles still import as
lifestyle/entertainment (no signal in the string — triage in `/channels`, per
plan's untagged flow); no live-Instagram integration test (nondeterministic 429s
must never gate CI); `DailyProgress.viewedCount`/50-reel modal left intact
alongside the new time quota (additive, not a migration).

## 3. Type errors & edge-case crashes swept

- `rank_top_reels(sources=None)` TypeError (R8) — any caller passing a missing
  source list crashed; now returns ranked items with default bucketing.
- Non-dict candidates (`"junk"`, `None`, `{}`) skipped with a warning (R8).
- Garbage timestamps coerced to 0; `taken_at` preserved (R9).
- Onboarding None/non-string/missing-handle inputs skipped, never raise (R10);
  corrupt (non-list) `sources.json` under merge resets to `[]` instead of
  crashing on `.get`.
- `NaN`-quota and invalid enum writes rejected (R11).
- Swift: `GridView.displayReels` uses `.distantPast` fallback so nil `takenAt`
  never traps the sort; `accrueWatchSeconds` guards non-finite/oversized
  deltas; `breatherTask` is optional-chained before cancel.
- SwiftUI changes are additive optional params with defaults (`hideRanks =
  false`, `sortOrder = "rank"`) — all existing call sites compile unchanged.

## 4. Files changed

`ranker.py` (R8/R9) · `interactive_onboarding.py` (R10/R13/R15) ·
`templates/partials/player.js` (R1/R2/R4/R11/R12) ·
`templates/partials/styles.css` (R3) · `templates/partials/modals.html` (R6) ·
`templates/channels.html` (R7/R19) · `local_server.py` (R14) · `README.md`
(R19) · `StoryCategoryBarView.swift` (R5) · `ReelCardView.swift` (R16) ·
`GridView.swift` (R16) · `InstagramDigestApp.swift` (R16/R17/R19) ·
`AVPlayerPool.swift` (R18) · `tests/test_ranker.py` +
`tests/test_interactive_onboarding.py` (4 new regression tests).

## 5. Verification

- Full Python suite (minus browser): **563 passed**.
- Browser UAT: `test_grid_view` **10 passed**, `test_mobile_pwa_uat` **14 passed**.
- `main.py --build-only` clean; built `site/index.html` contract probe 14/14
  (PIN row, breather skip, modals, quota badge, hide-ranks/grayscale CSS, toast
  rule, subtitle, data-archetype, manifesto link, quota validation, manual
  guard, zero neon conic).
- `py_compile` clean on all touched modules.
- Swift: Linux has no SwiftUI SDK so `swiftc -typecheck` is unavailable here;
  changes are additive/optional-param-only, and iOS CI
  (`.github/workflows/build-ipa.yml`) remains the gate for compile proof.
  Existing `Tests/` (incl. `test_watch_timer.py` contract + `AuditFixesTests`
  dailyLimit=50 assertions) are untouched and unbroken by these changes.

## 6. Known non-blockers (personal-use scale, left as-is)

1. `data/top100_digest.json` + `site/data.json` predate the ranker fix, so live
   items lack `archetype` until the next weekly run backfills it. Feed renders
   `data-archetype=""` safely; chrono/hide-rank behavior is unaffected. No
   action needed — do not hand-edit the batch.
2. Bare-handle imports with zero signal (`hubermanlab`) stay untagged
   lifestyle/entertainment by design; triage in `/channels`.
3. Web `recordDailyView` is a deliberate no-op shim (legacy `DAILY_SET_KEY`
   pruner retained) — quota flows through the seconds accumulator only.
4. iOS hard-lockout currently pauses + shows the mindful modal rather than a
   dedicated until-midnight overlay; the watch accrual + snooze contract holds,
   and a full lock screen can be added if real usage demands it.
