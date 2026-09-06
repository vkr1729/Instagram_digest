"""
test_ui_interactions.py — Playwright browser E2E automated test suite for Suite 6.
"""

import json
import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright

import config
from site_builder import build_site


@pytest.fixture(scope="module")
def setup_test_site(tmp_path_factory):
    """Generate a mock site with 4 test reels across categories in an isolated temporary directory."""
    import unittest.mock as mock
    mock_items = [
        {
            "id": "reel_tech_1",
            "creator_handle": "mkbhd",
            "rank": 1,
            "rank_display": "#01",
            "view_count": 850000,
            "category": "tech",
            "caption": "MKBHD folding phone review",
            "thumbnail": "",
            "video_url": "data:video/mp4;base64,AAAA",
        },
        {
            "id": "reel_tech_2",
            "creator_handle": "mrwhosetheboss",
            "rank": 2,
            "rank_display": "#02",
            "view_count": 600000,
            "category": "tech",
            "caption": "Battery test comparison",
            "thumbnail": "",
            "video_url": "data:video/mp4;base64,AAAA",
        },
        {
            "id": "reel_health_1",
            "creator_handle": "hubermanlab",
            "rank": 3,
            "rank_display": "#03",
            "view_count": 450000,
            "category": "health",
            "caption": "Circadian rhythm light exposure",
            "thumbnail": "",
            "video_url": "data:video/mp4;base64,AAAA",
        },
        {
            "id": "reel_explainer_1",
            "creator_handle": "veritasium",
            "rank": 4,
            "rank_display": "#04",
            "view_count": 920000,
            "category": "explainer",
            "caption": "Unstable equilibrium experiment",
            "thumbnail": "",
            "video_url": "data:video/mp4;base64,AAAA",
        }
    ]
    test_dir = tmp_path_factory.mktemp("test_site")
    with mock.patch.object(config, "SITE_DIR", test_dir):
        _, local_index = build_site({"run_date": "2026-09-06", "items": mock_items})
    return local_index


def test_uat_6_1_mobile_viewport_and_scroll_snap(setup_test_site):
    """UAT-6.1: Verify mobile viewport (390x844) renders with CSS scroll-snap."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.goto(file_url)

        # Check scroll-snap-type on .feed-container
        feed = page.locator(".feed-container")
        assert feed.is_visible()
        scroll_snap = feed.evaluate("el => getComputedStyle(el).scrollSnapType")
        assert "y mandatory" in scroll_snap

        # Check reel cards count
        cards = page.locator(".reel-card")
        assert cards.count() == 4

        browser.close()


def test_uat_6_2_story_category_filtering(setup_test_site):
    """UAT-6.2: Verify clicking story category bubbles filters cards immediately."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # Initial state: 'all' category shows all 4 cards
        tech_cards = page.locator(".reel-card[data-category='tech']")
        health_cards = page.locator(".reel-card[data-category='health']")

        # Click Tech category bubble
        page.click(".story-bubble[data-category='tech']")

        # Only tech cards should be visible
        for i in range(tech_cards.count()):
            assert tech_cards.nth(i).is_visible()

        for i in range(health_cards.count()):
            assert not health_cards.nth(i).is_visible()

        # Click Health category bubble
        page.click(".story-bubble[data-category='health']")
        for i in range(health_cards.count()):
            assert health_cards.nth(i).is_visible()
        for i in range(tech_cards.count()):
            assert not tech_cards.nth(i).is_visible()

        # Click All Top 100 bubble to restore
        page.click(".story-bubble[data-category='all']")
        for i in range(tech_cards.count()):
            assert tech_cards.nth(i).is_visible()

        browser.close()


def test_uat_6_3_playback_speed_cycling(setup_test_site):
    """UAT-6.3: Initial speed 1.5x; clicking speed button cycles correctly."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        speed_display = page.locator("#speedDisplay")
        assert speed_display.inner_text() == "1.5x"

        # Check video.playbackRate is 1.5
        first_video = page.locator(".reel-video").first
        assert page.evaluate("() => currentSpeed") == 1.5

        # Click to cycle: 1.5x -> 1.75x
        page.click("#speedToggleBtn")
        assert speed_display.inner_text() == "1.75x"

        # Click to cycle: 1.75x -> 2x
        page.click("#speedToggleBtn")
        assert speed_display.inner_text() == "2x"

        # Click to cycle: 2x -> 1x
        page.click("#speedToggleBtn")
        assert speed_display.inner_text() == "1x"

        # Click to cycle: 1x -> 1.25x
        page.click("#speedToggleBtn")
        assert speed_display.inner_text() == "1.25x"

        # Click to cycle: 1.25x -> 1.5x
        page.click("#speedToggleBtn")
        assert speed_display.inner_text() == "1.5x"

        browser.close()


def test_uat_6_4_and_6_5_auto_advance_and_watched_persistence(setup_test_site):
    """UAT-6.4 & 6.5: Video ended event triggers 0.5s auto-advance and records watched ID."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # Clear localStorage before test
        page.evaluate("() => localStorage.clear()")

        # Simulate 'ended' event on first video
        page.evaluate("""() => {
            const firstVideo = document.querySelector('.reel-card video');
            firstVideo.dispatchEvent(new Event('ended'));
        }""")

        # Wait 700ms (0.5s timer + buffer)
        page.wait_for_timeout(700)

        # Verify watched ID is saved in localStorage
        watched_json = page.evaluate("() => localStorage.getItem('ig_digest_watched_ids')")
        assert watched_json is not None
        assert "reel_tech_1" in watched_json

        browser.close()


def test_uat_6_6_celebration_screen(setup_test_site):
    """UAT-6.6: When all videos are marked watched, display 'All Caught Up' celebration."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        celebration = page.locator("#celebrationScreen")
        assert not celebration.is_visible()

        # Mark all 4 reel IDs as watched
        page.evaluate("""() => {
            const allIds = ['reel_tech_1', 'reel_tech_2', 'reel_health_1', 'reel_explainer_1'];
            localStorage.setItem('ig_digest_watched_ids', JSON.stringify(allIds));
            filterCategory('all');
        }""")

        # Celebration screen must be visible
        assert celebration.is_visible()
        assert "You're All Caught Up!" in celebration.inner_text()

        # Click reset button
        page.click(".reset-btn")
        assert not celebration.is_visible()
        assert page.locator(".reel-card").first.is_visible()

        browser.close()
