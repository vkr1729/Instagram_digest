"""
test_overnight_round3.py — Round-3 experiment features (F12..F15).

F12 lock-status endpoint · F13 digest-email shortfall line · F14 stale
recommendation badge markers · F15 digest top channels. Live-server tests
need the venv (local_server -> extractor -> playwright).
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import config


class _LiveServer:
    def __init__(self, tmp_path, monkeypatch):
        import local_server
        site = tmp_path / "site"
        site.mkdir(exist_ok=True)
        (site / "local_index.html").write_text("<html></html>", encoding="utf-8")
        monkeypatch.setattr(config, "SITE_DIR", site)
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            monkeypatch.delenv(var, raising=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), local_server.LocalDigestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def api_get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=10) as res:
            return res.status, json.loads(res.read().decode("utf-8"))

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()


# --- F12: lock-status endpoint ---

def test_f12_lock_status_free_and_held(tmp_path, monkeypatch):
    import main as pipeline
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, free = srv.api_get("/api/lock-status")
        assert status == 200 and free == {"success": True, "locked": False, "holder": None}
        held, release = threading.Event(), threading.Event()

        def _holder():
            with pipeline._pipeline_file_lock():
                held.set()
                assert release.wait(timeout=15)

        t = threading.Thread(target=_holder, daemon=True)
        t.start()
        try:
            assert held.wait(timeout=10)
            status, busy = srv.api_get("/api/lock-status")
        finally:
            release.set()
            t.join(timeout=10)
        assert status == 200 and busy["success"] is True and busy["locked"] is True
        assert busy["holder"]["pid"] > 0 and "started_at" in busy["holder"]
    finally:
        srv.close()


def test_f12_dashboard_markers():
    html = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    for marker in ("lockMeta", "loadLockStatus", "/api/lock-status", "Pipeline BUSY"):
        assert marker in html, marker


# --- F13: email shortfall line ---

def _payloads(msg):
    return {p.get_content_type(): p.get_payload(decode=True).decode("utf-8")
            for p in msg.get_payload()}


def test_f13_email_shortfall_shown_and_hidden():
    import notifier
    short = notifier.build_email_message("2026-09-20", count=212, target=250)
    p = _payloads(short)
    assert "212/250" in p["text/plain"] and "expand +38" in p["text/plain"]
    assert "Shortfall" in p["text/html"] and "212/250" in p["text/html"]
    full = notifier.build_email_message("2026-09-20", count=250, target=250)
    p = _payloads(full)
    assert "Shortfall" not in p["text/plain"] and "Shortfall" not in p["text/html"]
    legacy = notifier.build_email_message("2026-09-20", count=250)
    assert "Shortfall" not in _payloads(legacy)["text/plain"]


# --- F14: stale recommendation badge markers ---

def test_f14_stale_badge_markers():
    html = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    for marker in ("recommended_at", "over a week old", "7 * 86400"):
        assert marker in html, marker


# --- F15: digest top channels ---

def test_f15_digest_status_top_channels(tmp_path, monkeypatch):
    dd = tmp_path / "digests"
    dd.mkdir()
    items = ([{"id": f"a{i}", "creator_handle": "alpha"} for i in range(5)]
             + [{"id": f"b{i}", "creator_handle": "@Beta"} for i in range(3)]
             + [{"id": "c0", "creator_handle": "gamma"}])
    (dd / "2026-09-20.json").write_text(
        json.dumps({"run_date": "2026-09-20", "items": items}), encoding="utf-8")
    monkeypatch.setattr(config, "DIGESTS_DIR", dd)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/digest-status")
    finally:
        srv.close()
    assert status == 200
    assert data["top_channels"] == [
        {"handle": "alpha", "count": 5},
        {"handle": "beta", "count": 3},
        {"handle": "gamma", "count": 1},
    ]
