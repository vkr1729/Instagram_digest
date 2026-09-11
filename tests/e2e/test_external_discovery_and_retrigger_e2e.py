"""
test_external_discovery_and_retrigger_e2e.py — Playwright browser tests for:
1. External reels discovery badge (🌐 Discovery) in viewer
2. Dynamic Top 250 count in story bar & title
3. Retrigger page UI & sync-status endpoint
4. High-signal external reels feed extraction & CookieExpiredException handling
"""

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from playwright.sync_api import sync_playwright

import config
import extractor
from site_builder import build_site
from local_server import LocalDigestHandler
from http.server import ThreadingHTTPServer


@pytest.fixture(scope="module")
def setup_hybrid_test_site(tmp_path_factory):
    """Build a test site with both followed and external reels to verify UI badges."""
    site_tmp = tmp_path_factory.mktemp("hybrid_site")
    
    # 5 test items: 3 followed, 2 external
    sample_digest = {
        "run_date": "2026-09-11",
        "items": [
            {
                "id": "reel_fol_1",
                "creator_handle": "mkbhd",
                "creator_name": "Marques Brownlee",
                "category": "ai_tech",
                "rank": 1,
                "rank_display": "#01",
                "view_count": 500000,
                "caption": "Reviewing folding phones",
                "thumbnail": "https://example.com/thumb1.jpg",
                "video_url": "https://example.com/video1.mp4",
                "is_external": False,
            },
            {
                "id": "reel_ext_1",
                "creator_handle": "cool_inventor",
                "creator_name": "cool_inventor",
                "category": "ai_tech",
                "rank": 2,
                "rank_display": "#02",
                "view_count": 350000,
                "caption": "Discovered AI hardware prototype in the wild",
                "thumbnail": "https://example.com/thumb2.jpg",
                "video_url": "https://example.com/video2.mp4",
                "is_external": True,
            },
            {
                "id": "reel_fol_2",
                "creator_handle": "hubermanlab",
                "creator_name": "Dr. Andrew Huberman",
                "category": "health",
                "rank": 3,
                "rank_display": "#03",
                "view_count": 280000,
                "caption": "Sleep optimization protocols",
                "thumbnail": "https://example.com/thumb3.jpg",
                "video_url": "https://example.com/video3.mp4",
                "is_external": False,
            },
            {
                "id": "reel_ext_2",
                "creator_handle": "biohack_daily",
                "creator_name": "biohack_daily",
                "category": "health",
                "rank": 4,
                "rank_display": "#04",
                "view_count": 420000,
                "caption": "Cold plunge temperature analysis and dopamine spike",
                "thumbnail": "https://example.com/thumb4.jpg",
                "video_url": "https://example.com/video4.mp4",
                "is_external": True,
            },
            {
                "id": "reel_fol_3",
                "creator_handle": "veritasium",
                "creator_name": "Veritasium",
                "category": "niche",
                "rank": 5,
                "rank_display": "#05",
                "view_count": 910000,
                "caption": "Why the universe expands faster than light",
                "thumbnail": "https://example.com/thumb5.jpg",
                "video_url": "https://example.com/video5.mp4",
                "is_external": False,
            }
        ]
    }
    
    r2_url_map = {
        item["id"]: item["video_url"] for item in sample_digest["items"]
    }
    
    with patch.object(config, "SITE_DIR", site_tmp):
        r2_index, local_index = build_site(sample_digest, r2_uploaded_urls=r2_url_map)
        
    return {
        "site_dir": site_tmp,
        "local_index": local_index,
        "r2_index": r2_index,
        "items": sample_digest["items"]
    }


def test_playwright_discovery_badge_and_header_controls(setup_hybrid_test_site):
    """Verify Discovery badge, header controls, and speed toggle in a real Playwright browser."""
    local_index_path = setup_hybrid_test_site["local_index"]
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        page = context.new_page()
        page.goto(f"file://{local_index_path.resolve()}")
        page.wait_for_selector(".reel-card")
        
        # 1. Verify Top count bubble displays Top 5
        top_title = page.locator(".story-bubble.active .story-title").inner_text()
        assert "Top 5" in top_title
        
        # 2. Verify top speed button is removed from header
        assert page.locator("#speedToggleBtn").count() == 0
        
        # 3. Verify single-week view hides #weekSelector
        assert page.locator("#weekSelector").count() == 0
        
        # 4. Verify external reels display .discovery-pill with "🌐 Discovery"
        reel_cards = page.locator(".reel-card")
        assert reel_cards.count() == 5
        
        # reel_ext_1 (index 1) is external
        ext_card_1 = reel_cards.nth(1)
        assert ext_card_1.locator(".discovery-pill").count() == 1
        assert "Discovery" in ext_card_1.locator(".discovery-pill").inner_text()
        
        # reel_fol_1 (index 0) is followed creator, must NOT have discovery-pill
        fol_card_1 = reel_cards.nth(0)
        assert fol_card_1.locator(".discovery-pill").count() == 0
        
        # reel_ext_2 (index 3) is external
        ext_card_2 = reel_cards.nth(3)
        assert ext_card_2.locator(".discovery-pill").count() == 1
        
        # 5. Verify 2x speed toggle button in reel card actions
        boost_btn = fol_card_1.locator(".boost-speed-btn")
        assert boost_btn.count() == 1
        assert boost_btn.inner_text() == "2x"
        
        # Click 2x button and verify class active
        boost_btn.click()
        assert "active" in (boost_btn.get_attribute("class") or "")
        
        # Scroll to external reel (index 1) to capture the Discovery badge
        page.evaluate("() => document.querySelectorAll('.reel-card')[1].scrollIntoView()")
        page.wait_for_timeout(500)
        screenshot_ext_path = config.ROOT_DIR / "tests" / "test_viewer_discovery_badge.png"
        page.screenshot(path=str(screenshot_ext_path))
        assert screenshot_ext_path.exists()
        
        browser.close()


