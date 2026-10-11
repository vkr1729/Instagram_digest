# UAT Plan — Anti-Doomscroll Rebrand & Engine (Independent)

Date: 2026-10-11 (UTC) · Scope: `.workflow/REQUIREMENTS.md` §2 (D1–D5 + parity matrix) + `.workflow/AUDIT_AND_REMEDIATION_REPORT.md` R1–R19
Scale anchor: single-person personal use — functional app over stringent gates. No new services, no live-Instagram-gated CI.
Supersedes: 2026-09-27 cookie-free UAT plan (preserved in git history). Cookie-free pipeline behavior is untouched; this plan re-verifies it only as regression (§L).

## 1. Independence principles

- Black-box first: verify via observable surfaces — rendered DOM/CSS, `localStorage`, built `site/`, CLI exit codes + stdout, JSON outputs, `/channels` + `/api/channels` HTTP behavior, iOS Simulator UI. Do not assert on internal function names except where the contract names them (`extract_external_reels_from_feed`, `MIN_DEPLOY_ITEMS`, `merge_imported_channels`, `reconcile_following`).
- Isolation: every pipeline/onboarding case runs against a temp `DATA_DIR` (see `tests/conftest.py` pattern) or `--dry-run --limit-per-creator 1 --days-back 1`. Never run bare `python main.py` or `python main.py --sync` (full production sync). Never touch live `data/`. Tripwire: no new `data/sync_progress_*`, `data/expand_checkpoint_*`, `data/digests/*.json`, `cookies.json`/`cookies.txt` writes after the run.
- Mock the network, not the contract: Playwright / yt-dlp / SMTP stubbed or `--dry-run` / `--build-only` where noted. No live-Instagram assertion gates pass/fail (nondeterministic 429s). iOS Swift compile proof is delegated to GitHub Actions `build-ipa.yml` (no SwiftUI SDK on Linux).
- Secrets: never inspect `.env`; use `.env.example` + `config.py` for schema.
- Regression symmetry: each fix from audit R1–R19 has a re-verification case below; any red automated suite is P0 regardless of matrix result.
- Single-person rule (taste): only crashes or drastically terrible usage are major bugs. A P1 failing >~3 verification rounds without crash/data-loss risk gets relaxed or converted to a test seam — stop line-tuning.

## 2. Entry criteria / environment

- `requirements.txt` installed; `pytest` collects cleanly.
- Baseline: `git status` + `git log --oneline -5` recorded; live `data/` snapshotted.
- Env scrubbed for automated runs: `VIEWING_PIN=""`, `SMTP_*=""`, `NOTIFICATION_EMAIL=""` (conftest pattern).
- Entry probes (must pass before matrix):
  - `python main.py --build-only` clean.
  - `grep -rn "conic-gradient(#f09433" templates/ || echo "neon-free OK"`.
  - `python -m pytest tests/test_ranker.py tests/test_interactive_onboarding.py -q` green (R8/R9/R10/R13/R15 guards).

## 3. Exit criteria

- All P0 cases pass. P1 may have at most documented non-blocker waivers (§8).
- No live-state tripwire firing; `py_compile` clean on touched Python modules.
- Any failure reproduced on a clean checkout before filing as defect (dirty-tree guard).
- iOS CI (`build-ipa.yml`) green for Swift parity cases (UAT-PA-*, UAT-VI-06/07, UAT-QT-07b/08, UAT-AD-06, UAT-CH-04, UAT-GR-02).

## 4. Test matrix

Conventions: `P0` = blocker, `P1` = fix soon, `P2` = waiver-candidate. Web = `local_server.py` + built `site/`; iOS = Simulator / CI.

### A. Brand, manifesto, docs (REQ D1; audit R19)

