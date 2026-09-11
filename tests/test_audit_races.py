"""
test_audit_races.py — Static regression tests for the 11 audit findings
(P0-1–P0-5, P1-6–P1-10, P2-11) in templates/partials/player.js.

Each test pins the fix marker so the same bug class cannot silently return.
Style follows tests/test_dom_perf.py (substring markers on template source).
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _js():
    return (TEMPLATES / "partials" / "player.js").read_text(encoding="utf-8")


def _fn(js, name, length=900):
    start = js.index("function " + name)
    return js[start:start + length]


def test_filter_hide_guards_currenttime():
    # P0-1: hide path must not throw at HAVE_NOTHING mid filter loop.
    js = _js()
    anchor = js.index("Release the decoder for hidden cards")
    assert "readyState" in js[max(0, anchor - 300):anchor]


def test_scrub_commit_guards_readystate():
    # P0-2: scrub release must not write currentTime with no metadata.
    js = _js()
    anchor = js.index("video.currentTime = scrubTargetTime")
    assert "readyState" in js[max(0, anchor - 200):anchor]


def test_single_flight_ready_waiter():
    # P0-3: redundant calls share one waiter and stop re-issuing load().
    js = _js()
    assert "readyWaiter" in js
    assert "networkState === 2" in js


def test_same_card_replay_does_not_bump_generation():
    # P0-4: already-playing same card returns before ++navGen.
    js = _js()
    start = js.index("function playCardVideo")
    block = js[start:js.index("const myGen = ++navGen", start)]
    assert "currentActiveCard === card" in block
    assert "return;" in block
    assert "++navGen" not in block


def test_paused_launch_invalidates_pending_nav():
    # P0-5: prepareCardVideoPaused must cancel queued goToCard rAF chains.
    assert "navGen++" in _fn(_js(), "prepareCardVideoPaused")


def test_pill_unmute_drops_stale_play():
    # P1-6: pill play() must not leave a retargeted card playing offscreen.
    block = _fn(_js(), "unmuteFromPill")
    assert "const myGen = navGen" in block
    assert "v.pause()" in block


def test_dead_cards_marked_without_hijack():
    # P1-7: offscreen errors mark dead without navigating; position is
    # snapshotted before the card leaves the visible list.
    js = _js()
    start = js.index("function skipDeadCard")
    block = js[start:start + 1600]
    assert "wasCurrent" in block
    assert block.index("const cards = visibleCards()") < block.index("card.dataset.dead = '1'")
    err = js[js.index("media error listener"):js.index("media error listener") + 500]
    assert "skipDeadCard(card, 'media error')" in err
    assert "if (card ===" not in err


def test_mute_pill_hide_is_card_scoped():
    # P1-8: no global wipe of other cards' pills.
    assert "querySelectorAll('.mute-pill.visible')" not in _js()


def test_bootstrap_disarms_sibling_registrations():
    # P1-9: first gesture must disarm the other once-per-type registrations.
    js = _js()
    assert "AbortController" in js
    assert "initController" in js
    assert "signal" in js


def test_hold_release_flag_survives_to_feed():
    # P1-10: the card swallow branch must NOT clear the flag; the bubbled
    # feed handler consumes it so no phantom double-tap is seeded.
    js = _js()
    start = js.index("Hold-2x release clicks are swallowed here")
    assert "suppressNextClick = false" not in js[start:start + 400]
    assert js.count("suppressNextClick = false") >= 2


def test_toast_timer_single_flight():
    # P2-11: overlapping toasts must not hide each other early.
    js = _js()
    assert "toastTimer" in js
    assert "clearTimeout(toastTimer)" in js


def test_mediadebug_tracer_present_and_gated():
    # Field diagnosis overlay: present, strictly query-flag gated, and wired
    # through every playback decision point (play/nav/pause/reject/event).
    js = _js()
    assert "mediadebug" in js
    assert "mediaDebugOverlay" in js
    assert "function mtrace" in js
    assert js.count("mtrace(`") >= 12


def test_manual_pause_cooldown_blocks_auto_resume():
    # iOS tap-scroll-into-view must not auto-resume a tap-paused card;
    # explicit tap resume bypasses via isManual.
    js = _js()
    assert "manualPause" in js
    assert "MANUAL_PAUSE_COOLDOWN_MS" in js
    assert "isManual" in js
    assert "playCardVideo(card, true)" in js


def test_total_playback_failure_traced_and_tappable():
    # A dead reel (even the muted fallback rejected, e.g. Low Power Mode)
    # must be visible in the trace and offer a tap affordance.
    js = _js()
    assert "play-reject2" in js
    assert js.count("textContent = '▶'") >= 2


def test_ready_waiter_cleared_on_src_detach():
    # A waiter token must never outlive its fetch: cleared on ready, on
    # error, and wherever src is detached (sliding window, filter hide).
    js = _js()
    assert js.count("delete video.dataset.readyWaiter") >= 4
