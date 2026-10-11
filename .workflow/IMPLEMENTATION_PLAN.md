# IMPLEMENTATION_PLAN.md — Instagram Digest Rebranding & Anti-Doomscroll Engine

## Architectural Grounding & Dependency Order

This plan incorporates all 11 architectural findings from `.workflow/FRONTIER_PLAN_REVIEW.md`. Execution follows a strict dependency order: data plumbing first, pipeline second, Web PWA third, visual & asset sweep fourth, onboarding fifth, and native iOS parity sixth.

---

## Phase 1: Data Plumbing (`archetype` & `timestamp`/`taken_at` End-to-End)

1. **`sources.json` Schema**:
   - Additive optional `archetype: "educational" | "craft" | "lifestyle" | "vlog" | null` field (`null` = untagged, shown in All).
   - Seed migration map: `ai_tech`, `finance`, `health`, `food`, `niche` → `"educational"`; `entertainment` → `"lifestyle"`.
2. **Digest JSON & `site_builder.py`**:
   - Preserve `archetype` and `timestamp`/`taken_at` through candidate transformation into rendered items.
   - `templates/partials/feed.html`: emit `data-archetype="{{ item.archetype }}"` and `data-timestamp="{{ item.timestamp }}"` attributes on `.reel-card`.
   - `templates/partials/grid_view.html`: include `data-archetype` and `data-timestamp` on grid cards.
3. **Native iOS Model (`ReelItem.swift`)**:
   - Add optional `takenAt: Date?` and `archetype: String?` in `ReelItem.swift` (`decodeIfPresent`, preserving backward compatibility with older archives).

---

## Phase 2: Pipeline Hardening (Tier 3 Strip & Shortfall Safety)

1. **Explicit Tier 3 Removal**:
   - `main.py`: Skip Tier 3 at all three call sites (shortfall resume at :1194, main Tier-3 at :1882, and `--expand` at :2668) with descriptive log lines.
   - `--expand`: Refuse with clear error ("Cookie-free mode active: external feed expand is deprecated; use `--limit-per-creator`").
   - `scripts/topup_digest.py`: Update to skip feed extraction.
   - `config.py`: Deprecate `MAX_EXTERNAL_SHARE`, `MAX_FEED_EVALUATIONS`, `MIN_EXTERNAL_LIKES`, `MIN_EXTERNAL_COMMENTS`.
2. **Shortfall & Floor Safety**:
   - Maintain `MIN_DEPLOY_ITEMS = int(TOP_DIGEST_COUNT * 0.6)` hard floor.
   - If banked candidates < floor, pause cleanly at `shortfall_paused` without mutating R2 or corrupting previous digests.
3. **Test Mocks Update**:
   - Update tests (`test_sync_resume.py`, `test_expand_resume.py`, `test_enhancements.py`) to align with Tier 3 bypass.

---

## Phase 3: Web PWA Anti-Addiction Engine

1. **Atomic Count→Seconds Migration**:
   - Delete legacy 50-reel count tracker in `templates/partials/player.js` (`DAILY_SET_KEY`, `DAILY_SNOOZE_KEY`, 50-copy strings).
   - Implement delta-based active playback accumulator (`daily_watch_seconds`), counting strictly while `!video.paused && !video.ended && document.visibilityState === 'visible'`.
   - Day boundary: local `YYYY-MM-DD`. Prune older date keys via updated regex in `player.js`.
   - Time quota picker: 10m, 20m, 30m, 45m (default: 20m).
   - Modes:
     - *Gentle Pause (Default):* Mindful check-in modal with elapsed time and "Take a Break" / "Continue" options.
     - *Hard Lockout:* Full-screen overlay halting playback until next calendar day, with 4-digit salted local PIN override.
   - Header session pill: replace `🎯 0/50` with `⏱️ 8m/20m`.
2. **Advance Mode & Breather Semantics**:
   - `advance_mode: 'auto' | 'manual'` in `localStorage` (default: `'auto'`).
   - In `auto`: Keeps current 0.5s auto-advance toast and behavior.
   - In `manual`: Video `ended` event pauses on final frame, shows "Swipe for next ↓" affordance, and prevents `IntersectionObserver` from auto-advancing past the stopped reel.
   - `breather_enabled: bool` (default: `false`): Optional 3-second calm breather countdown toast between reels. Skippable by tap; breather time excluded from watch quota.
3. **Settings Modal (`#settingsModal`)**:
   - Dedicated clean modal in `templates/partials/modals.html` hosting:
     - Time quota target slider & mode selector (Gentle vs Hard Lockout + PIN setup).
     - Advance mode toggle (`auto` vs `manual`).
     - Breather toggle (3s pause).
     - Grayscale toggle.
     - Rank-hide toggle (`.hide-ranks` class covering feed and grid).
     - Chronological sort toggle (reordering Top-250 batch client-side by `data-timestamp` descending).
   - Header bar retains only brand, session pill `⏱️`, settings gear `⚙️`, and info `ℹ️` trigger.

