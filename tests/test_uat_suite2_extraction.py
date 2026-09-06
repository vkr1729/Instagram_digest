"""
test_uat_suite2_extraction.py — Automated tests for Suite 2: Public Reel Extraction.
"""

from datetime import datetime, timedelta, timezone
import pytest
import extractor


def test_uat_2_2_and_2_3_metadata_integrity_and_7day_filter(monkeypatch):
    """
    UAT-2.2 & 2.3: Verify reel parser checks all required fields and filters older than 7 days.
    """
    now = datetime.now(timezone.utc)
    recent_ts = int((now - timedelta(days=2)).timestamp())
    stale_ts = int((now - timedelta(days=10)).timestamp())

    # Mock discovered reel URLs
    monkeypatch.setattr(extractor, "discover_creator_reel_urls", lambda handle, max_reels: [
        {"id": "reel_recent", "url": "https://www.instagram.com/reel/reel_recent/", "creator_handle": handle, "view_count": 50000},
        {"id": "reel_stale", "url": "https://www.instagram.com/reel/reel_stale/", "creator_handle": handle, "view_count": 100000},
    ])

    # Mock metadata extraction
    def mock_meta(info):
        rid = info["id"]
        if rid == "reel_recent":
            return {
                "id": "reel_recent",
                "url": info["url"],
                "creator_handle": info["creator_handle"],
                "caption": "Tech review caption",
                "view_count": 50000,
                "like_count": 4000,
                "comment_count": 250,
                "duration": 45,
                "timestamp": recent_ts,
                "thumbnail": "https://example.com/thumb.jpg",
            }
        else:
            return {
                "id": "reel_stale",
                "url": info["url"],
                "creator_handle": info["creator_handle"],
                "caption": "Old reel",
                "view_count": 100000,
                "like_count": 8000,
                "comment_count": 500,
                "duration": 30,
                "timestamp": stale_ts,
                "thumbnail": "https://example.com/thumb2.jpg",
            }

    monkeypatch.setattr(extractor, "extract_single_reel_metadata", mock_meta)

    reels = extractor.extract_creator_reels("mkbhd", max_reels=10, days_back=7)

    # 1. Stale reel must be filtered out (older than 7 days)
    assert len(reels) == 1
    recent = reels[0]
    assert recent["id"] == "reel_recent"

    # 2. Verify all metadata integrity fields are present
    required_fields = [
        "id", "url", "view_count", "like_count", "comment_count",
        "caption", "duration", "timestamp", "creator_handle"
    ]
    for field in required_fields:
        assert field in recent
        assert recent[field] is not None


def test_pinned_and_missing_timestamp_exclusion(monkeypatch):
    """
    Verify reels with missing/0 timestamps are strictly excluded from output.
    """
    monkeypatch.setattr(extractor, "discover_creator_reel_urls", lambda handle, max_reels: [
        {"id": "reel_no_ts", "url": "https://www.instagram.com/reel/reel_no_ts/", "creator_handle": handle, "view_count": 50000},
    ])
    monkeypatch.setattr(extractor, "extract_single_reel_metadata", lambda info: {
        "id": "reel_no_ts",
        "url": info["url"],
        "creator_handle": info["creator_handle"],
        "caption": "Reel without valid date",
        "view_count": 50000,
        "like_count": 4000,
        "comment_count": 250,
        "duration": 30,
        "timestamp": 0,
        "thumbnail": "https://example.com/thumb.jpg",
    })

    reels = extractor.extract_creator_reels("mkbhd", max_reels=10, days_back=7)
    assert len(reels) == 0, "Reels with missing/0 timestamp must be excluded"


def test_categorize_creator_6_categories():
    """Verify categorize_creator maps handles to the 6 new categories."""
    assert extractor.categorize_creator("mkbhd", "Marques Brownlee") == "ai_tech"
    assert extractor.categorize_creator("peterattiamd", "Dr. Peter Attia") == "health"
    assert extractor.categorize_creator("casarthakahuja", "Sarthak Ahuja") == "finance"
    assert extractor.categorize_creator("_masalalab", "Krish Ashok") == "food"
    assert extractor.categorize_creator("chalchitratalks", "Chalchitra Talks") == "entertainment"
    assert extractor.categorize_creator("veritasium", "Veritasium") == "niche"

