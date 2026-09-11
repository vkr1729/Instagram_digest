"""
test_tap_unmute.py — Static regression tests for the tap-to-unmute pause bug.

History: after a muted-autoplay fallback (iOS-unmuted-autoplay policy), the
first tap meant "sound" but the video paused instead. Two handlers in
templates/partials/player.js fought inside one gesture:

  1. restoreAudioOnInteraction (window touchstart) flipped video.muted=false
     and hid the mute pill mid-gesture;
  2. the hidden pill retargeted the click onto the video, so the card's
     single-tap timer ran video.pause().

Fix: gesture handlers stamp gestureUnmuteAt when they flip muted->unmuted;
the card click handler captures/consumes it synchronously and the tap timer
swallows the pause-toggle for a playing video unmuted by the same gesture.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _js():
    return (TEMPLATES / "partials" / "player.js").read_text(encoding="utf-8")


def _restore_block(js):
    start = js.index("function restoreAudioOnInteraction")
    return js[start:js.index("}", js.index("hideMutePill(currentActiveCard)", start))]


def _bootstrap_block(js):
    start = js.index("function handleInitialInteraction")
    return js[start:js.index("requestWakeLock();", start)]


def _card_click_block(js):
    start = js.index("card.addEventListener('click'")
    return js[start:js.index("video.addEventListener('ended'", start)]


def test_gesture_unmute_stamp_declared():
    assert "let gestureUnmuteAt = 0;" in _js()


def test_touchstart_restore_stamps_on_unmute():
    block = _restore_block(_js())
    assert "activeVideo.muted = false" in block
    assert "gestureUnmuteAt = performance.now()" in block


def test_first_interaction_bootstrap_stamps_on_unmute():
    block = _bootstrap_block(_js())
    assert "v.muted = false" in block
    assert "gestureUnmuteAt = performance.now()" in block


def test_pill_tap_clears_stamp():
    js = _js()
    start = js.index("function unmuteFromPill")
    block = js[start:js.index("hideMutePill(card);", start)]
    # Explicit pill tap owns the unmute: nothing pending to swallow.
    assert "gestureUnmuteAt = 0;" in block


def test_card_click_consumes_stamp_synchronously():
    block = _card_click_block(_js())
    cap = block.index("const tapUnmutedAt = gestureUnmuteAt;")
    # Consumed at handler entry, before the suppress/scroll early-returns, so
    # only this gesture's toggle can be suppressed.
    assert block.index("if (suppressNextClick)") > cap
    assert "gestureUnmuteAt = 0;" in block[cap:cap + 120]


def test_tap_timer_swallows_pause_after_same_gesture_unmute():
    block = _card_click_block(_js())
    timer = block[block.index("pendingSingleTapTimer = setTimeout"):]
    swallow = timer.index("tapUnmutedAt")
    # Boost-exit keeps priority; paused video still resumes (never dead tap).
    assert timer.index("exitBoost(video);") < swallow
    assert "!video.paused" in timer[swallow:swallow + 200]
    assert "if (video.paused) {" in timer[swallow:]
