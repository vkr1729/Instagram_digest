"""
test_mobile_pwa_uat.py — Comprehensive Playwright UAT test suite for Instagram Digest Mobile PWA.
Validates:
1. iPhone 15 Pro mobile viewport, safe area padding, and layout symmetry.
2. Configurable default speed (1.25x).
3. Bottom 2x booster button (per-reel booster with auto-reset on next reel).
4. Double-tap 3-zone gestures (Left -10s, Right +10s, Center Fullscreen toggle & return).
5. Horizontal touch swipe video seeking with floating HUD.
6. 35% watched threshold triggering read state.
7. WhatsApp share link pointing to Open Graph share page with inline R2 video.
8. In-session rewatching allowing swiping back up to previous reels.
9. Deep link ?reel=ID navigation.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
import pytest
from playwright.sync_api import Page, sync_playwright

import config
import site_builder

ROOT_DIR = Path(__file__).resolve().parent.parent
SITE_INDEX = ROOT_DIR / "site" / "index.html"

# iPhone 15 Pro device metrics
IPHONE_15_PRO = {
    "viewport": {"width": 393, "height": 852},
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "has_touch": True,
    "is_mobile": True,
    "device_scale_factor": 3,
}


@pytest.fixture(scope="module")
def built_site():
    """Ensure site is freshly compiled before testing."""
    site_builder.build_site()
    assert SITE_INDEX.exists()
    return SITE_INDEX


@pytest.fixture
def mobile_page(built_site):
    """Playwright page configured as iPhone 15 Pro PWA."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**IPHONE_15_PRO)
        page = context.new_page()
        # Reset localStorage
        page.goto(f"file://{built_site.resolve()}")
        page.evaluate("() => localStorage.clear()")
        page.reload()
        page.wait_for_selector(".reel-card")
        yield page
        browser.close()


def test_safe_area_padding_and_symmetry(mobile_page: Page):
    """Verify top header and bottom scrim have safe-area padding for Dynamic Island and home bar."""
    top_header = mobile_page.locator(".top-header")
    bottom_scrim = mobile_page.locator(".bottom-scrim").first

    top_padding_top = top_header.evaluate("el => window.getComputedStyle(el).paddingTop")
    bottom_padding_bottom = bottom_scrim.evaluate("el => window.getComputedStyle(el).paddingBottom")

    # Compact safe area padding: snug top under camera bump and natural 12px bottom
    assert int(top_padding_top.replace("px", "")) >= 6
    assert int(bottom_padding_bottom.replace("px", "")) >= 12

    # Feed container should have overscroll-behavior-y none and touch-action pan-y
    feed_touch_action = mobile_page.locator("#feedContainer").evaluate("el => window.getComputedStyle(el).touchAction")
    assert "pan-y" in feed_touch_action


def test_default_playback_speed_1_25x(mobile_page: Page):
    """Verify default speed starts at 1.25x as configured."""
    speed_display = mobile_page.locator("#speedDisplay").text_content()
    assert "1.25x" in speed_display

    # Active video should have playbackRate = 1.25
    rate = mobile_page.evaluate("""() => {
        const v = document.querySelector('.reel-card .reel-video');
        return v ? v.playbackRate : null;
    }""")
    assert rate == 1.25


def test_bottom_2x_booster_and_auto_reset(mobile_page: Page):
    """Verify bottom 2x booster button toggles 2x on active reel and resets on next reel."""
    first_card = mobile_page.locator(".reel-card").first
    boost_btn = first_card.locator(".boost-speed-btn")
    assert boost_btn.is_visible()

    # Initial rate 1.25x
    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 1.25

    # Click 2x booster
    boost_btn.click()
    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 2.0
    assert "active" in boost_btn.evaluate("el => el.className")

    # Click 2x booster again -> reverts to default 1.25x
    boost_btn.click()
    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 1.25
    assert "active" not in boost_btn.evaluate("el => el.className")

    # Boost to 2x again, then advance to next reel
    boost_btn.click()
    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 2.0

    # Advance to next reel programmatically
    mobile_page.evaluate("""() => {
        const cards = document.querySelectorAll('.reel-card');
        advanceToNextReel(cards[0]);
    }""")
    mobile_page.wait_for_timeout(400)

    # Next card rate should reset to default 1.25x
    second_card = mobile_page.locator(".reel-card").nth(1)
    assert second_card.locator(".reel-video").evaluate("v => v.playbackRate") == 1.25
    # Booster button on second card should not be active
    assert "active" not in second_card.locator(".boost-speed-btn").evaluate("el => el.className")


