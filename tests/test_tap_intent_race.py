"""
test_tap_intent_race.py — Tap committed while paused must not pause a video
that starts during the 320ms single-tap debounce.

Field traces (Sep 12 "Tap - Play Pause issue", Sep 15 mediadebug) show the
signature repeatedly: ev-play -> ev-pause ~320ms later at t~0. The tap timer
read video.paused at FIRE time: a tap committed on a paused/loading video
executed after autoplay won the race, pausing it instantly — and the
manual-pause cooldown then wedged it until the next tap ("touch it, it plays
and pauses, tap again to start").

Fix: capture video.paused synchronously in the click handler; the timer
no-ops when the state flipped during the debounce (intent satisfied).
"""

from __future__ import annotations

import base64
import io
import wave
from pathlib import Path

import config
from site_builder import build_site

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _js():
    return (TEMPLATES / "partials" / "player.js").read_text(encoding="utf-8")


def _card_click_block(js):
    start = js.index("card.addEventListener('click'")
    return js[start:js.index("video.addEventListener('ended'", start)]


def test_tap_intent_captured_synchronously():
    block = _card_click_block(_js())
    cap = block.index("const pausedAtTap = Boolean(video.paused ||")
    # Captured at handler entry, before the debounce timer is armed, and
    # coerced: strict !== against a 0/number would no-op every pause toggle.
    assert cap < block.index("pendingSingleTapTimer = setTimeout")


def test_bootstrap_play_stamped_and_consumed():
    js = _js()
    assert "let gesturePlayAt = 0;" in js
    start = js.index("function handleInitialInteraction")
    boot = js[start:js.index("requestWakeLock();", start)]
    assert "if (v.paused) gesturePlayAt = performance.now();" in boot
    block = _card_click_block(js)
    cap = block.index("const tapPlayedAt = gesturePlayAt;")
    assert "gesturePlayAt = 0;" in block[cap:cap + 120]
    assert "tapPlayedAt" in block[block.index("const pausedAtTap"):][:200]


def test_tap_timer_noops_when_state_flipped_during_debounce():
    block = _card_click_block(_js())
    timer = block[block.index("pendingSingleTapTimer = setTimeout"):]
    noop = timer.index("video.paused !== pausedAtTap")
    toggle = timer.index("if (video.paused) {")
    assert noop < toggle
    assert "tap-noop" in timer[noop:toggle + 60]


def test_intent_check_preserves_guard_priority():
    block = _card_click_block(_js())
    timer = block[block.index("pendingSingleTapTimer = setTimeout"):]
    noop = timer.index("video.paused !== pausedAtTap")
    # Boost-exit and tap-to-unmute keep priority over the intent check.
    assert timer.index("exitBoost(video);") < noop
    assert timer.index("tapUnmutedAt") < noop


def _wav_data_url(seconds: int = 30) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00" * 8000 * 2 * seconds)
    return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()


def _sample_digest(n: int = 3) -> dict:
    cats = ["ai_tech", "finance", "health"]
    return {
        "run_date": "2026-09-14",
        "items": [
            {
                "id": f"reel_{i + 1:02d}",
                "creator_handle": f"creator{i + 1}",
                "creator_name": f"Creator {i + 1}",
                "category": cats[i % len(cats)],
                "rank": i + 1,
                "rank_display": f"#{i + 1:02d}",
                "view_count": 100000 + i,
                "caption": f"Caption {i + 1}",
                "thumbnail": f"https://example.com/reel_{i + 1:02d}.jpg",
                "video_url": f"https://example.com/reel_{i + 1:02d}.mp4",
            }
            for i in range(n)
        ],
    }


def _vstate(page) -> dict:
    return page.evaluate(
        "() => { const c = [...document.querySelectorAll('.reel-card')]"
        ".find(x => x.classList.contains('is-active'));"
        " const v = c.querySelector('.reel-video');"
        " return { paused: v.paused, muted: v.muted, t: +v.currentTime.toFixed(2) }; }"
    )


def _load_muted_media(page, wav_url: str):
    """Point the active video at playable media (muted play needs no gesture)."""
    page.evaluate(
        "(url) => { const c = [...document.querySelectorAll('.reel-card')]"
        ".find(x => x.classList.contains('is-active'));"
        " const v = c.querySelector('.reel-video');"
        " v.muted = true; v.src = url; return v.play().catch(() => 'deferred'); }",
        wav_url,
    )
    page.wait_for_function(
        "() => { const c = [...document.querySelectorAll('.reel-card')]"
        ".find(x => x.classList.contains('is-active'));"
        " return !c.querySelector('.reel-video').paused; }",
        timeout=8000,
    )


