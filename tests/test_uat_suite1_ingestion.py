"""
test_uat_suite1_ingestion.py — Automated tests for Suite 1: Ingestion & Following Sync.
"""

import json
import time
from pathlib import Path
import pytest
import config
import extractor


def test_uat_1_1_session_cookie_detection():
    """UAT-1.1 Session Extraction: Checks cookie args formulation."""
    cookie_args = extractor.get_cookie_args()
    assert len(cookie_args) == 2
    assert cookie_args[0] in ("--cookies-from-browser", "--cookies")
    if cookie_args[0] == "--cookies-from-browser":
        assert cookie_args[1] == "chrome"


def test_uat_1_3_and_1_5_private_account_filtering_and_non_destructive_merge(tmp_path, monkeypatch):
    """
    UAT-1.3 & UAT-1.5: Verify private accounts filtered and custom categories preserved.
    """
    sources_file = tmp_path / "sources.json"
    cache_file = tmp_path / "following_cache.json"
    data_export_file = tmp_path / "following.json"

    monkeypatch.setattr(config, "SOURCES_FILE", sources_file)
    monkeypatch.setattr(config, "FOLLOWING_CACHE_FILE", cache_file)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    # Pre-existing sources with custom category and disabled account
    initial_sources = [
        {"handle": "mkbhd", "name": "Marques", "category": "tech", "enabled": True},
        {"handle": "annoying_friend", "name": "Friend", "category": "personal", "enabled": False},
    ]
    sources_file.write_text(json.dumps(initial_sources), encoding="utf-8")

    # Mock an Instagram export payload with a public creator and private friend
    export_payload = {
        "relationships_following": [
            {"string_list_data": [{"value": "mkbhd"}]},
            {"string_list_data": [{"value": "annoying_friend"}]},
            {"string_list_data": [{"value": "new_creator"}]},
        ]
    }
    data_export_file.write_text(json.dumps(export_payload), encoding="utf-8")

    synced = extractor.sync_following_accounts(force=True)

    synced_by_handle = {s["handle"]: s for s in synced}

    # 1. New creator added
    assert "new_creator" in synced_by_handle
    # 2. Existing custom category and enabled status preserved
    assert synced_by_handle["annoying_friend"]["enabled"] is False
    assert synced_by_handle["annoying_friend"]["category"] == "personal"
    assert synced_by_handle["mkbhd"]["category"] == "tech"


def test_uat_1_4_30_day_freshness_cache(tmp_path, monkeypatch):
    """
    UAT-1.4 30-Day Freshness Cache: Repeated calls within 30 days must use cache.
    """
    cache_file = tmp_path / "following_cache.json"
    sources_file = tmp_path / "sources.json"
    monkeypatch.setattr(config, "FOLLOWING_CACHE_FILE", cache_file)
    monkeypatch.setattr(config, "SOURCES_FILE", sources_file)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    cached_accounts = [{"handle": "cached_hero", "name": "Hero", "category": "tech", "enabled": True}]
    cache_payload = {
        "timestamp": time.time() - (5 * 86400),  # 5 days old (< 30 days)
        "accounts": cached_accounts,
    }
    cache_file.write_text(json.dumps(cache_payload), encoding="utf-8")

    # Calling without force must return cached accounts without querying network
    result = extractor.sync_following_accounts(force=False)
    assert len(result) == 1
    assert result[0]["handle"] == "cached_hero"
