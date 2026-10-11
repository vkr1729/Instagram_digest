# REQUIREMENTS.md — Instagram Digest Rebranding & Anti-Doomscroll Engine

## 1. Project Overview & Scope Anchor
- **Target User Scale:** Single-person personal use.
- **Product Vision:** Transform Instagram Digest from an automated reel viewer into **"The Anti-Doomscroll Briefing"** — a mindful, quiet-luxury media briefing that systematically dismantles the 10 behavioral addiction traps outlined in the Satvic Movement video (*"The Brain Behind Your Phone Addiction. We Caught Him!"*, https://youtu.be/FmLJbYBu094).
- **Platform Scope:** Full parity across **Web PWA** (HTML5/CSS3/Vanilla JS) and **Native iOS App** (SwiftUI / SwiftData / AVFoundation).

---

## 2. Locked Functional Requirements

### Decision 1: Rebranding & Identity
- **Primary Brand Name:** Keep **"Instagram Digest"**.
- **Subtitle / Tagline:** **"The Anti-Doomscroll Briefing"**.
- **Manifesto & The 10 Traps Modal:** Add a dedicated, beautifully styled in-app modal breaking down the 10 psychological traps from the Satvic video and detailing how Instagram Digest neutralizes each one.
- **Video Reference:** Embed reference link to `https://youtu.be/FmLJbYBu094` in the manifesto modal and project README.

### Decision 2: Stripping Tier 3 & Interactive Channel Onboarding
- **Strip Legacy Tier 3:** Remove algorithmic home-feed discovery (`extract_external_reels_from_feed`) and its brittle session-challenge code, eliminating bot-detection risks. Routine extraction becomes strictly cookie-free.
- **Deploy Floor & Shortfall:** Keep `MIN_DEPLOY_ITEMS = 150` hard floor intact. Thin weeks halt safely at `shortfall_paused` without touching R2 or corrupting previous archives. Expanding depth uses `--limit-per-creator`.
- **Interactive Additive Channel Onboarding:**
  - Launchable via CLI (`python main.py --import-following`) and via `/channels` dashboard button (when running on local server).
  - Runs an interactive Playwright browser window where the user completes login/2FA manually.
  - Scrapes the following list from the DOM, then **presents a selection review**:
    - Compares imported channels against existing `sources.json`.
    - Channels already present are clearly flagged ("Already Present") with existing categories and favorite status preserved.
    - New channels are displayed with selectable checkboxes so the user can pick/unpick before confirming.
    - Merges selected channels non-destructively into `sources.json` (new items defaulted to `enabled: true`, `archetype: "educational"`).
  - Wipes temporary browser profile and ensures zero session cookies are stored or used for routine video extraction.

### Decision 3: Time-Based Daily Quota & Session Lockout
- **Watch Time Metric:** Accumulated active video playback duration (`daily_watch_seconds`), counting strictly while `<video>` is playing and visible (`!paused && !ended && document.visibilityState === 'visible'`). Buffering, paused states, and background tabs are excluded.
- **Day Boundary:** Local calendar date (`YYYY-MM-DD`).
- **Default Limit:** 20 minutes per calendar day.
- **User-Configurable:** Picker for 10m, 20m, 30m, 45m.
- **Enforcement Modes:**
  1. *Gentle Pause (Default):* Displays a peaceful check-in screen with an explicit option to continue.
  2. *Hard Lockout (Opt-in):* Halts playback and locks the screen until the next calendar day (with emergency local 4-digit PIN override).
- **Storage:** Enforced per-platform locally (`localStorage` in Web PWA / SwiftData `DailyProgress` in iOS).

### Decision 4: Visual Redesign & App Icon
- **App Icon:** Progressive gradient icon fading diagonally from a muted, desaturated Instagram hue (bottom-left) to slate/graphite grayscale (top-right).
- **In-App Palette (Quiet Luxury / Editorial):**
  - **Category Rings:** Replace loud neon conic gradients (`#f09433`, `#dc2743`, `#bc1888`) with calm frosted slate rings (active = solid crisp white/slate `#ffffff`, inactive = translucent `rgba(255,255,255,0.14)`). Zero glowing pink drop shadows.
  - **Action Buttons & Badges:** Replace neon gradient buttons with clean white/black monochrome pills or frosted glass buttons.
  - **Progress Bars:** Calm neutral zinc/amber fill instead of neon pink.

### Decision 5: The Top 5 Anti-Addiction Features
1. **Advance Mode & Breather Toggles:**
   - `advance_mode: 'auto' | 'manual'` (default: `'auto'`).
   - In `auto` mode, reels continue with the familiar 0.5s pause without requiring manual scroll.
   - In `manual` mode, video completion pauses on the final frame with a *"Swipe for next ↓"* indicator and never auto-advances.
   - Separate toggle for **3-second Breather**: when enabled, inserts a 3s skippable pause between reels (breather time excluded from watch quota).
2. **One-Tap Grayscale ("Dopamine-Detox") Mode:**
   - Player toggle applying hardware-accelerated `filter: grayscale(100%)` to eliminate visual overstimulation.
3. **Hard Session Time-Boxing:**
   - Active duration tracking with session timer pill and daily lockout.
4. **Creator Archetype Filtering:**
   - Additive field in `sources.json`: `archetype: "educational" | "craft" | "lifestyle" | "vlog"` without breaking existing category chips.
   - Filter controls in `/channels` and viewer to filter by archetype.
5. **Chronological & Unranked Neutrality Mode:**
   - Ranker still selects the finite Top 250 pool.
   - Viewer UI adds a toggle to **hide rank pills** (`#01`, `#02`) and sort by post date (`taken_at` desc).

---

## 3. Platform Parity Matrix

| Feature | Web PWA (`templates/`) | Native iOS App (`Sources/InstagramDigest/`) |
| :--- | :--- | :--- |
| **Progressive Icon** | `assets/icon.svg`, PNGs, PWA manifest | `Resources/Assets.xcassets/AppIcon.appiconset` |
| **Time-Based Quota** | `localStorage`, `feedContainer` pause | `DailyProgress` model, SwiftUI lock overlay |
| **Advance Mode Toggle** | `player.js` `handleVideoEnd` | `FeedPlayerCoordinator.swift` advance logic |
| **Breather Toggle** | `player.js` overlay timer | `FeedPlayerCoordinator.swift` delay overlay |
| **Grayscale Toggle** | CSS class `.grayscale-mode` | SwiftUI `.grayscale(1.0)` modifier |
| **Quiet Luxury UI** | `styles.css` refactor | SwiftUI view styling (neutral rings & buttons) |
| **Manifesto Modal** | `templates/partials/modals.html` | SwiftUI `ManifestoSheetView.swift` |
| **Cookie-Free Pipeline** | `extractor.py`, `main.py`, remove Tier 3 | N/A (Client reads compiled R2/static feed) |
| **Interactive Import** | `main.py --import-following`, `/channels` | N/A (Admin/Pipeline tooling) |

---

## 4. Verification & Release Criteria
- Local Python test suite passes (`pytest tests/`).
- Web PWA builds cleanly with no console errors or playback regressions.
- Native iOS app builds via XcodeGen, verified via GitHub Actions (`build-ipa.yml`) macOS runner executing unit tests and XCUITest suite.
