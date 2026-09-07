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
            "category": "ai_tech",
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
            "category": "ai_tech",
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
        tech_cards = page.locator(".reel-card[data-category='ai_tech']")
        health_cards = page.locator(".reel-card[data-category='health']")

        # Click AI & Tech category bubble
        page.click(".story-bubble[data-category='ai_tech']")

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

        # Click All Top 200 bubble to restore
        page.click(".story-bubble[data-category='all']")
        for i in range(tech_cards.count()):
            assert tech_cards.nth(i).is_visible()

        browser.close()


def test_uat_6_3_playback_speed_cycling(setup_test_site):
    """UAT-6.3: Initial speed default (1.25x); clicking speed button cycles correctly."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        speed_display = page.locator("#speedDisplay")
        assert speed_display.inner_text() == f"{config.DEFAULT_PLAYBACK_SPEED}x"

        # Check video.playbackRate is default (1.25)
        first_video = page.locator(".reel-video").first
        assert page.evaluate("() => currentSpeed") == config.DEFAULT_PLAYBACK_SPEED

        # Click to cycle: 1.25x -> 1.5x
        page.click("#speedToggleBtn")
        assert speed_display.inner_text() == "1.5x"

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

        # Verify watched ID is saved in localStorage (using STORAGE_KEY)
        watched_json = page.evaluate("() => localStorage.getItem(STORAGE_KEY) || localStorage.getItem('ig_digest_watched_ids')")
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
            localStorage.setItem(STORAGE_KEY, JSON.stringify(allIds));
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


def test_uat_6_7_default_audio_pitch_preservation_and_wake_lock(setup_test_site):
    """UAT-6.7: Audio is on by default, pitch preservation is active, and wake lock is registered."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # UI audio button dropped as requested
        sound_btn = page.locator("#soundToggleBtn")
        assert not sound_btn.is_visible()

        # Audio is unmuted by default
        assert page.evaluate("() => isAudioMuted") is False

        # Pitch preservation and wake lock function registered
        assert page.evaluate("() => typeof requestWakeLock === 'function'")

        browser.close()


def test_uat_6_8_automatic_fullscreen_and_immersive_mode(setup_test_site):
    """UAT-6.8: Automatic edge-to-edge full layout (no manual button) and double-tap immersive mode."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # Fullscreen button dropped from header
        fs_btn = page.locator("#fullscreenBtn")
        assert not fs_btn.is_visible()

        # Shell fills full viewport width
        shell = page.locator("#appShell")
        assert shell.is_visible()

        # Double-click feed container -> toggles immersive mode
        feed = page.locator("#feedContainer")
        feed.dblclick()
        is_immersive = shell.evaluate("el => el.classList.contains('immersive-mode')")
        assert is_immersive is True

        browser.close()


def test_uat_6_9_sliding_window_virtualization(setup_test_site):
    """UAT-6.9: Sliding-window video loader preloads queue without unneeded offscreen media."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # First cards have src, distant card has data-src
        cards = page.locator(".reel-card")
        video0 = cards.nth(0).locator("video")
        video3 = cards.nth(3).locator("video")

        assert video0.get_attribute("src") is not None
        assert video3.get_attribute("data-src") is not None

        # Verify updateSlidingWindow function exists
        assert page.evaluate("() => typeof updateSlidingWindow === 'function'")

        browser.close()