| ID | Requirement | Procedure | Expected (observable) | P |
|---|---|---|---|---|
| UAT-BR-01 | Keep "Instagram Digest" + subtitle (Web) | Build (`--build-only`), open `site/index.html`, inspect header | Brand "Instagram Digest" + subtitle "The Anti-Doomscroll Briefing" visible; no old tagline | P0 |
| UAT-BR-02 | Subtitle parity (iOS) | Simulator: launch, inspect header bar | Same brand + subtitle; `HeaderBarView` renders both | P1 |
| UAT-BR-03 | 10-traps manifesto modal (Web) | Click info `ℹ️` → manifesto modal | 10 traps listed, each with how Digest neutralizes it; styled quiet-luxury, opens/closes cleanly, playback resumes after dismiss (see PA-03) | P0 |
| UAT-BR-04 | Video reference in modal (both) | In modal / `ManifestoSheetView` | Link `https://youtu.be/FmLJbYBu094` present and tappable in Web modal and iOS sheet | P0 |
| UAT-BR-05 | README contract | Inspect `README.md` | Title "Instagram Digest — The Anti-Doomscroll Briefing", quota copy (time-based, not 50-reel), philosophy block with video link | P1 |
| UAT-BR-06 | Progressive icon (Web) | Inspect `assets/icon.svg`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`, PWA manifest | Diagonal gradient muted IG (`#d97736`/`#be436b` bottom-left) → slate/graphite (`#27272a`/`#18181b` top-right); PNGs exist; `sw.js` cache bumped | P1 |
| UAT-BR-07 | Progressive icon (iOS) | Inspect `Resources/Assets.xcassets/AppIcon.appiconset` / CI artifact | Same progressive artwork; app installs with new icon (CI-gated) | P2 |

### B. Pipeline hardening — Tier 3 strip + floor (REQ D2a; plan Phase 2)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-PL-01 | Scoped dry run: `main.py --dry-run --limit-per-creator 1 --days-back 1`; grep `extract_external_reels_from_feed` call sites in `main.py` + `scripts/topup_digest.py` | Tier 3 skipped at all 3 sites with descriptive log; function never invoked; no session-challenge/bot-detection path | P0 |
| UAT-PL-02 | Same dry run with decoy `cookies.json`/`cookies.txt` present in temp DATA_DIR | Neither file read/injected; extraction strictly cookie-free | P0 |
| UAT-PL-03 | Force thin week (banked candidates < floor) in temp DATA_DIR | Stage `shortfall_paused`, exit 2, zero R2 mutation, previous weekly digest untouched | P0 |
| UAT-PL-04 | Assert `MIN_DEPLOY_ITEMS == int(TOP_DIGEST_COUNT*0.6)` (=150 at defaults); grep for hardcoded `150` bypass | Shared constant used; no hardcoded floor fork | P1 |
| UAT-PL-05 | `--expand` in cookie-free mode | Refuses with "external feed expand is deprecated; use `--limit-per-creator`" (exit 2) | P0 |
| UAT-PL-06 | Confirm no second depth knob | `--limit-per-creator` is the only depth control; no `ANON_REELS_PER_CREATOR_DEPTH` env | P2 |

### C. Interactive additive onboarding (REQ D2b; audit R7/R10/R13/R15; plan Phase 5)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-OB-01 | `python main.py --import-following` (headed Playwright, manual login/2FA) against temp `sources.json` | Launches temp-profile browser; user completes login manually; following list scraped from DOM; lands on selection review | P0 |
| UAT-OB-02 | Review screen with mix of existing + new handles | Existing channels flagged "Already Present" with category/favorite preserved; new rows have selectable checkboxes | P0 |
| UAT-03 | Confirm selection | Non-destructive merge into `sources.json`; unpicked rows skipped; existing entries untouched. New imports default `category: "niche"`, `archetype: null` (audit-corrected contract — REQ text saying `educational` is superseded) | P0 |
| UAT-OB-04 | Post-run forensics on temp dir | Temp browser profile wiped; zero session cookies persisted (`cookies.json`/`cookies.txt` absent/unmodified) | P0 |
| UAT-OB-05 | `/channels` dashboard on local server (`is_local`) | "Import Following" button visible locally (hidden remotely); opens modal diff table (present rows disabled, new checkboxes); commit via `/api/channels/import-following` is additive | P0 |
| UAT-OB-06 | Malformed inputs: `reconcile_following(None)`, non-dict rows, `handle: None`, `merge_imported_channels([None])`, `candidates=None` | Skip-and-continue, never raise; corrupt non-list `sources.json` resets to `[]` under merge | P0 |
| UAT-OB-07 | Inference spot-check: food handles, health→health (not niche), compound handles (`monitormunchies`, `hubermanlab`-style) | Food set hits; health→health; substring pre-pass before token match. Bare zero-signal handles stay untagged lifestyle/entertainment by design → triage in `/channels` | P1 |
| UAT-OB-08 | CSV / handle-paste fallback input | Same review + merge path works without browser scrape | P1 |

