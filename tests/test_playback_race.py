"""
test_playback_race.py — Static regression tests for the fast-scroll autoplay race.

History: fast flings occasionally left the settled reel paused until the user
tapped (or scrolled away and back). Two defects in
templates/partials/player.js combined to cause it:

R1: the pause-neighbors loop in playCardVideo() set `v.currentTime = 0`
    unconditionally. With readyState HAVE_NOTHING that setter throws
    InvalidStateError, aborting playCardVideo() before the NEW card's
    triggerPlay ran — the settled reel sat paused. Slow scrolls never hit
    it because neighbors had settled (paused, so skipped).

R2: every play() rejection — including AbortError from being superseded by
    a newer card mid-fling — fell into the muted-autoplay fallback, which
    muted and replayed a STALE off-screen card and flipped
    playback-active for the wrong reel.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _js():
    return (TEMPLATES / "partials" / "player.js").read_text(encoding="utf-8")


def _pause_neighbors_block(js):
    start = js.index("if (v && !v.paused)")
    end = js.index("currentTime = 0", start)
    return js[start:end]


def _trigger_play_block(js):
    start = js.index("const triggerPlay")
    end = js.index("if (video.readyState >= 2)", start)
    return js[start:end]


def test_neighbor_reset_guards_currenttime_by_readystate():
    js = _js()
    block = _pause_neighbors_block(js)
    assert "v.pause()" in block
    # HAVE_NOTHING guard: zeroing currentTime with no metadata throws.
    assert "readyState" in block
    # The guarded reset line itself still exists just past the window.
    assert js.index("currentTime = 0", js.index("if (v && !v.paused)")) > 0


def test_play_rejection_distinguishes_abort_from_policy_block():
    block = _trigger_play_block(_js())
    # An interrupted (superseded) play request must not hit the muted fallback.
    assert "AbortError" in block
    assert "video.muted = true" in block


def test_stale_playback_attempts_are_dropped():
    block = _trigger_play_block(_js())
    # Stale generations / non-current cards must never touch playback state.
    assert "myGen !== navGen" in block
    assert "currentActiveCard !== card" in block
