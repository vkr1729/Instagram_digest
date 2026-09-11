"""
test_scraper_hardening.py — Regression tests for P0-1 / P0-2 anti-bot fixes.

Covers: Gaussian human_pause, per-context fingerprint rotation + stealth
script, Following-API retry/backoff with inter-page pacing. No network.
"""

import json
import random
import time

import config
import extractor


def test_human_pause_gaussian_floored_with_variance(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    random.seed(20260911)
    try:
        for _ in range(300):
            extractor.human_pause(mu=3.8, sigma=1.1, floor=1.5)
    finally:
        random.seed()

    assert len(sleeps) == 300
    assert min(sleeps) >= 1.5
    assert len(set(sleeps)) > 200  # continuous distribution, not fixed steps
    mean = sum(sleeps) / len(sleeps)
    assert 3.0 < mean < 6.0  # Gaussian core + ~10% long tail
    assert max(sleeps) > 7.0  # long tail actually fires over 300 draws


def test_open_context_rotates_fingerprint_and_masks_webdriver():
    seen_uas = set()
    inits = []

    class FakePage:
        def close(self):
            pass

    class FakeContext:
        def __init__(self, **kw):
            self.kw = kw

        def add_init_script(self, script):
            inits.append(script)

        def add_cookies(self, cookies):
            pass

        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        def new_context(self, **kw):
            seen_uas.add(kw.get("user_agent"))
            widths = {v[0] for v in extractor.VIEWPORT_POOL}
            assert kw["viewport"]["width"] in widths
            assert kw["locale"] in extractor.LOCALE_POOL
            assert kw["timezone_id"] in extractor.TIMEZONE_POOL
            return FakeContext(**kw)

    for _ in range(12):
        sess = extractor.InstagramSession()
        sess._browser = FakeBrowser()
        sess._inject_cookies = lambda: None
        sess._open_context()
        assert sess._page is not None

    assert len(seen_uas) > 1  # rotation actually varies the UA
    assert seen_uas <= set(extractor.USER_AGENT_POOL)
    assert inits and all("webdriver" in s for s in inits)


def test_following_api_retries_429_with_backoff_then_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FOLLOWING_CACHE_FILE", tmp_path / "following_cache.json")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "cookies.json").write_text(json.dumps({
        "cookies_dict": {"ds_user_id": "123", "csrftoken": "tok"},
    }))

    calls = {"n": 0}

    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    def _fake_get(url, headers=None, cookies=None, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            return Resp(429, {"message": "rate limited"})
        if "max_id" in url:
            return Resp(200, {"users": [
                {"username": "chefuser", "full_name": "Chef User", "is_private": False},
            ]})
        return Resp(200, {
            "users": [
                {"username": "techuser", "full_name": "Tech User", "is_private": False},
                {"username": "priv", "full_name": "Priv", "is_private": True},
            ],
            "next_max_id": "page2",
        })

    import requests
    monkeypatch.setattr(requests, "get", _fake_get)
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    accounts = extractor.sync_following_accounts(force=True)

    handles = {a["handle"] for a in accounts}
    assert handles == {"techuser", "chefuser"}  # private filtered, both pages kept
    assert calls["n"] == 4  # 2x429 + page1 + page2
    assert len(sleeps) >= 3  # 2 backoffs (growing) + inter-page pacing
    assert sleeps[1] > sleeps[0]  # exponential growth
