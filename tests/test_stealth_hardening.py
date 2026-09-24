"""
test_stealth_hardening.py — Regression tests for stealth/bot-detection hardening.

Covers: contactpoint block markers -> InstagramBlocked, stealth script spoofs,
Sec-CH-UA client hints in requests headers, Following-API pagination cap.
No network.
"""

import json
import time

import pytest

import config
import extractor
from extractor import (
    InstagramBlocked,
    _assert_not_blocked,
    _BLOCK_MARKERS,
    _client_hint_headers,
    _stealth_script_for_locale,
)


class _FakePage:
    def __init__(self, url):
        self.url = url


@pytest.mark.parametrize("marker", [
    "/update_risky_contactpoint",
    "risky_contactpoint",
    "verify_contactpoint",
    "/accounts/confirm",
    "checkpoint",
])
def test_block_markers_include_contactpoint_challenge(marker):
    assert marker in _BLOCK_MARKERS


@pytest.mark.parametrize("url", [
    "https://www.instagram.com/accounts/update_risky_contactpoint/?next=/",
    "https://www.instagram.com/accounts/confirm_email_as_safe/",
    "https://www.instagram.com/challenge/12345/verify_contactpoint/",
    "https://www.instagram.com/checkpoint/block/",
])
def test_assert_not_blocked_raises_on_contactpoint_redirects(url):
    with pytest.raises(InstagramBlocked):
        _assert_not_blocked(_FakePage(url), "probe")


def test_assert_not_blocked_passes_on_clean_reel_url():
    _assert_not_blocked(_FakePage("https://www.instagram.com/reel/ABC123/"), "probe")


def test_feed_uses_shared_block_markers():
    import inspect
    src = inspect.getsource(extractor.extract_external_reels_from_feed)
    # Feed crawler redirects must go through the same tuple (no private copy).
    assert "_BLOCK_MARKERS" in src


def test_stealth_script_contains_new_spoofs():
    script = _stealth_script_for_locale("en-US")
    # (a) chrome.runtime + loadTimes stubs
    assert "chrome.runtime" in script
    assert "loadTimes" in script
    # (b) plugins length guard
    assert "plugins" in script
    assert "length" in script
    # (c) hardware spoofing
    assert "hardwareConcurrency" in script
    assert "deviceMemory" in script
    # (d) WebGL vendor/renderer spoof
    assert "getParameter" in script
    assert "ANGLE" in script
    # (e) permissions.query override
    assert "permissions" in script
    assert "notifications" in script
    # (f) languages kept and locale-matched
    assert "languages" in script
    assert "en-US" in script


def test_stealth_script_locale_matching_and_wrapped():
    script = _stealth_script_for_locale("en-GB")
    assert "en-GB" in script
    assert "try" in script and "catch" in script


def test_client_hint_headers_match_ua_major():
    hints = _client_hint_headers(extractor.DEFAULT_USER_AGENT)
    assert "Sec-CH-UA" in hints
    assert hints["Sec-CH-UA-Mobile"] == "?0"
    assert "Sec-CH-UA-Platform" in hints
    # UA major version must appear in the brand list
    assert '"131"' in hints["Sec-CH-UA"] or '"120"' in hints["Sec-CH-UA"]
    hints130 = _client_hint_headers(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    assert '"130"' in hints130["Sec-CH-UA"]


def test_sync_following_sends_client_hints(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FOLLOWING_CACHE_FILE", tmp_path / "following_cache.json")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "cookies.json").write_text(json.dumps({
        "cookies_dict": {"ds_user_id": "123", "csrftoken": "tok"},
    }))

    seen = {}

    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"users": [], "has_more": False}

    def _fake_get(url, headers=None, cookies=None, timeout=None):
        seen.update(headers or {})
        return Resp()

    import requests
    monkeypatch.setattr(requests, "get", _fake_get)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    extractor.sync_following_accounts(force=True)

    assert "Sec-CH-UA" in seen
    assert seen["Sec-CH-UA-Mobile"] == "?0"
    assert "Sec-CH-UA-Platform" in seen


def test_following_pagination_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FOLLOWING_CACHE_FILE", tmp_path / "following_cache.json")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "cookies.json").write_text(json.dumps({
        "cookies_dict": {"ds_user_id": "123", "csrftoken": "tok"},
    }))
    monkeypatch.setattr(extractor, "MAX_FOLLOWING_PAGES", 3)

    calls = {"n": 0}

    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            # Always offers another page (runaway cursor) with has_more True.
            return {"users": [], "next_max_id": "cursor-x", "has_more": True}

    def _fake_get(url, headers=None, cookies=None, timeout=None):
        calls["n"] += 1
        return Resp()

    import requests
    monkeypatch.setattr(requests, "get", _fake_get)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    extractor.sync_following_accounts(force=True)
    assert calls["n"] == 3  # capped, not infinite


def test_following_pagination_stops_on_has_more_false(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FOLLOWING_CACHE_FILE", tmp_path / "following_cache.json")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "cookies.json").write_text(json.dumps({
        "cookies_dict": {"ds_user_id": "123", "csrftoken": "tok"},
    }))

    calls = {"n": 0}

    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            # Stale cursor present but API says no more data.
            return {"users": [], "next_max_id": "stale", "has_more": False}

    def _fake_get(url, headers=None, cookies=None, timeout=None):
        calls["n"] += 1
        return Resp()

    import requests
    monkeypatch.setattr(requests, "get", _fake_get)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    extractor.sync_following_accounts(force=True)
    assert calls["n"] == 1


def test_download_reel_video_headers_include_client_hints():
    import inspect
    src = inspect.getsource(extractor.download_reel_video)
    assert "_client_hint_headers" in src
