"""
tests/test_recommendations.py — Tests for AI creator recommendations parsing and fallback.
"""

from unittest.mock import patch, MagicMock
import pytest
from recommendations import (
    sanitize_and_validate_recommendations,
    refresh_recommendations,
    check_agy_auth,
)


def test_sanitize_and_validate_recommendations():
    raw_markdown = """```json
    [
        {"handle": "@valid_tech", "name": "Valid Tech", "category": "ai_tech", "reason": "Great coding tutorials", "follower_scale": "200K"},
        {"handle": "existing_user", "name": "Existing", "category": "ai_tech", "reason": "Already followed"},
        {"handle": "invalid handle with spaces!", "name": "Bad Handle", "category": "ai_tech", "reason": "None"},
        {"handle": "another.valid_123", "name": "Another Valid", "category": "ai_tech", "reason": "Top AI news"}
    ]
    ```"""
    existing = {"existing_user"}
    recs = sanitize_and_validate_recommendations(raw_markdown, "ai_tech", existing)

    assert len(recs) == 2
    assert recs[0]["handle"] == "valid_tech"
    assert recs[0]["name"] == "Valid Tech"
    assert recs[0]["category"] == "ai_tech"
    assert recs[1]["handle"] == "another.valid_123"


def test_refresh_recommendations_auth_failure_preserves_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("recommendations.RECOMMENDED_FILE", tmp_path / "recommended.json")
    with patch("recommendations.check_agy_auth", return_value=False), \
         patch("recommendations.load_recommended_creators", return_value=[{"handle": "cached_one", "category": "health"}]):
        res = refresh_recommendations()
        assert len(res) == 1
        assert res[0]["handle"] == "cached_one"


def test_refresh_recommendations_quarantines_insufficient_categories(tmp_path, monkeypatch):
    monkeypatch.setattr("recommendations.RECOMMENDED_FILE", tmp_path / "recommended.json")
    monkeypatch.setattr("recommendations.QUARANTINE_FILE", tmp_path / "quarantine.json")

    # Only 1 category succeeds
    def mock_discover(cat, samples, existing, timeout_secs=600):
        if cat == "ai_tech":
            return [{"handle": "ai_hero", "name": "AI Hero", "category": "ai_tech", "reason": "AI"}]
        return []

    with patch("recommendations.check_agy_auth", return_value=True), \
         patch("recommendations.discover_category_creators", side_effect=mock_discover), \
         patch("recommendations.load_recommended_creators", return_value=[]):
        res = refresh_recommendations()
        assert res == []
        assert (tmp_path / "quarantine.json").exists()
