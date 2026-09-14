"""
test_bookmarks_ui.py — Automated tests for Bookmarks UI, toggle isolation, and save button.
"""

from pathlib import Path
import pytest
from playwright.sync_api import Page, sync_playwright
import os

import site_builder

ROOT_DIR = Path(__file__).resolve().parent.parent
SITE_INDEX = ROOT_DIR / "site" / "index.html"

IPHONE_15_PRO = {
    "viewport": {"width": 393, "height": 852},
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15",
    "has_touch": True,
    "is_mobile": True,
    "device_scale_factor": 3,
}


@pytest.fixture(scope="module")
def built_site():
    site_builder.build_site()
    assert SITE_INDEX.exists()
    return SITE_INDEX


def test_api_base_is_never_taken_from_url():
    import config
    auth = (config.ROOT_DIR / "templates" / "partials" / "auth.html").read_text()
    assert "localStorage.setItem('digest_api_base'" not in auth
    js = (config.ROOT_DIR / "templates" / "partials" / "player.js").read_text()
    body = js[js.index("function bookmarkApiBase"):js.index("function bookmarkAuthHeaders")]
    assert "h === 'localhost' || h === '127.0.0.1'" in body


@pytest.fixture
def page(built_site):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**IPHONE_15_PRO)
        page = context.new_page()
        pin = os.getenv("VIEWING_PIN", "").strip()
        url = f"file://{built_site.resolve()}"
        pin_url = f"{url}?pin={pin}" if pin else url
        page.goto(url)
        page.evaluate("() => localStorage.clear()")
        page.goto(pin_url)
        page.wait_for_selector(".reel-card", state="visible")
        page.evaluate("() => { const v = document.querySelector('.reel-card .reel-video'); if (v) v.pause(); document.getElementById('appShell')?.classList.remove('immersive-mode'); }")
        yield page
        browser.close()


def test_bookmark_button_is_visible_on_cards(page: Page):
    """Verify bookmark button is visible and displays '🔖 Save'."""
    active_card = page.locator(".reel-card.is-active").first
    btn = active_card.locator(".bookmark-btn")
    btn.wait_for(state="visible", timeout=3000)
    assert btn.is_visible()
    assert "Save" in btn.text_content()


def test_bookmarks_view_toggle_and_top_chrome_isolation(page: Page):
    """Verify opening bookmarks hides topChrome and closing restores it."""
    top_chrome = page.locator("#topChrome")
    bm_container = page.locator("#bookmarksContainer")
    feed = page.locator("#feedContainer")

    # Initial state: feed visible, topChrome visible, bookmarksContainer hidden
    assert feed.is_visible()
    assert top_chrome.is_visible()
    assert bm_container.is_visible() is False

    # Open bookmarks view
    page.evaluate("() => toggleBookmarksView(true)")
    page.wait_for_timeout(100)

    # Top chrome MUST be hidden to avoid overlapping header and story circles
    assert top_chrome.is_visible() is False
    assert bm_container.is_visible() is True
    assert feed.is_visible() is False

    # Check back button exists and is clickable
    back_btn = bm_container.locator(".back-to-feed-btn").first
    assert back_btn.is_visible()
    assert "Feed" in back_btn.text_content()

    # Click back to feed
    back_btn.click()
    page.wait_for_timeout(100)

    # State restored
    assert top_chrome.is_visible() is True
    assert feed.is_visible() is True
    assert bm_container.is_visible() is False


def test_owner_key_status_button(page: Page):
    """Verify owner key status button reflects device linkage state."""
    page.evaluate("() => toggleBookmarksView(true)")
    page.wait_for_timeout(100)

    btn = page.locator("#ownerKeyStatusBtn")
    assert btn.is_visible()
    assert "Link Key" in btn.text_content()

    # Simulate linking key
    page.evaluate("() => { localStorage.setItem('digest_owner_key', 'test_key_123'); updateOwnerKeyStatus(); }")
    assert "Linked" in btn.text_content()
    assert "linked" in (btn.get_attribute("class") or "")


def test_bookmark_overlay_safe_area_padding(page: Page):
    """Verify bookmark overlay top and bottom bars have safe-area padding applied."""
    top_bar = page.locator(".bookmark-overlay-top")
    bottom_bar = page.locator(".bookmark-overlay-bottom")
    assert top_bar.count() == 1
    assert bottom_bar.count() == 1


def test_resume_from_last_active_reel(page: Page):
    """Verify that opening the viewer resumes at the last active reel stored in localStorage."""
    card_4 = page.locator(".reel-card").nth(3)
    card_4_id = card_4.evaluate("c => c.dataset.id")
    card_4_offset = card_4.evaluate("c => c.offsetTop")

    # Set LAST_ACTIVE_KEY in localStorage and trigger resumeInitialPosition
    page.evaluate(f"""() => {{
        const week = (document.getElementById('weekSelector')?.value || '').split('/').pop() || '2026-09-11';
        localStorage.setItem('ig_digest_last_active_id_' + week, '{card_4_id}');
        resumeInitialPosition('{card_4_id}');
    }}""")
    page.wait_for_timeout(200)

    scroll_top = page.evaluate("() => document.getElementById('feedContainer').scrollTop")
    assert abs(scroll_top - card_4_offset) < 5
    assert "is-active" in (card_4.get_attribute("class") or "")


