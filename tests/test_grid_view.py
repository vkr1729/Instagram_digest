"""
test_grid_view.py — Automated tests for the 300-Reel Digest Visual Grid View.
Tests button placement, toggle isolation, search, category chips, watched indicators,
and direct navigation back to the vertical feed.
"""

from pathlib import Path
import os
import pytest
from playwright.sync_api import Page, sync_playwright

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
        page.evaluate("() => { const v = document.querySelector('.reel-card .reel-video'); if (v) v.pause(); }")
        yield page
        browser.close()


def test_grid_toggle_button_present_in_header(page: Page):
    """Verify grid view toggle button (⊞) exists in header controls."""
    btn = page.locator("#gridToggleBtn")
    assert btn.is_visible()
    assert "⊞" in btn.text_content()
    # Confirm it sits inside header-controls
    parent = page.locator(".header-controls")
    assert parent.locator("#gridToggleBtn").count() == 1


def test_grid_view_toggle_and_top_chrome_isolation(page: Page):
    """Verify opening grid view hides topChrome and feedContainer; closing restores them."""
    top_chrome = page.locator("#topChrome")
    feed = page.locator("#feedContainer")
    grid_container = page.locator("#gridContainer")

    # Initial state: feed and chrome visible, grid hidden
    assert feed.is_visible()
    assert top_chrome.is_visible()
    assert grid_container.is_visible() is False

    # Open grid view via toggle button
    page.locator("#gridToggleBtn").click()
    page.wait_for_timeout(100)

    # Grid must be visible; feed and chrome must be hidden to prevent collision
    assert grid_container.is_visible() is True
    assert top_chrome.is_visible() is False
    assert feed.is_visible() is False

    # Check back button exists and restores feed
    back_btn = grid_container.locator(".back-to-feed-btn").first
    assert back_btn.is_visible()
    assert "Feed" in back_btn.text_content()

    back_btn.click()
    page.wait_for_timeout(100)

    assert grid_container.is_visible() is False
    assert top_chrome.is_visible() is True
    assert feed.is_visible() is True


def test_grid_view_renders_cards_with_rank_and_handle(page: Page):
    """Verify grid view renders 3-column cards matching feed item count."""
    feed_card_count = page.locator("#feedContainer .reel-card").count()
    assert feed_card_count > 0

    page.locator("#gridToggleBtn").click()
    page.wait_for_timeout(100)

    grid_cards = page.locator("#digestGrid .digest-grid-card")
    assert grid_cards.count() == feed_card_count

    first_card = grid_cards.first
    assert first_card.locator(".grid-card-rank").is_visible()
    assert first_card.locator(".grid-card-handle").is_visible()
    assert "@" in first_card.locator(".grid-card-handle").text_content()


def test_grid_view_search_filtering(page: Page):
    """Verify real-time search filters grid cards correctly."""
    page.locator("#gridToggleBtn").click()
    page.wait_for_timeout(100)

    total_cards = page.locator("#digestGrid .digest-grid-card").count()

    # Get handle of first card
    first_handle = page.locator("#digestGrid .grid-card-handle").first.text_content().replace("@", "").strip()

    search_input = page.locator("#digestGridSearchInput")
    search_input.fill(first_handle)
    page.wait_for_timeout(200)

    filtered_cards = page.locator("#digestGrid .digest-grid-card")
    assert filtered_cards.count() >= 1
    assert filtered_cards.count() < total_cards

    # Clear search restores all cards
    search_input.fill("")
    page.wait_for_timeout(200)
    assert page.locator("#digestGrid .digest-grid-card").count() == total_cards


def test_grid_view_category_filtering(page: Page):
    """Verify category chips filter grid cards."""
    page.locator("#gridToggleBtn").click()
    page.wait_for_timeout(100)

    total_cards = page.locator("#digestGrid .digest-grid-card").count()

    ent_chip = page.locator('#gridCategoryChips button[data-cat="entertainment"]')
    if ent_chip.count() > 0:
        ent_chip.click()
        page.wait_for_timeout(100)
        assert "active" in ent_chip.get_attribute("class")

        # Cards should be filtered
        cat_cards = page.locator("#digestGrid .digest-grid-card").count()
        assert cat_cards <= total_cards

        # Clicking All resets
        all_chip = page.locator('#gridCategoryChips button[data-cat="all"]')
        all_chip.click()
        page.wait_for_timeout(100)
        assert page.locator("#digestGrid .digest-grid-card").count() == total_cards


