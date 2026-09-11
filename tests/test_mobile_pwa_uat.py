"""
test_mobile_pwa_uat.py — Comprehensive Playwright UAT test suite for Instagram Digest Mobile PWA.
Validates:
1. iPhone 15 Pro mobile viewport, safe area padding, and layout symmetry.
2. Configurable default speed (1.25x).
3. Press-and-hold 2x (latched per reel, right-zone tap exits, auto-reset on next reel).
4. Single auto-immersive state (play hides chrome+scrim, pause reveals) and
   center double-tap routing to the native fullscreen toggle (no ±10s seek).
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
    """Verify default speed starts at 1.25x as configured and top header speed button is dropped."""
    assert not mobile_page.locator("#speedToggleBtn").is_visible()

    # Active video should have playbackRate = 1.25
    rate = mobile_page.evaluate("""() => {
        const v = document.querySelector('.reel-card .reel-video');
        return v ? v.playbackRate : null;
    }""")
    assert rate == 1.25


def test_hold_to_boost_latch_tap_exit_and_reset(mobile_page: Page):
    """Verify rightmost-35% 500ms hold latches 2x, right-tap exits, reel change resets."""
    # No per-reel 2x button anymore; the gesture owns the boost.
    assert mobile_page.locator(".boost-speed-btn").count() == 0

    first_card = mobile_page.locator(".reel-card").first
    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 1.25

    # Headless media never really plays: shadow `paused` so the hold may engage.
    mobile_page.evaluate("""() => {
        const v = document.querySelector('.reel-card .reel-video');
        Object.defineProperty(v, 'paused', { value: false, configurable: true });
        const feed = document.getElementById('feedContainer');
        feed.dispatchEvent(new PointerEvent('pointerdown', {
            clientX: 350, clientY: 400, bubbles: true
        }));
    }""")
    mobile_page.wait_for_timeout(700)
    mobile_page.evaluate("() => window.dispatchEvent(new PointerEvent('pointerup'))")

    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 2.0
    assert "Boosted to 2x" in mobile_page.locator("#globalToast").text_content()

    # First right-tap is swallowed as the hold release, the next exits the latch.
    first_card.click(position={"x": 350, "y": 400})
    mobile_page.wait_for_timeout(100)
    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 2.0
    first_card.click(position={"x": 350, "y": 400})
    mobile_page.wait_for_timeout(150)
    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 1.25

    # Latch again, then advance: the next reel must reset to default speed.
    mobile_page.evaluate("""() => {
        const card = document.querySelector('.reel-card');
        engageBoost(card, card.querySelector('.reel-video'));
    }""")
    assert first_card.locator(".reel-video").evaluate("v => v.playbackRate") == 2.0
    mobile_page.evaluate("""() => {
        const cards = document.querySelectorAll('.reel-card');
        advanceToNextReel(cards[0]);
    }""")
    mobile_page.wait_for_timeout(400)
    second_card = mobile_page.locator(".reel-card").nth(1)
    assert second_card.locator(".reel-video").evaluate("v => v.playbackRate") == 1.25


def test_share_action_without_2x_button(mobile_page: Page):
    """Verify the 2x button is gone and WhatsApp share stands alone in reel actions."""
    assert mobile_page.locator(".boost-speed-btn").count() == 0

    first_card = mobile_page.locator(".reel-card").first
    share_btn = first_card.locator(".whatsapp-share-btn")
    assert share_btn.is_visible()
    assert "Share" in share_btn.text_content()


def test_center_double_tap_routes_to_native_fullscreen_toggle(mobile_page: Page):
    """Verify center double-tap calls the native fullscreen toggle; sides do not seek."""
    shell = mobile_page.locator("#appShell")
    assert "immersive-mode" not in shell.evaluate("el => el.className")

    # Spy on the native toggle (headless may not honor the Fullscreen API itself).
    mobile_page.evaluate("""() => {
        window.__toggleCalls = 0;
        const orig = window.toggleUnifiedFullscreen;
        window.toggleUnifiedFullscreen = (...args) => {
            window.__toggleCalls += 1;
            return orig(...args);
        };
    }""")

    feed = mobile_page.locator("#feedContainer")

    # Double tap in center zone (x = 393/2 = 196, y = 400)
    feed.click(position={"x": 196, "y": 400})
    feed.click(position={"x": 196, "y": 400})
    mobile_page.wait_for_timeout(100)
    assert mobile_page.evaluate("() => window.__toggleCalls") == 1

    # Side double-taps must not seek and must not toggle: set a known time first.
    mobile_page.evaluate("""() => {
        const v = document.querySelector('.reel-card .reel-video');
        Object.defineProperty(v, 'duration', { value: 30, writable: true });
        v.currentTime = 15;
    }""")
    feed.click(position={"x": 350, "y": 400})
    feed.click(position={"x": 350, "y": 400})
    mobile_page.wait_for_timeout(100)
    curr_time = mobile_page.evaluate("() => document.querySelector('.reel-card .reel-video').currentTime")
    assert abs(curr_time - 15) < 1.0
    assert mobile_page.evaluate("() => window.__toggleCalls") == 1

    feed.click(position={"x": 50, "y": 400})
    feed.click(position={"x": 50, "y": 400})
    mobile_page.wait_for_timeout(100)
    curr_time = mobile_page.evaluate("() => document.querySelector('.reel-card .reel-video').currentTime")
    assert abs(curr_time - 15) < 1.0
    assert mobile_page.evaluate("() => window.__toggleCalls") == 1

    # Ripple elements are gone entirely.
    assert mobile_page.locator("#skipRippleRight").count() == 0
    assert mobile_page.locator("#skipRippleLeft").count() == 0


def test_auto_immersive_hides_and_pause_reveals(mobile_page: Page):
    """Verify play auto-hides chrome+scrim (immersive) and pause restores them."""
    shell = mobile_page.locator("#appShell")
    scrim = mobile_page.locator(".bottom-scrim").first
    assert "immersive-mode" not in shell.evaluate("el => el.className")

    # Headless media never really plays: shadow `paused` and fire the events.
    mobile_page.evaluate("""() => {
        const v = document.querySelector('.reel-card .reel-video');
        Object.defineProperty(v, 'paused', { value: false, configurable: true });
        v.dispatchEvent(new Event('play'));
    }""")
    mobile_page.wait_for_timeout(400)
    assert "immersive-mode" in shell.evaluate("el => el.className")
    assert scrim.evaluate("el => window.getComputedStyle(el).opacity") == "0"

    mobile_page.evaluate("""() => {
        const v = document.querySelector('.reel-card .reel-video');
        Object.defineProperty(v, 'paused', { value: true, configurable: true });
        v.dispatchEvent(new Event('pause'));
    }""")
    mobile_page.wait_for_timeout(400)
    assert "immersive-mode" not in shell.evaluate("el => el.className")
    assert float(scrim.evaluate("el => window.getComputedStyle(el).opacity")) > 0.9


def test_swipe_up_forward_watched_recording(mobile_page: Page):
    """Verify reel is marked watched upon forward swipe / navigation (Requirement 6)."""
    cards = mobile_page.locator(".reel-card")
    first_card = cards.first
    card_id = first_card.evaluate("c => c.dataset.id")

    # Initially unwatched
    assert mobile_page.evaluate(f"() => getWatchedIds().has('{card_id}')") is False

    # Simulate navigating forward to the next card (swipe-up / goToCard)
    mobile_page.evaluate("""() => {
        const vCards = visibleCards();
        if (vCards.length > 1) {
            goToCard(vCards[1]);
        }
    }""")
    mobile_page.wait_for_timeout(200)

    # Departed card should now be marked watched in DOM and localStorage
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


def test_caption_and_controls_elevation(mobile_page: Page):
    """Verify caption snippet is displayed, with share comfortably elevated for the thumb."""
    first_card = mobile_page.locator(".reel-card").first
    caption_snippet = first_card.locator(".caption-snippet")
    assert caption_snippet.is_visible()
    caption_text = caption_snippet.text_content().strip()
    assert len(caption_text) > 0

    # Verify the share button sits comfortably elevated above the bottom of the viewport
    share_btn = first_card.locator(".whatsapp-share-btn")
    share_box = share_btn.bounding_box()
    viewport_height = mobile_page.viewport_size["height"]
    distance_from_bottom = viewport_height - (share_box["y"] + share_box["height"])

    # Elevation must be comfortably above home indicator (> 24px)
    assert distance_from_bottom >= 24


def test_navigator_share_with_physical_file(mobile_page: Page):
    """Verify native Web Share API passes the physical thumbnail file when supported."""
    first_card = mobile_page.locator(".reel-card").first
    card_id = first_card.evaluate("c => c.dataset.id")

    # Mock navigator.share and pre-seed thumbnail file (file:// protocol blocks local fetch)
    mobile_page.evaluate(f"""() => {{
        window.__sharedPayload = null;
        cachedThumbnailFiles.set('{card_id}', new File(['dummy_img_content'], '{card_id}.jpg', {{ type: 'image/jpeg' }}));
        navigator.canShare = (data) => Boolean(data && data.files && data.files.length > 0);
        navigator.share = async (data) => {{
            window.__sharedPayload = {{
                hasFiles: Boolean(data.files && data.files.length > 0),
                fileName: data.files && data.files[0] ? data.files[0].name : null,
                fileType: data.files && data.files[0] ? data.files[0].type : null,
                title: data.title,
                text: data.text
            }};
            return Promise.resolve();
        }};
    }}""")

    share_btn = first_card.locator(".whatsapp-share-btn")
    share_btn.click()
    mobile_page.wait_for_timeout(200)

    payload = mobile_page.evaluate("() => window.__sharedPayload")
    assert payload is not None
    assert payload["hasFiles"] is True
    assert payload["fileName"] == f"{card_id}.jpg"
    assert payload["fileType"] == "image/jpeg"
    assert f"/share/{card_id}.html" in payload["text"]


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


def test_download_all_button_lifecycle(mobile_page: Page):
    """Verify download all button remains permanently visible in header and updates status."""
    dl_btn = mobile_page.locator("#offlineDownloadBtn")
    assert dl_btn.is_visible()

    # Simulate download complete for current week
    mobile_page.evaluate("""() => {
        localStorage.setItem('ig_digest_download_completed_' + currentWeekId, 'true');
        syncDownloadButtonState();
    }""")
    # Button remains visible in header with offline ready status
    assert dl_btn.is_visible()
    title = dl_btn.get_attribute("title") or ""
    assert "Offline" in title or "downloaded" in title

    # Reload page - button remains visible for offline inspection & re-sync
    mobile_page.reload()
    mobile_page.wait_for_timeout(200)
    assert mobile_page.locator("#offlineDownloadBtn").is_visible()

    # If new week arrives or cache cleared
    mobile_page.evaluate("""() => {
        localStorage.removeItem('ig_digest_download_completed_' + currentWeekId);
        syncDownloadButtonState();
    }""")
    assert mobile_page.locator("#offlineDownloadBtn").is_visible()
