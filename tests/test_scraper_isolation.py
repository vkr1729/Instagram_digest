"""
tests/test_scraper_isolation.py — Tests for scraper isolation and pinned reel handling.
"""

from unittest.mock import MagicMock, patch
import pytest
from extractor import InstagramSession, extract_single_reel_metadata, extract_creator_reels


def test_new_isolated_page_does_not_bump_recycle_counter():
    """Verify new_isolated_page creates a context page without advancing _nav_count."""
    session = InstagramSession()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_context.new_page.return_value = mock_page

    session._context = mock_context
    session._nav_count = 5

    with patch.object(session, "start"):
        isolated = session.new_isolated_page()
        assert isolated == mock_page
        assert session._nav_count == 5  # Unchanged!
        mock_context.new_page.assert_called_once()


def test_extract_single_reel_metadata_uses_provided_page():
    """When a caller passes a dedicated page, it should be used instead of calling session.get_page()."""
    mock_session = MagicMock()
    mock_page = MagicMock()
    mock_page.content.return_value = '<meta property="og:title" content="Test Reel"><time datetime="2026-09-18T10:00:00Z">'

    reel_info = {"id": "ABC123xyz", "url": "https://www.instagram.com/reel/ABC123xyz/", "creator_handle": "test_creator", "is_pinned": True}

    with patch("extractor._assert_not_blocked"):
        meta = extract_single_reel_metadata(reel_info, session=mock_session, page=mock_page)

    mock_session.get_page.assert_not_called()
    mock_page.goto.assert_called_once_with(reel_info["url"], wait_until="domcontentloaded", timeout=18000)
    assert meta is not None
    assert meta["id"] == "ABC123xyz"
    assert meta["is_pinned"] is True


def test_extract_creator_reels_exempts_pinned_reels_from_cutoff():
    """Pinned reels should be retained even if their upload timestamp is older than days_back."""
    old_timestamp = 1500000000  # Far in the past

    def mock_discover(clean_handle, max_reels=10, session=None, include_pinned=False):
        return [
            {"id": "pinned_old", "url": "https://www.instagram.com/reel/pinned_old/", "creator_handle": clean_handle, "view_count": 50000, "is_pinned": True},
            {"id": "unpinned_old", "url": "https://www.instagram.com/reel/unpinned_old/", "creator_handle": clean_handle, "view_count": 10000, "is_pinned": False},
        ]

    def mock_meta(info, session=None):
        return {
            "id": info["id"],
            "url": info["url"],
            "creator_handle": info["creator_handle"],
            "view_count": info["view_count"],
            "timestamp": old_timestamp,
            "is_pinned": info.get("is_pinned", False),
        }

    with patch("extractor.discover_creator_reel_urls", side_effect=mock_discover), \
         patch("extractor.extract_single_reel_metadata", side_effect=mock_meta):
        reels = extract_creator_reels("test_handle", days_back=7, include_pinned=True)

    # Pinned reel must be included despite being older than 7 days; unpinned old reel must be discarded.
    assert len(reels) == 1
    assert reels[0]["id"] == "pinned_old"
    assert reels[0]["is_pinned"] is True