---

## Phase 4: Visual Redesign, Progressive Icon & Manifesto Rebranding

1. **Quiet Luxury Palette Sweep**:
   - Pre-work grep sweep across `templates/partials/styles.css`, `channels.html`, `dashboard.html`, `modals.html`.
   - Replace all instances of `--ig-gradient`, conic gradients, and pink drop shadows:
     - Category rings: Calm frosted slate borders (Active = solid white `border: 2px solid #ffffff`, Inactive = translucent `rgba(255,255,255,0.14)`).
     - Buttons: Monochrome white/frosted pills (`#ffffff` background, `#000000` text).
     - Progress bars: Neutral zinc/amber.
   - Feed container hardware-accelerated `.grayscale-mode` (`filter: grayscale(100%)`).
2. **Progressive Gradient Icon**:
   - `assets/icon.svg` & `site/icon.svg`: Diagonal gradient fading from muted warm Instagram hue (`#d97736`, `#be436b`) at bottom-left into matte slate/graphite (`#27272a`, `#18181b`) at top-right.
   - Render and update `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`, and iOS `AppIcon.appiconset`.
   - Bump `templates/sw.js` cache version.
3. **The 10 Traps Manifesto Modal**:
   - `#manifestoModal` in `templates/partials/modals.html` referencing Satvic Movement's video (`https://youtu.be/FmLJbYBu094`), detailing how the 10 traps are eliminated.
   - Subtitle in header: *"The Anti-Doomscroll Briefing"*.

---

## Phase 5: Interactive Additive Channel Onboarding

1. **CLI Canonical Tool (`interactive_onboarding.py`)**:
   - Command `python main.py --import-following`: Launches headed Playwright browser with a temporary user directory.
   - User logs in; script extracts followed accounts DOM.
   - Reuses `audit_channels.import_missing` logic to compute diff vs existing `sources.json`:
     - Preserves existing accounts (`favorite`, `category`, `enabled`).
     - Shows diff summary in CLI with selection review.
     - New imports default to `category: "niche"`, `archetype: null` (shown in All, triaged in `/channels`).
   - Wipes temporary profile upon exit; asserts zero persistent cookies written (`cookies.json`, `cookies.txt`).
   - CSV / handle-paste fallback input supported.
2. **Local Dashboard Integration**:
   - In `/channels`, "Import Following" button active only when `is_local` on server.
   - Displays modal with diff table (already present vs new checkboxes) before non-destructive commit.

---

## Phase 6: Native iOS Parity (SwiftUI / SwiftData)

1. **Accurate File Targets**:
   - `Sources/InstagramDigest/Models/DigestState.swift`: Extend `DailyProgress` with `watchDurationSeconds: Double`, `targetMinutes: Int`, `enforcementMode: String`. Update four init call sites across `InstagramDigestApp.swift:72, 610, 742, 802`.
   - `Sources/InstagramDigest/Engine/AVPlayerPool.swift`:
     - Extend with `advanceMode` enum (`.auto`, `.manual`) and `breatherEnabled`.
     - In `handlePlaybackEnded:573`, respect `advanceMode`.
     - Accumulate playback seconds using existing 0.5s time observer (`attachTimeObserver:649`) while active.
   - `Sources/InstagramDigest/Views/Feed/`:
     - `ReelCardView.swift` & `FeedPagerView.swift`: Add `.grayscale(isGrayscale ? 1.0 : 0.0)` modifier, manual swipe affordance, rank-hide, and chrono sort.
     - `StoryCategoryBarView.swift`: Update rings to frosted slate styling without glowing pink shadows.
   - `Sources/InstagramDigest/Views/Modals/`:
     - Replace `MindfulDailyModalView` with time-based check-in & lockout.
     - Add `ManifestoSheetView.swift` referencing the 10 traps and video.
     - Add `SettingsSheetView.swift` hosting the anti-addiction toggles.
   - `Resources/Assets.xcassets/AppIcon.appiconset`: Replace icon assets with progressive gradient artwork.

---

## Phase 7: Verification & UAT

1. **Local Automated Tests**:
   - Run `pytest tests/` covering data plumbing, Tier 3 skips, onboarding deduplication, and zero-cookie assertions.
   - Test migration logic (count to seconds accumulator, date rollover).
2. **Web PWA Build Smoke Check**:
   - Verify `python main.py --build-only` compiles cleanly without missing attributes or syntax errors.
3. **Stage 4: Headless Frontier Code Audit & Direct Workspace Remediation**:
   - Launch `cmd -t -p` to perform independent code audit, verify personal-scale adherence, and directly patch any bugs in workspace files.
4. **Stage 5: GitHub Actions macOS Runner CI**:
   - Push branch and run `.github/workflows/build-ipa.yml` to compile the iOS app with XcodeGen, boot simulator, and execute unit tests and XCUITest suite until functional app is verified.