### D. Time quota + lockout (REQ D3; audit R6/R11/R17; plan Phase 3.1)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-QT-01 | Play video, then pause / let buffer / switch tab hidden; watch `daily_watch_seconds` in `localStorage` | Accrues only while `!paused && !ended && visibilityState==='visible'`. Pause, buffering, background tabs, and breather time add nothing | P0 |
| UAT-QT-02 | Spoof date key to yesterday, reload | New local `YYYY-MM-DD` bucket starts at 0; old date keys pruned (regex) | P0 |
| UAT-QT-03 | Settings quota picker | Options 10/20/30/45m, default 20m; persists per-platform locally | P0 |
| UAT-QT-04 | Hit quota in Gentle (default) | Peaceful check-in modal with elapsed time + "Take a Break" / "Continue"; playback pauses; Continue resumes | P0 |
| UAT-QT-05 | Switch to Hard Lockout, set 4-digit PIN, hit quota; then try override with wrong PIN, no-PIN state, correct PIN | Locks until next calendar day; PIN row visible only in hard mode; 4–6 digit validation, field cleared after save; override refuses with setup guidance when no PIN stored; no `'1234'` fallback; correct PIN unlocks (emergency use) | P0 |
| UAT-QT-06 | `updateQuotaSetting("garbage")` + invalid enforcement/advance/sort writes via console | Rejected (allowlist `ALLOWED_QUOTAS`, `gentle`/`hard`, `auto`/`manual`, `rank`/`chrono`); stored value unchanged; quota never silently disabled by `NaN` | P0 |
| UAT-QT-07a | Web storage | `localStorage` keys per date; no server round-trip | P1 |
| UAT-QT-07b | iOS storage | `DailyProgress.watchDurationSeconds/targetMinutes/enforcementMode` persist to today's row; `accrueWatchSeconds` guards `0<delta<5`, ignores non-finite/oversized | P0 |
| UAT-QT-08 | iOS quota hit | Playback pauses + mindful modal surfaces, honoring snooze; count-based legacy modal path untouched | P0 |

### E. Quiet-luxury visual sweep (REQ D4; audit R4/R5/R19)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-VI-01 | Watch ≥1 reel (marks category progress), inspect rings in DevTools | Rings are frosted slate (active solid `#ffffff`, inactive `rgba(255,255,255,0.14)`); no inline `conic-gradient(#f09433,#dc2743,#bc1888)` from JS | P0 |
| UAT-VI-02 | Inspect ring/button shadows | Zero glowing pink drop shadows anywhere | P1 |
| UAT-VI-03 | Buttons + badges | Monochrome white/black pills or frosted glass; no neon gradient buttons | P1 |
| UAT-VI-04 | Progress bars | Calm neutral zinc/amber fill, not neon pink | P1 |
| UAT-VI-05 | `grep -rn "ig-gradient\|#f09433\|#dc2743\|#bc1888" templates/ assets/` | Zero hits (channels.html swept) | P0 |
| UAT-VI-06 | iOS story bar | Active solid white 2pt, inactive `rgba(255,255,255,0.14)`; no `AngularGradient` orange/pink/purple, no pink shadow | P1 |