def test_select_card_from_grid_navigates_to_feed(page: Page):
    """Verify tapping a card in grid closes grid and navigates feed directly to that reel."""
    page.locator("#gridToggleBtn").click()
    page.wait_for_timeout(100)

    # Click the 3rd card in the grid
    target_card = page.locator("#digestGrid .digest-grid-card").nth(2)
    target_id = target_card.get_attribute("data-id")
    target_card.click()
    page.wait_for_timeout(200)

    # Grid should be closed and feed visible
    assert page.locator("#gridContainer").is_visible() is False
    assert page.locator("#feedContainer").is_visible() is True

    # Active reel card should match the chosen ID
    active_feed_card = page.locator("#feedContainer .reel-card.is-active")
    if active_feed_card.count() > 0:
        assert active_feed_card.get_attribute("data-id") == target_id
    else:
        # Fallback check via JS currentActiveCard
        active_id = page.evaluate("() => currentActiveCard ? currentActiveCard.dataset.id : null")
        assert active_id == target_id


def test_watched_state_badges_in_grid(page: Page):
    """Verify reels marked as watched display the checkmark badge in the grid."""
    # Mark first reel as watched
    first_id = page.locator("#feedContainer .reel-card").first.get_attribute("data-id")
    page.evaluate(f"() => markAsWatched('{first_id}')")

    page.locator("#gridToggleBtn").click()
    page.wait_for_timeout(100)

    first_grid_card = page.locator(f'#digestGrid .digest-grid-card[data-id="{first_id}"]')
    assert "is-watched" in first_grid_card.get_attribute("class")
    badge = first_grid_card.locator(".grid-card-watched-badge")
    assert badge.is_visible()
    assert "✓" in badge.text_content()

    # Counter in top bar
    counter = page.locator("#gridWatchedCounter")
    assert "1/" in counter.text_content()


def test_grid_and_bookmarks_mutual_exclusion(page: Page):
    """Verify opening bookmarks closes grid and vice versa."""
    # Open grid
    page.locator("#gridToggleBtn").click()
    page.wait_for_timeout(100)
    assert page.locator("#gridContainer").is_visible() is True

    # Open bookmarks via JS
    page.evaluate("() => toggleBookmarksView(true)")
    page.wait_for_timeout(100)
    assert page.locator("#bookmarksContainer").is_visible() is True
    assert page.locator("#gridContainer").is_visible() is False

    # Open grid via JS
    page.evaluate("() => toggleGridView(true)")
    page.wait_for_timeout(100)
    assert page.locator("#gridContainer").is_visible() is True
    assert page.locator("#bookmarksContainer").is_visible() is False


def test_top_header_does_not_overflow_viewport(page: Page):
    """Verify that on iPhone 15 Pro (393px width), the top header controls do not overflow or protrude."""
    viewport = page.viewport_size
    assert viewport is not None
    vp_width = viewport["width"]

    top_header = page.locator("#topHeader")
    header_box = top_header.bounding_box()
    assert header_box is not None
    assert header_box["x"] >= 0
    assert header_box["x"] + header_box["width"] <= vp_width + 1

    # Check that the rightmost button (bookmarks-chip) is fully inside viewport with margin
    bm_chip = page.locator(".header-controls .bookmarks-chip")
    chip_box = bm_chip.bounding_box()
    assert chip_box is not None
    assert chip_box["x"] + chip_box["width"] <= vp_width - 4


def test_top_header_on_small_mobile_screen_does_not_overflow(built_site):
    """Verify that on a narrow 360px Android screen, top header does not overflow."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 360, "height": 780}, is_mobile=True)
        page = context.new_page()
        pin = os.getenv("VIEWING_PIN", "").strip()
        url = f"file://{built_site.resolve()}"
        pin_url = f"{url}?pin={pin}" if pin else url
        page.goto(url)
        page.evaluate("() => localStorage.clear()")
        page.goto(pin_url)
        page.wait_for_selector(".reel-card", state="visible")

        bm_chip = page.locator(".header-controls .bookmarks-chip")
        chip_box = bm_chip.bounding_box()
        assert chip_box is not None
        assert chip_box["x"] + chip_box["width"] <= 360 - 2
        browser.close()