def test_playwright_retrigger_ui_and_status_api():
    """Verify /retrigger page rendering and /api/sync-status via Playwright against LocalDigestHandler."""
    import socket

    # Pick a random free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), LocalDigestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.5)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # 1. Test /api/sync-status JSON endpoint
            response = page.goto(f"http://127.0.0.1:{port}/api/sync-status")
            assert response.status == 200
            data = json.loads(page.inner_text("body"))
            assert "status" in data
            assert "is_running" in data
            assert "last_error" in data
            
            # 2. Test /retrigger page UI with active running state
            from local_server import _SYNC_STATE, _SYNC_LOCK
            with _SYNC_LOCK:
                _SYNC_STATE["is_running"] = True
                _SYNC_STATE["status"] = "running"
                _SYNC_STATE["last_error"] = None

            with patch("local_server.trigger_adhoc_sync_task"):
                page.goto(f"http://127.0.0.1:{port}/retrigger")
                page.wait_for_selector(".card")
                
                # Check OLED Dark UI components while running
                assert "Refreshing Instagram Feed" in page.locator("#title").inner_text()
                assert page.locator("#spinner").is_visible()
                assert "Sync Running" in page.locator("#badge").inner_text()
                
                # Take screenshot of active retrigger UI
                retrigger_png = config.ROOT_DIR / "tests" / "test_retrigger_ui.png"
                page.screenshot(path=str(retrigger_png))
                assert retrigger_png.exists()

                # 3. Simulate completion and verify live transition
                with _SYNC_LOCK:
                    _SYNC_STATE["is_running"] = False
                    _SYNC_STATE["status"] = "completed"
                    _SYNC_STATE["last_result"] = 0

                # Wait for polling JS (fetches every 2s) to transition UI to "Sync Complete!"
                page.wait_for_selector("text=Sync Complete!", timeout=5000)
                assert page.locator("#title").inner_text() == "Sync Complete!"
                assert page.locator("#badge").inner_text() == "Completed"

            browser.close()
    finally:
        server.shutdown()


def test_extract_external_reels_from_feed_mock():
    """Verify extract_external_reels_from_feed filters high-signal reels and categorizes correctly."""
    mock_session = MagicMock()
    mock_page = MagicMock()
    mock_session.get_page.return_value = mock_page
    mock_page.url = "https://www.instagram.com/reels/"

    reel_dom_responses = [
        {
            "reelId": "reel_ext_high_1",
            "reelUrl": "https://www.instagram.com/reel/reel_ext_high_1/",
            "handle": "ai_insider",
            "caption": "Deep learning models are scaling fast with new GPU clusters",
            "rawLikes": "75K likes",
            "rawComments": "1.2K comments",
        },
        {
            "reelId": "reel_ext_low",
            "reelUrl": "https://www.instagram.com/reel/reel_ext_low/",
            "handle": "random_user",
            "caption": "Just having coffee today",
            "rawLikes": "5,000 likes",
            "rawComments": "20 comments",
        },
        {
            "reelId": "reel_ext_high_2",
            "reelUrl": "https://www.instagram.com/reel/reel_ext_high_2/",
            "handle": "dr_wellness",
            "caption": "Cardiologist advice on sleep and metabolic health",
            "rawLikes": "",
            "rawComments": "450 comments",
        },
    ]

    eval_idx = 0
    def mock_eval(script):
        nonlocal eval_idx
        if eval_idx < len(reel_dom_responses):
            res = reel_dom_responses[eval_idx]
            eval_idx += 1
            return res
        return None

    mock_page.evaluate.side_effect = mock_eval

    with patch("time.sleep"):
        reels = extractor.extract_external_reels_from_feed(
            session=mock_session,
            target_count=2,
            existing_ids=set(),
            active_sources=[{"handle": "mkbhd"}],
            max_evaluations=5,
        )

    assert len(reels) == 2
    assert reels[0]["id"] == "reel_ext_high_1"
    assert reels[0]["category"] == "ai_tech"
    assert reels[0]["is_external"] is True
    assert reels[0]["like_count"] == 75000

    assert reels[1]["id"] == "reel_ext_high_2"
    assert reels[1]["category"] == "health"
    assert reels[1]["is_external"] is True
    assert reels[1]["comment_count"] == 450


def test_extract_external_reels_cookie_expired_exception():
    """Verify CookieExpiredException is raised when redirected to Instagram login."""
    mock_session = MagicMock()
    mock_page = MagicMock()
    mock_session.get_page.return_value = mock_page
    mock_page.url = "https://www.instagram.com/accounts/login/?next=/reels/"

    with pytest.raises(extractor.CookieExpiredException) as exc_info:
        extractor.extract_external_reels_from_feed(
            session=mock_session,
            target_count=5,
        )

    assert "Instagram session expired" in str(exc_info.value)