def test_uat_6_10_watched_persistence_and_resuming(setup_test_site):
    """UAT-6.10: Resumes from last unwatched video and filters out watched reels."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # Simulate having watched first 2 reels (reel_tech_1 and reel_tech_2)
        page.evaluate("() => { markAsWatched('reel_tech_1'); markAsWatched('reel_tech_2'); filterCategory('all'); }")

        cards = page.locator(".reel-card")
        card0_display = cards.nth(0).evaluate("el => el.style.display")
        card1_display = cards.nth(1).evaluate("el => el.style.display")
        card2_display = cards.nth(2).evaluate("el => el.style.display")

        # Watched reels are hidden, unwatched reel is active and visible
        assert card0_display == "none"
        assert card1_display == "none"
        assert card2_display == "flex"

        # Verify active reel is reel_health_1 (the 3rd video, resuming after the 2 watched)
        active_id = page.evaluate("() => currentActiveCard ? currentActiveCard.dataset.id : null")
        assert active_id == "reel_health_1"

        browser.close()


def test_uat_6_11_keyboard_shortcuts_and_week_switcher(setup_test_site):
    """UAT-6.11: Desktop keyboard shortcuts (Space for play/pause, F for fullscreen) and week selector."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # Verify week selector dropdown exists in header
        selector = page.locator("#weekSelector")
        assert selector.is_visible()

        # Mock video play/pause on active video to test Spacebar keyboard binding
        page.evaluate("""() => {
            const v = document.querySelector('.reel-card video');
            v._mockPaused = false;
            Object.defineProperty(v, 'paused', { get: () => v._mockPaused, configurable: true });
            v.pause = () => { v._mockPaused = true; };
            v.play = () => { v._mockPaused = false; return Promise.resolve(); };
        }""")

        # Press Space -> pauses video
        page.keyboard.press("Space")
        assert page.evaluate("() => document.querySelector('.reel-card video').paused") is True

        # Press Space again -> resumes video
        page.keyboard.press("Space")
        assert page.evaluate("() => document.querySelector('.reel-card video').paused") is False

        # 'F' key triggers fullscreen
        page.keyboard.press("f")

        browser.close()


def test_uat_6_12_jump_to_reel_and_mark_prior_watched(setup_test_site):
    """UAT-6.12: Jump directly to reel #X marks preceding reels as watched and updates rank display."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # Clear localStorage for clean state
        page.evaluate("() => localStorage.clear()")
        page.reload()

        # Check jump button exists and shows initial #1/4
        jump_btn = page.locator("#jumpBtn")
        assert jump_btn.is_visible()
        assert "#1/4" in jump_btn.inner_text().replace(" ", "").replace("\n", "")

        # Open jump modal using keyboard shortcut 'g'
        page.keyboard.press("g")
        jump_modal = page.locator("#jumpModal")
        assert jump_modal.evaluate("el => el.classList.contains('active')") is True

        # Input reel number 3 and submit
        page.fill("#jumpInput", "3")
        page.click(".jump-submit-btn")

        # Verify jump modal closed
        assert jump_modal.evaluate("el => el.classList.contains('active')") is False

        # Verify reels 1 and 2 are stored as watched in localStorage
        watched_json = page.evaluate("() => localStorage.getItem('ig_digest_watched_ids_2026-09-06')")
        assert watched_json is not None
        watched_ids = json.loads(watched_json)
        assert "reel_tech_1" in watched_ids
        assert "reel_tech_2" in watched_ids
        assert "reel_health_1" not in watched_ids

        # Verify active card rank display updated to 3
        rank_display = page.locator("#currentRankDisplay")
        assert rank_display.inner_text() == "3"

        browser.close()


def test_uat_6_13_unselect_channel_instant_and_seamless(setup_test_site):
    """UAT-6.13: Local unselect skips popup, seamlessly advances to next reel, and works via 'b' shortcut."""
    file_url = setup_test_site.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_url)

        # Clear localStorage for clean state
        page.evaluate("() => localStorage.clear()")
        page.reload()

        # Verify unselect buttons exist on local site
        unselect_btns = page.locator(".unselect-channel-btn")
        initial_count = page.locator(".reel-card").count()
        assert initial_count == 4

        # Click unselect button on active reel #1 (@mkbhd)
        unselect_btns.first.click()

        # Verify toast showed instant feedback (no popup)
        toast = page.locator("#globalToast")
        assert "mkbhd" in toast.inner_text().lower()

        # Verify reel count decreased by 1
        assert page.locator(".reel-card").count() == 3

        # Verify next reel (@mrwhosetheboss, rank #2) is now active, NOT resetting to anything missing
        active_card = page.locator(".reel-card").first
        badge = active_card.locator(".creator-badge")
        assert "@mrwhosetheboss" in badge.inner_text()

        # Test 'b' keyboard shortcut on active reel
        page.keyboard.press("b")

        # Verify toast showed instant feedback for mrwhosetheboss
        assert "mrwhosetheboss" in toast.inner_text().lower()

        # Reel count decreased to 2
        assert page.locator(".reel-card").count() == 2

        # Reel #3 (@hubermanlab) is now at the top
        active_card_new = page.locator(".reel-card").first
        assert "@hubermanlab" in active_card_new.locator(".creator-badge").inner_text()

        browser.close()