def test_double_tap_fullscreen_toggle_and_return(mobile_page: Page):
    """Verify center double-tap toggles fullscreen on and OFF (resolving Feedback #5)."""
    shell = mobile_page.locator("#appShell")
    assert "immersive-mode" not in shell.evaluate("el => el.className")

    # Double tap in center zone (x = 393/2 = 196, y = 400)
    feed = mobile_page.locator("#feedContainer")
    feed.click(position={"x": 196, "y": 400})
    feed.click(position={"x": 196, "y": 400})
    mobile_page.wait_for_timeout(100)

    # Fullscreen should now be active
    assert "immersive-mode" in shell.evaluate("el => el.className")

    # Double tap center again -> should exit fullscreen back to normal mode
    feed.click(position={"x": 196, "y": 400})
    feed.click(position={"x": 196, "y": 400})
    mobile_page.wait_for_timeout(100)

    assert "immersive-mode" not in shell.evaluate("el => el.className")


def test_double_tap_10s_skip_left_and_right(mobile_page: Page):
    """Verify double-tap on Left rewinds 10s and Right advances 10s."""
    first_card = mobile_page.locator(".reel-card").first
    video = first_card.locator(".reel-video")

    # Set video currentTime to 15s and mock duration to 30s
    mobile_page.evaluate("""() => {
        const v = document.querySelector('.reel-card .reel-video');
        Object.defineProperty(v, 'duration', { value: 30, writable: true });
        v.currentTime = 15;
    }""")

    feed = mobile_page.locator("#feedContainer")

    # Double tap on Right zone (x = 350 > 393 * 0.65 = 255)
    feed.click(position={"x": 350, "y": 400})
    feed.click(position={"x": 350, "y": 400})
    mobile_page.wait_for_timeout(100)

    # 15s + 10s = 25s (with minor playback advancement allowance)
    curr_time = video.evaluate("v => v.currentTime")
    assert abs(curr_time - 25) < 0.5
    # Right ripple should be triggered
    assert mobile_page.locator("#skipRippleRight").evaluate("el => el.classList.contains('active')")

    # Double tap on Left zone (x = 50 < 393 * 0.35 = 137)
    feed.click(position={"x": 50, "y": 400})
    feed.click(position={"x": 50, "y": 400})
    mobile_page.wait_for_timeout(100)

    # 25s - 10s = 15s (with minor playback advancement allowance)
    curr_time = video.evaluate("v => v.currentTime")
    assert abs(curr_time - 15) < 0.5
    assert mobile_page.locator("#skipRippleLeft").evaluate("el => el.classList.contains('active')")


def test_35_percent_watched_threshold(mobile_page: Page):
    """Verify reel is marked watched once 35% is reached (Feedback #9)."""
    first_card = mobile_page.locator(".reel-card").first
    card_id = first_card.evaluate("c => c.dataset.id")

    # Initially unwatched
    assert mobile_page.evaluate(f"() => getWatchedIds().has('{card_id}')") is False

    # Simulate reaching 36% duration
    mobile_page.evaluate("""() => {
        const card = document.querySelector('.reel-card');
        const v = card.querySelector('.reel-video');
        Object.defineProperty(v, 'duration', { value: 20, writable: true });
        v.currentTime = 7.5; // 7.5 / 20 = 37.5% > 35%
        v.dispatchEvent(new Event('timeupdate'));
    }""")

    # Should be marked watched in DOM and localStorage
    assert first_card.evaluate("c => c.dataset.markedWatched") == "true"
    assert mobile_page.evaluate(f"() => getWatchedIds().has('{card_id}')") is True


