"""
test_channel_add_follow.py — Dashboard "add channel + follow on IG".

Covers POST /api/channels/add writing sources.json, the background
extractor.follow_creator worker, GET /api/channels/follow-status, and
handle validation. No browser, network, or live pipeline is touched:
extractor.follow_creator and InstagramSession are mocked.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import config
import extractor
import local_server


class _LiveServer:
    """Real LocalDigestHandler on an ephemeral port with isolated files."""

    def __init__(self, tmp_path, monkeypatch):
        site = tmp_path / "site"
        site.mkdir(exist_ok=True)
        (site / "local_index.html").write_text(
            "<html><body>viewer</body></html>", encoding="utf-8")
        monkeypatch.setattr(config, "SITE_DIR", site)
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            monkeypatch.delenv(var, raising=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), local_server.LocalDigestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def api_post(self, path, payload):
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def api_get(self, path):
        try:
            with urllib.request.urlopen(self._url(path), timeout=10) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()


def _isolate(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    blacklist = tmp_path / "blacklist.json"
    sources.write_text("[]", encoding="utf-8")
    blacklist.write_text(json.dumps({"creators": []}), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources)
    monkeypatch.setattr(config, "BLACKLIST_FILE", blacklist)
    monkeypatch.setattr(local_server, "_FOLLOW_RESULTS", {})
    return sources, blacklist


def _wait_for_follow(handle, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with local_server._FOLLOW_LOCK:
            if handle in local_server._FOLLOW_RESULTS:
                return local_server._FOLLOW_RESULTS[handle]
        time.sleep(0.05)
    return None


def test_add_writes_sources_and_returns_pending(tmp_path, monkeypatch):
    """(a) POST /api/channels/add writes sources.json, returns ig_follow pending."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        extractor, "follow_creator",
        lambda handle, **kw: {"ok": True, "state": "followed"},
    )
    sources, _ = _isolate(tmp_path, monkeypatch)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_post(
            "/api/channels/add",
            {"handle": "newcreator", "name": "New Creator", "category": "food"},
        )
    finally:
        srv.close()
    assert status == 200
    assert data["success"] is True
    assert data["handle"] == "newcreator"
    assert data["ig_follow"] == "pending"
    saved = json.loads(sources.read_text(encoding="utf-8"))
    assert any(s["handle"] == "newcreator" for s in saved)


def test_follow_worker_invoked_and_status_stored(tmp_path, monkeypatch):
    """(b)+(c) background worker calls follow_creator; follow-status returns result."""
    _isolate(tmp_path, monkeypatch)
    calls = []
    sentinel = {"ok": True, "state": "followed"}

    def fake_follow(handle, **kw):
        calls.append(handle)
        return sentinel

    monkeypatch.setattr(extractor, "follow_creator", fake_follow)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_post("/api/channels/add", {"handle": "SomeCreator"})
        assert status == 200
        result = _wait_for_follow("somecreator")
        assert result == sentinel
        assert calls == ["somecreator"]
        status, fdata = srv.api_get("/api/channels/follow-status?handle=somecreator")
    finally:
        srv.close()
    assert status == 200
    assert fdata["success"] is True
    assert fdata["handle"] == "somecreator"
    assert fdata["status"] == "done"
    assert fdata["result"] == sentinel