def _fresh_page(p, url: str):
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 393, "height": 852}, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_selector(".reel-card", state="visible")
    page.wait_for_timeout(800)  # let init settle (stuck waiter, no media yet)
    return browser, page


def test_tap_during_load_wins_playing_not_paused(tmp_path, monkeypatch):
    """Autoplay starting inside the tap debounce must survive the timer."""
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    r2_index, _ = build_site(_sample_digest())
    url = f"file://{r2_index.resolve()}?mediadebug=1"
    wav_url = _wav_data_url()

    with sync_playwright() as p:
        browser, page = _fresh_page(p, url)
        try:
            _load_muted_media(page, wav_url)
            # Paused + unmuted: the tap carries no unmute stamp, so the
            # pre-fix timer would pause the video that starts mid-debounce.
            page.evaluate(
                "() => { const c = [...document.querySelectorAll('.reel-card')]"
                ".find(x => x.classList.contains('is-active'));"
                " const v = c.querySelector('.reel-video'); v.pause(); v.muted = false; }"
            )
            assert _vstate(page)["paused"] is True

            page.touchscreen.tap(150, 400)
            page.wait_for_timeout(60)
            # Autoplay wins mid-debounce (data arrives right after the tap).
            page.evaluate(
                "() => { const c = [...document.querySelectorAll('.reel-card')]"
                ".find(x => x.classList.contains('is-active'));"
                " const v = c.querySelector('.reel-video');"
                " v.muted = true; return v.play(); }"
            )
            page.wait_for_timeout(700)  # past the 320ms tap timer
            state = _vstate(page)
            assert state["paused"] is False, f"tap raced autoplay and paused it: {state}"
            overlay = page.evaluate(
                "() => (document.getElementById('mediaDebugOverlay') || {}).textContent || ''"
            )
            assert "tap-noop" in overlay
        finally:
            browser.close()


def test_tap_during_pause_wins_paused_not_replayed(tmp_path, monkeypatch):
    """A video pausing inside the debounce must not be replayed by the timer."""
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    r2_index, _ = build_site(_sample_digest())
    url = f"file://{r2_index.resolve()}?mediadebug=1"
    wav_url = _wav_data_url()

    with sync_playwright() as p:
        browser, page = _fresh_page(p, url)
        try:
            _load_muted_media(page, wav_url)
            page.evaluate(
                "() => { const c = [...document.querySelectorAll('.reel-card')]"
                ".find(x => x.classList.contains('is-active'));"
                " c.querySelector('.reel-video').muted = false; }"
            )
            assert _vstate(page)["paused"] is False

            page.touchscreen.tap(150, 400)
            page.wait_for_timeout(60)
            # Video pauses mid-debounce (ended / driven elsewhere).
            page.evaluate(
                "() => { const c = [...document.querySelectorAll('.reel-card')]"
                ".find(x => x.classList.contains('is-active'));"
                " c.querySelector('.reel-video').pause(); }"
            )
            page.wait_for_timeout(700)
            state = _vstate(page)
            assert state["paused"] is True, f"tap replayed a video that paused: {state}"
        finally:
            browser.close()


def test_first_tap_plays_and_second_tap_pauses(tmp_path, monkeypatch):
    """First tap plays (bootstrap + intent no-op, not bootstrap + re-pause);
    second tap pauses. Steady-state taps toggle exactly once."""
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    r2_index, _ = build_site(_sample_digest())
    url = f"file://{r2_index.resolve()}"
    wav_url = _wav_data_url()

    with sync_playwright() as p:
        browser, page = _fresh_page(p, url)
        try:
            _load_muted_media(page, wav_url)
            page.evaluate(
                "() => { const c = [...document.querySelectorAll('.reel-card')]"
                ".find(x => x.classList.contains('is-active'));"
                " const v = c.querySelector('.reel-video'); v.pause(); v.muted = false; }"
            )
            page.wait_for_timeout(1600)  # clear any manual-pause cooldown

            page.touchscreen.tap(150, 400)
            page.wait_for_function(
                "() => { const c = [...document.querySelectorAll('.reel-card')]"
                ".find(x => x.classList.contains('is-active'));"
                " return !c.querySelector('.reel-video').paused; }",
                timeout=5000,
            )
            page.wait_for_timeout(800)
            assert _vstate(page)["paused"] is False

            page.touchscreen.tap(150, 400)
            page.wait_for_function(
                "() => { const c = [...document.querySelectorAll('.reel-card')]"
                ".find(x => x.classList.contains('is-active'));"
                " return c.querySelector('.reel-video').paused; }",
                timeout=5000,
            )
            page.wait_for_timeout(800)
            assert _vstate(page)["paused"] is True
        finally:
            browser.close()