### F. Advance mode + breather (REQ D5.1; audit R1/R2/R3/R18)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-AD-01 | Default `auto`: let reel end | 0.5s countdown toast appears (`.countdown-toast.visible` renders, opacity 1) then advances | P0 |
| UAT-AD-02 | Settings → `manual`: let reel end, then scroll-settle without swiping | Parks on final frame with "Swipe for next ↓"; `IntersectionObserver` + `checkCenteredCard` scroll tick do NOT advance past parked reel; swipe advances once | P0 |
| UAT-AD-03 | Enable breather: let reel end; tap toast mid-countdown on one reel, let it expire on next | 3s skippable countdown "(tap to skip)" visible both times; tap skips immediately; expiry advances once; no double-advance in either path | P0 |
| UAT-AD-04 | Breather + quota accumulator | Breather seconds excluded from `daily_watch_seconds` | P1 |
| UAT-AD-05 | Reload | `advance_mode` + `breather_enabled` (default `auto`/`false`) persist; invalid writes rejected | P1 |
| UAT-AD-06 | iOS: enable breather, swipe mid-pause | Departed reel's pending `Task.sleep` cancelled (`breatherTask` + `Task.isCancelled` re-check); no stale auto-advance after navigation | P0 |

### G. Grayscale dopamine-detox (REQ D5.2)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-GR-01 | Web player grayscale toggle on/off | `filter: grayscale(100%)` on feed container (hardware-accelerated), persists; toggle off restores color | P1 |
| UAT-GR-02 | iOS grayscale toggle | `.grayscale(1.0)` modifier applied; persists | P1 |

### H. Session pill (REQ D5.3; plan Phase 3.1 header)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-SE-01 | Header during playback (Web) | Pill shows `⏱️ m/m` tracking accumulator vs target; replaces legacy `🎯 0/50`; no 50-copy strings in player/settings UI | P0 |
| UAT-SE-02 | iOS feed header | `WatchTimerPill` equivalent, same values | P1 |
| UAT-SE-03 | Legacy compat note | `DailyProgress.viewedCount`/50-reel modal may remain as additive legacy; quota flows through seconds accumulator only; `recordDailyView` no-op shim retained — not a defect | P2 |

### I. Archetype plumbing + filters (REQ D5.4; audit R13/R14/R15; plan Phase 1)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-AR-01 | Inspect `sources.json` + category chips | Additive optional `archetype` (`educational`/`craft`/`lifestyle`/`vlog`/`null`); `null` = untagged shown in All; existing category chips unbroken | P0 |
| UAT-AR-02 | `--build-only`, inspect `.reel-card` nodes + `site/data.json` | Ranker→`site_builder` preserves `archetype` + `timestamp`/`taken_at`; feed emits `data-archetype` + `data-timestamp`; grid cards carry both | P0 |
| UAT-AR-03 | `GET /api/channels`; `POST /api/channels/add` | Listing includes `archetype`; add stamps `archetype: None` (untagged) | P0 |
| UAT-AR-04 | `/channels` filter row | Pills All/Educational/Craft/Lifestyle/Vlog/Untagged, composable with category/search filters | P0 |
| UAT-AR-05 | Viewer archetype filter | Feed filters by archetype without breaking chrono/hide-rank toggles | P1 |
| UAT-AR-06 | `rank_top_reels(sources=None)`, candidates `"junk"`/`None`/`{}`, `timestamp: "abc"` | No TypeError; dict-only coercion with warning; garbage timestamps coerce to 0; `taken_at` preserved | P0 |

### J. Chrono + hide-ranks neutrality (REQ D5.5; audit R9/R12/R16)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-CH-01 | Ranker output vs viewer default | Pool is finite Top 250 from ranker; viewer default order = rank; chrono is client-side reorder only | P0 |
| UAT-CH-02 | Chrono toggle with mixed `data-timestamp` (seconds, ms, garbage) | Sorts `taken_at` desc; s-vs-ms normalized (`new Date` on raw seconds is fixed); non-numeric → 0 stable, never mis-sorts | P0 |
| UAT-CH-03 | Hide-ranks toggle | Rank pills (`#01`, `#02`) hidden in feed and grid (`.hide-ranks` covers both) | P0 |
| UAT-CH-04 | iOS `hideRanks` + `sortOrder` | Feed badge + grid pills hidden; grid sorts `takenAt` desc (nil → `.distantPast` stable); original indices preserved for `onSelectReel` | P0 |
| UAT-CH-05 | Reload | Both toggles persist (`rank`/`chrono` validated) | P1 |