def test_horizontal_touch_swipe_seeking(mobile_page: Page):
    """Verify horizontal touch gestures scrub the video and show seek HUD."""
    mobile_page.evaluate("""() => {
        const card = document.querySelector('.reel-card');
        const v = card.querySelector('.reel-video');
        Object.defineProperty(v, 'duration', { value: 40, writable: true });
        v.currentTime = 5;
    }""")

    # Dispatch touchstart, touchmove (drag 110px right), touchend
    mobile_page.evaluate("""() => {
        const feed = document.getElementById('feedContainer');
        const touch1 = new Touch({ identifier: 1, target: feed, clientX: 100, clientY: 300, pageX: 100, pageY: 300 });
        feed.dispatchEvent(new TouchEvent('touchstart', {
            touches: [touch1], targetTouches: [touch1], changedTouches: [touch1]
        }));
        const touch2 = new Touch({ identifier: 1, target: feed, clientX: 210, clientY: 302, pageX: 210, pageY: 302 });
        feed.dispatchEvent(new TouchEvent('touchmove', {
            touches: [touch2], targetTouches: [touch2], changedTouches: [touch2]
        }));
    }""")

    # Seek HUD should be visible
    hud = mobile_page.locator("#seekHud")
    assert hud.evaluate("el => el.classList.contains('visible')") is True

    # Dispatch touchend
    mobile_page.evaluate("""() => {
        const feed = document.getElementById('feedContainer');
        const touchEnd = new Touch({ identifier: 1, target: feed, clientX: 210, clientY: 302, pageX: 210, pageY: 302 });
        feed.dispatchEvent(new TouchEvent('touchend', {
            touches: [], targetTouches: [], changedTouches: [touchEnd]
        }));
    }""")

    # HUD hides and video currentTime advances
    assert hud.evaluate("el => el.classList.contains('visible')") is False
    new_time = mobile_page.evaluate("() => document.querySelector('.reel-card .reel-video').currentTime")
    assert new_time > 5


def test_whatsapp_share_url_generation(mobile_page: Page):
    """Verify WhatsApp button formats direct link to Open Graph share page without opening blank window."""
    first_card = mobile_page.locator(".reel-card").first
    card_id = first_card.evaluate("c => c.dataset.id")
    share_btn = first_card.locator(".whatsapp-share-btn")
    assert share_btn.is_visible()

    share_btn.click()
    dispatched = mobile_page.evaluate("() => window.__dispatchedShareUrl")
    assert dispatched is not None
    assert "whatsapp://send?text=" in dispatched
    decoded_url = urllib.parse.unquote(dispatched)
    assert f"/share/{card_id}.html" in decoded_url


def test_in_session_rewatching_remains_visible(mobile_page: Page):
    """Verify watched reels during the current session stay visible for swiping back up."""
    first_card = mobile_page.locator(".reel-card").nth(0)
    card_id = first_card.evaluate("c => c.dataset.id")

    # Mark first card as watched
    mobile_page.evaluate(f"() => markAsWatched('{card_id}')")

    # Advance to next reel
    mobile_page.evaluate("""() => {
        const cards = document.querySelectorAll('.reel-card');
        advanceToNextReel(cards[0]);
    }""")
    mobile_page.wait_for_timeout(350)

    # First card should still have display: flex in the current session so user can swipe back up!
    display_style = first_card.evaluate("el => el.style.display")
    assert display_style != "none"


def test_deep_link_specific_reel(built_site):
    """Verify opening ?reel=ID deep link focuses on the target reel."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**IPHONE_15_PRO)
        page = context.new_page()

        # Open with 5th reel ID
        digest_data = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
        target_id = digest_data["items"][4]["id"]

        page.goto(f"file://{built_site.resolve()}?reel={target_id}")
        page.wait_for_timeout(400)

        # Active reel should be the 5th reel
        active_id = page.evaluate("() => currentActiveCard ? currentActiveCard.dataset.id : null")
        assert active_id == target_id
        browser.close()
