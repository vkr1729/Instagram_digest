"""
test_immersive_revamp.py — Static regression tests for the single auto-immersive
player revamp (grill scope Q1–Q6c, accepted 2026-09-11).

Contract under test:
- Single auto-immersive state: play hides top chrome + bottom scrim via
  `immersive-mode`; pause restores. The old `playback-active` twin is retired.
- No per-reel 2x button; latched per-reel 2x via rightmost-35% 500ms hold;
  single right-zone tap exits; reel change resets to default speed.
- No ±10s skip or ripples; center double-tap native fullscreen and the
  horizontal scrub gesture stay. No programmatic Fullscreen API on play.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_no_per_reel_2x_button():
    feed = _read("partials/feed.html")
    js = _read("partials/player.js")
    assert "boost-speed-btn" not in feed
    assert "toggleReelSpeed" not in feed
    assert "function toggleReelSpeed" not in js


def test_no_pm10s_skip_or_ripples():
    js = _read("partials/player.js")
    viewer = _read("viewer.html")
    assert "function skipSeconds" not in js
    assert "function showSkipRipple" not in js
    assert "skipRippleLeft" not in viewer
    assert "skipRippleRight" not in viewer


def test_hold_to_boost_gesture_markers():
    js = _read("partials/player.js")
    # Rightmost-35% zone with a 500ms hold timer driving a latched boost.
    assert "0.65" in js
    assert "holdTimer" in js
    assert "500" in js
    assert "function engageBoost" in js
    assert "function exitBoost" in js
    assert "playbackRate = 2.0" in js
    # Reel change clears the latch back to default speed.
    assert "latchedBoostCard = null" in js


def test_single_immersive_state_play_hides_pause_reveals():
    js = _read("partials/player.js")
    css = _read("partials/styles.css")
    # The playback-active twin state is retired everywhere.
    assert "playback-active" not in js
    assert "playback-active" not in css
    # One computed sync point driven by play / pause / fullscreen changes.
    assert "function syncImmersive" in js
    assert js.count("syncImmersive()") >= 4
    # Immersive hides the top chrome AND the bottom scrim.
    assert ".app-shell.immersive-mode .bottom-scrim" in css


def test_center_fullscreen_and_scrub_kept_no_auto_fullscreen_api():
    js = _read("partials/player.js")
    # Native fullscreen stays manual via center double-tap.
    assert "function toggleUnifiedFullscreen" in js
    assert "requestFullscreen" in js
    handle = js[js.index("function handleDoubleAction"):]
    handle = handle[:handle.index("});", handle.index("feed.addEventListener('dblclick'")) + 3]
    assert "toggleUnifiedFullscreen" in handle
    # Horizontal scrub gesture untouched.
    assert "isHorizontalScrubbing" in js
    assert "seekHud" in js
    # CSS-only auto-immersive: play path must never request OS fullscreen.
    play_region = js[js.index("function playCardVideo"):js.index("function handleVideoEnd")]
    assert "ullscreen" not in play_region