### K. Sheet / parity wiring (audit R16–R19; parity matrix)

| ID | Procedure | Expected | P |
|---|---|---|---|
| UAT-PA-01 | iOS Manifesto sheet | 10 traps + video link, parity with Web modal content | P1 |
| UAT-PA-02 | iOS Settings sheet | Hosts quota slider, mode, PIN, advance, breather, grayscale, hide-rank, chrono toggles | P0 |
| UAT-PA-03 | Open each of the 5 sheets during playback, dismiss | Pre-sheet playing feed resumes in all 5 cases (`resumeFeedAfterSheet` 5-sheet guard) | P0 |
| UAT-PA-04 | Model compat | `ReelItem.takenAt`/`archetype` `decodeIfPresent` — old archives without keys load; `DigestState` init call sites compile (CI-gated) | P1 |

### L. Automated regression gate (REQUIREMENTS §4)

| ID | Suite | Gate |
|---|---|---|
| UAT-RG-01 | Full Python suite minus browser (`pytest tests/ --ignore` browser) | Green (audit baseline 563 passed; any red = P0) |
| UAT-RG-02 | Browser UAT: `test_grid_view` (10) + `test_mobile_pwa_uat` (14) | 24/24 green |
| UAT-RG-03 | `main.py --build-only` + built-site contract probe (PIN row, breather skip affordance, modals, quota badge, hide-ranks/grayscale CSS, toast rule, subtitle, `data-archetype`, manifesto link, quota validation, manual guard, zero neon conic) | Clean build + 14/14 probe |
| UAT-RG-04 | iOS CI `build-ipa.yml` (XcodeGen + unit + XCUITest) | Green — sole compile proof for Swift changes |

## 5. Copy-paste manual probes

```bash
# Entry probes (§2)
.venv/bin/python main.py --build-only
grep -rn "conic-gradient(#f09433\|#dc2743\|#bc1888\|ig-gradient" templates/ assets/ || echo "neon-free OK"
.venv/bin/python -m pytest tests/test_ranker.py tests/test_interactive_onboarding.py -q

# Scoped pipeline probes (never bare --sync)
.venv/bin/python main.py --dry-run --limit-per-creator 1 --days-back 1
.venv/bin/python main.py --expand 3 --dry-run   # expect Tier-3 deprecation refusal path

# Built-site contract probe (audit §5 pattern, 14 checks)
python - <<'EOF'
import pathlib
html = pathlib.Path("site/index.html").read_text()
checks = ["manifestoModal","settingsModal","PIN","tap to skip","hide-ranks",
  "grayscale-mode","countdown-toast.visible","Anti-Doomscroll Briefing",
  "data-archetype","youtu.be/FmLJbYBu094","ALLOWED_QUOTAS","advanceMode",
  "daily_watch_seconds","conic-gradient(#f09433"]
for c in checks[:-1]: assert c in html, f"MISSING {c}"
assert checks[-1] not in html, "neon leak"
print("contract 14/14 OK")
EOF

# API parity probes (local server only)
# GET /api/channels → each row has archetype key; POST /api/channels/add → archetype None
# /channels page → Import Following button + archetype pill row present

# Full automated gate (UAT-RG-01/02)
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_mobile_pwa_uat.py --ignore=tests/test_grid_view.py
.venv/bin/python -m pytest tests/test_grid_view.py tests/test_mobile_pwa_uat.py -q

# Tripwire (must print nothing new)
ls data/sync_progress_*.json data/expand_checkpoint_*.json data/expand_progress_*.json 2>/dev/null || echo "no checkpoints OK"
git status --short data/ | grep -v "^ M .workflow" || echo "no live-data mutation OK"
```

