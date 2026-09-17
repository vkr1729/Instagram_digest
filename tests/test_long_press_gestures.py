"""
test_long_press_gestures.py — Regression tests for long-press gestures:
1. Center-Lower long-press bookmarks the reel automatically.
2. Lower-Right long-press triggers the share action automatically.
3. Upper-Right long-press preserves 2x playback boost.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_hold_timer_zone_partitioning():
    js = _read("partials/player.js")
    assert "function armHoldTimer" in js
    assert "triggerGestureShare" in js
    assert "triggerGestureBookmark" in js
    assert "triggerHapticFeedback" in js
    assert "showGestureIconPop" in js

    # Partition checks in armHoldTimer
    assert "isLower = y > h * 0.65" in js
    assert "isRight = x > w * HOLD_ZONE" in js
    assert "isCenter = x >= w * 0.35 && x <= w * HOLD_ZONE" in js

    # Zone action dispatches
    assert "isRight && !isLower" in js
    assert "engageBoost(card, video)" in js
    assert "isRight && isLower" in js
    assert "triggerGestureShare(card, x, y)" in js
    assert "isCenter && isLower" in js
    assert "triggerGestureBookmark(card, x, y)" in js


def test_pointerdown_gesture_arming():
    js = _read("partials/player.js")
    # Verify pointerdown arms the timer with coordinates for right and center-lower
    pointerdown_block = js[js.index("window.addEventListener('pointerdown'"):js.index("window.addEventListener('pointermove'")]
    assert "armHoldTimer(currentActiveCard, e.clientX, e.clientY)" in pointerdown_block
    assert "isRight || (isCenter && isLower)" in pointerdown_block


def test_gesture_bookmark_and_share_implementations():
    js = _read("partials/player.js")

    # Bookmark gesture implementation
    bm_block = js[js.index("async function triggerGestureBookmark"):js.index("function triggerGestureShare")]
    assert "triggerHapticFeedback()" in bm_block
    assert "showGestureIconPop('🔖', x, y)" in bm_block
    assert "toggleBookmark(reelId, card)" in bm_block

    # Share gesture implementation
    share_block = js[js.index("function triggerGestureShare"):js.index("function armHoldTimer")]
    assert "triggerHapticFeedback()" in share_block
    assert "showGestureIconPop('↗️', x, y)" in share_block
    assert "shareReelWhatsApp(reelId, handle)" in share_block


def test_gesture_pop_icon_css():
    css = _read("partials/styles.css")
    assert ".gesture-pop-icon" in css
    assert "gesturePopAnim" in css
    assert "pointer-events: none" in css
    assert "@keyframes gesturePopAnim" in css


def test_touchmove_cancels_hold():
    js = _read("partials/player.js")
    touchmove_block = js[js.index("feed.addEventListener('touchmove'"):js.index("feed.addEventListener('touchend'")]
    assert "cancelHold()" in touchmove_block
