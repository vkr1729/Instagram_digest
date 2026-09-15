"""
test_resume_after_unlock.py — PWA must reopen at the last-active reel.

Regression: the PIN lock screen hides #feedContainer at player init, so the
initial scroll is deferred ("defer scroll to resumeInitialPosition"). But
nothing called resumeInitialPosition() after unlockScreen() revealed the
feed: every launch opened at reel #1, and the autoplay observer then
overwrote the saved last-active id with reel #1 — permanently wedging the
PWA at the top of the feed. Deep links (?reel=) through the lock broke too.

Covers both layers:
1. Unit: a PIN-locked build hides the feed and unlockScreen() performs a
   one-shot resumeInitialPosition() after revealing it.
2. E2E (Playwright): with pin_ok + last-active preset, a locked build loads
   scrolled to the last-active reel and preserves the stored id; ?reel=
   takes precedence through the lock.
"""

from __future__ import annotations

from pathlib import Path

import config
from site_builder import build_site


WEEK_ID = "2026-09-14"
LAST_ACTIVE_KEY = f"ig_digest_last_active_id_{WEEK_ID}"
CATEGORIES = ["ai_tech", "finance", "health", "food", "entertainment", "niche"]


def _sample_digest(n: int = 12) -> dict:
    items = []
    for i in range(n):
        rid = f"reel_{i + 1:02d}"
        items.append(
            {
                "id": rid,
                "creator_handle": f"creator{i + 1}",
                "creator_name": f"Creator {i + 1}",
                "category": CATEGORIES[i % len(CATEGORIES)],
                "rank": i + 1,
                "rank_display": f"#{i + 1:02d}",
                "view_count": 100000 + i,
                "caption": f"Caption for {rid}",
                "thumbnail": f"https://example.com/{rid}.jpg",
                "video_url": f"https://example.com/{rid}.mp4",
            }
        )
    return {"run_date": WEEK_ID, "items": items}


def _build_locked_site(tmp_path: Path, monkeypatch, n: int = 12) -> Path:
    """Build with a viewing PIN so the feed starts hidden behind the lock."""
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    monkeypatch.setenv("VIEWING_PIN", "1234")
    r2_index, _ = build_site(_sample_digest(n))
    assert r2_index.exists()
    return r2_index


def test_locked_build_defers_scroll_and_resumes_on_unlock(tmp_path, monkeypatch):
    """unlockScreen() must perform the deferred initial resume after reveal."""
    index = _build_locked_site(tmp_path, monkeypatch, n=3)
    html = index.read_text(encoding="utf-8")

    # Locked build: PIN gate present, feed hidden at player init.
    assert 'id="feedContainer" style="display:none"' in html
    assert "window.__PIN_HASH" in html

    # The deferred scroll has a caller: unlockScreen() resumes exactly once.
    start = html.index("function unlockScreen()")
    end = html.index("function isOwnerDevice()")
    unlock_body = html[start:end]
    assert "window.resumeInitialPosition()" in unlock_body
    assert "__DID_INITIAL_RESUME" in unlock_body


def test_pwa_resumes_last_active_after_pin_unlock(tmp_path, monkeypatch):
    """E2E: locked PWA opens at the last-active reel, not reel #1."""
    from playwright.sync_api import sync_playwright

    target_idx = 8  # 0-based -> reel_09
    target_id = f"reel_{target_idx + 1:02d}"
    index = _build_locked_site(tmp_path, monkeypatch, n=12)
    url = f"file://{index.resolve()}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                viewport={"width": 393, "height": 852},
                has_touch=True,
                is_mobile=True,
            )
            ctx.add_init_script(
                f"localStorage.setItem('digest_pin_ok','1');"
                f"localStorage.setItem('{LAST_ACTIVE_KEY}','{target_id}');"
            )
            page = ctx.new_page()
            page.goto(url)
            page.wait_for_selector(".reel-card", state="visible")
            # Resume scroll converges via rAF + settle timeouts after unlock.
            page.wait_for_function(
                "() => { const f = document.getElementById('feedContainer');"
                " const cards = [...document.querySelectorAll('.reel-card')];"
                f" return f && cards.length === 12 && "
                f"Math.abs(f.scrollTop - cards[{target_idx}].offsetTop) <= 24; }}",
                timeout=8000,
            )

            # Saved position must survive the launch (pre-fix: observer
            # autoplayed reel #1 and overwrote it).
            last_active = page.evaluate(
                f"() => localStorage.getItem('{LAST_ACTIVE_KEY}')"
            )
            assert last_active == target_id

            active_idx = page.evaluate(
                "() => [...document.querySelectorAll('.reel-card')]"
                ".findIndex(c => c.classList.contains('is-active'))"
            )
            assert active_idx == target_idx

            # ?reel= deep link still wins over last-active through the lock.
            page.goto(f"{url}?reel=reel_04")
            page.wait_for_selector(".reel-card", state="visible")
            page.wait_for_function(
                "() => { const f = document.getElementById('feedContainer');"
                " const cards = [...document.querySelectorAll('.reel-card')];"
                " return f && Math.abs(f.scrollTop - cards[3].offsetTop) <= 24; }",
                timeout=8000,
            )
        finally:
            browser.close()