Web interactive checks (QT/AD/GR/CH/SE): open local server → DevTools → Application → `localStorage` (`daily_watch_seconds`, `advance_mode`, `breather_enabled`, quota/enforcement keys); throttle network to force buffering; toggle `document.visibilityState` via tab switch; assert accumulator behavior per QT-01.

## 6. Defect triage

- P0 (blocker): Tier-3 invoked (PL-01), shortfall mutating R2 (PL-03), onboarding cookie persistence (OB-04) or destructive merge (OB-03), quota silently disabled (QT-06) or accruing while paused/hidden (QT-01), hardcoded PIN fallback (QT-05), manual mode auto-advancing (AD-02), untappable breather (AD-03), invisible toast (AD-01 CSS), neon conic in JS/CSS (VI-01/05), `archetype` dropped end-to-end (AR-02/03), chrono mis-sort (CH-02), sheets stranding playback (PA-03), any RG red.
- P1 (fix soon): subtitle/icon copy drift, shadow/pill/bar polish, inference misses on signal-bearing handles, persistence edge cases, iOS pill/modal copy.
- P2 (waiver-candidate): icon PNG rendering nuance, Friday-overnight prompt wording, doc polish, legacy 50-count compat notes.
- Single-person rule: a P1 failing >~3 rounds with no crash/data-loss gets relaxed or seamed, trusting post-usage feedback.

## 7. Traceability

| REQUIREMENTS | UAT cover | Audit fix re-verified |
|---|---|---|
| D1 brand + subtitle | BR-01/02 | — (pre-existing) |
| D1 manifesto + video link | BR-03/04, PA-01 | — (pre-existing) |
| D1 README video ref | BR-05 | R19 |
| D2 Tier 3 stripped, cookie-free | PL-01/02 | — (pre-existing, untouched) |
| D2 floor 150 + shortfall_paused | PL-03/04 | — (pre-existing, untouched) |
| D2 `--import-following` CLI + `/channels` review | OB-01/02/03/05 | R7 |
| D2 already-present, non-destructive, zero cookies | OB-02/03/04/06 | R10/R15 |
| D3 active-playback accumulator | QT-01 | — (pre-existing) |
| D3 date boundary, 20m default, 10/20/30/45 | QT-02/03 | R11 (hardened) |
| D3 gentle + hard + PIN | QT-04/05/06 | R6 |
| D3 per-platform local storage | QT-07a/07b/08 | R17 |
| D4 progressive icon | BR-06/07 | — (pre-existing) |
| D4 quiet luxury (rings/pills/bars) | VI-01–06 | R4/R5/R19 |
| D5.1 advance auto/manual + skippable breather | AD-01–06 | R1/R2/R3/R18 |
| D5.2 grayscale | GR-01/02 | — (pre-existing) |
| D5.3 session pill + lockout | SE-01/02, QT-04/05/08 | R17 |
| D5.4 archetype field + filters | AR-01–06, OB-07 | R13/R14/R15 |
| D5.5 chrono + hide-rank, Top-250 unchanged | CH-01–05 | R9/R12/R16 |
| Parity: sheets, DailyProgress, ReelItem | PA-01–04 | R16/R17/R18/R19 |
| Verify: pytest, PWA build, iOS CI | RG-01–04 | §5 (587 passed) |

## 8. Out of scope / known non-blockers (audit §6, accepted)

- Live-Instagram end-to-end (nondeterministic 429s never gate CI).
- `data/top100_digest.json` + `site/data.json` predate the ranker fix → live items show `data-archetype=""` until next weekly run backfills; render-safe, chrono/hide-rank unaffected. Do not hand-edit the batch.
- Bare-handle imports with zero signal stay untagged by design; triage in `/channels`.
- Web `recordDailyView` no-op shim retained (legacy `DAILY_SET_KEY` pruner); quota flows through seconds accumulator only.
- iOS hard-lockout pauses + shows mindful modal (not a dedicated until-midnight overlay); accrual + snooze contract holds. Full lock screen only if real usage demands it.
- `DailyProgress.viewedCount`/50-reel modal left intact alongside time quota (additive, not a migration).