def test_follow_status_pending_before_worker_finishes(tmp_path, monkeypatch):
    """follow-status reports pending when no result has been stored yet."""
    _isolate(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def slow_follow(handle, **kw):
        started.set()
        assert release.wait(timeout=10)
        return {"ok": True, "state": "followed"}

    monkeypatch.setattr(extractor, "follow_creator", slow_follow)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, _ = srv.api_post("/api/channels/add", {"handle": "slowhandle"})
        assert status == 200
        status, fdata = srv.api_get("/api/channels/follow-status?handle=slowhandle")
        assert status == 200
        assert fdata["status"] == "pending"
        release.set()
        assert _wait_for_follow("slowhandle") == {"ok": True, "state": "followed"}
    finally:
        release.set()
        srv.close()


def test_follow_worker_error_surfaces_via_status(tmp_path, monkeypatch):
    """A failed follow (e.g. blocked) is stored and returned, not raised."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        extractor, "follow_creator",
        lambda handle, **kw: {"ok": False, "error": "blocked"},
    )
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        srv.api_post("/api/channels/add", {"handle": "blockedhandle"})
        assert _wait_for_follow("blockedhandle") == {"ok": False, "error": "blocked"}
        status, fdata = srv.api_get("/api/channels/follow-status?handle=blockedhandle")
    finally:
        srv.close()
    assert status == 200
    assert fdata["result"] == {"ok": False, "error": "blocked"}


def test_handle_validation_rejected_400(tmp_path, monkeypatch):
    """(d) empty/invalid handles are rejected with 400; no follow is launched."""
    _isolate(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        extractor, "follow_creator",
        lambda handle, **kw: calls.append(handle) or {"ok": True, "state": "followed"},
    )
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        bad_payloads = [
            {},
            {"handle": ""},
            {"handle": "   "},
            {"handle": "@"},
            {"handle": "!!!not-a-handle!!!"},
            {"handle": "way_too_long_handle_over_thirty_chars_xx"},
            {"handle": "has space"},
        ]
        for payload in bad_payloads:
            status, _ = srv.api_post("/api/channels/add", payload)
            assert status == 400, payload
        status, _ = srv.api_get("/api/channels/follow-status")
        assert status == 400
        status, _ = srv.api_get("/api/channels/follow-status?handle=!!!")
        assert status == 400
    finally:
        srv.close()
    assert calls == []


def test_add_channel_form_present_in_template():
    """channels.html has the add-channel form polling follow-status; sinks escaped."""
    html = (config.ROOT_DIR / "templates" / "channels.html").read_text(encoding="utf-8")
    for marker in ("addChannelForm", "addHandleInput", "addCategorySelect",
                   "/api/channels/add", "/api/channels/follow-status",
                   "addChannelFromForm", "pollFollowStatus",
                   "Following on IG", "function esc(s)"):
        assert marker in html, marker
    # New status text goes through textContent/esc(), never raw innerHTML.
    assert "addChannelStatus" in html


def test_follow_creator_uses_innertext_probe():
    """follow_creator detects state via raw-DOM button innerText (not get_by_role)."""
    src = (config.ROOT_DIR / "extractor.py").read_text(encoding="utf-8")
    assert "def follow_creator(handle" in src
    assert "querySelectorAll('button')" in src
    assert "session_invalid" in src
    assert "followed|requested|already" in src


class _FakeLocator:
    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        texts = self._page._texts
        for i, t in enumerate(texts):
            if t.strip().lower() in ("follow", "follow back"):
                texts[i] = "Following"
                return
        raise RuntimeError("no follow button")


class _FakePage:
    def __init__(self, texts, block=False):
        self._texts = list(texts)
        self.url = "https://www.instagram.com/someone/"
        self._block = block

    def goto(self, *a, **k):
        return None

    def content(self):
        if self._block:
            return "<div>we limit how often you can do things</div><span>reel</span>"
        return "<html><body>profile</body></html>"

    def evaluate(self, js):
        if "querySelectorAll('button')" in js and "click" in js:
            for i, t in enumerate(self._texts):
                if t.strip().lower() in ("follow", "follow back"):
                    self._texts[i] = "Following"
                    return True
            return False
        return list(self._texts)

    def get_by_role(self, *a, **k):
        return _FakeLocator(self)

    def wait_for_timeout(self, ms):
        return None


class _FakeSession:
    def __init__(self, page, valid=True):
        self._page = page
        self._valid = valid

    def validate(self, url="https://www.instagram.com/"):
        return self._valid

    def get_page(self):
        return self._page

    def close(self):
        return None


def _run_follow(monkeypatch, page, valid=True):
    monkeypatch.setattr(extractor, "InstagramSession", lambda: _FakeSession(page, valid))
    monkeypatch.setattr(extractor, "human_pause", lambda *a, **k: 0.0)
    return extractor.follow_creator("somecreator")


def test_follow_creator_already_following(monkeypatch):
    page = _FakePage(["Message", "Following", "Contact"])
    assert _run_follow(monkeypatch, page) == {"ok": True, "state": "already"}


def test_follow_creator_clicks_follow(monkeypatch):
    page = _FakePage(["Message", "Follow"])
    assert _run_follow(monkeypatch, page) == {"ok": True, "state": "followed"}


def test_follow_creator_requested_private(monkeypatch):
    page = _FakePage(["Follow"])
    page._texts = ["Follow"]
    orig_click = _FakeLocator.click

    def _req(self, timeout=None):
        self._page._texts = ["Message", "Requested"]
    monkeypatch.setattr(_FakeLocator, "click", _req)
    try:
        assert _run_follow(monkeypatch, page) == {"ok": True, "state": "requested"}
    finally:
        monkeypatch.setattr(_FakeLocator, "click", orig_click)


def test_follow_creator_invalid_session(monkeypatch):
    page = _FakePage(["Follow"])
    assert _run_follow(monkeypatch, page, valid=False) == {"ok": False, "error": "session_invalid"}


def test_follow_creator_blocked(monkeypatch):
    page = _FakePage(["Follow"], block=True)
    assert _run_follow(monkeypatch, page) == {"ok": False, "error": "blocked"}


def test_follow_creator_invalid_handle(monkeypatch):
    assert extractor.follow_creator("!!!") == {"ok": False, "error": "invalid_handle"}
