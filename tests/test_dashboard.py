"""
test_dashboard.py — Focused tests for the desktop Ops Dashboard.

Covers local_server.resume_pipeline_state() (backs GET /api/resume-state),
the /dashboard + /viewer routes against a live in-process server, the viewer
header contract (ops buttons gone, download kept, dashboard linked), and the
launcher entry point. No browser, network, or pipeline is touched.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import urllib.request

import config
import local_server


def _stage(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


def test_resume_state_reports_pending_work(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _stage(tmp_path, "expand_checkpoint_2026-09-12.json",
           {"version": 1, "target_count": 100,
            "reels": [{"id": "a"}, {"id": "b"}, {"noid": True}]})
    _stage(tmp_path, "expand_checkpoint_legacy.json", [{"id": "old"}])
    (tmp_path / "expand_checkpoint_broken.json").write_text("{nope", encoding="utf-8")
    _stage(tmp_path, "sync_progress_2026-09-12.json",
           {"version": 1, "stage": "extracting", "done": {"h1": False, "h2": True},
            "candidates": [{"id": "c1"}]})
    _stage(tmp_path, "sync_progress_ranked.json",
           {"version": 1, "stage": "ranked", "ranked": [{"id": "r1"}, {}]})
    _stage(tmp_path, "sync_progress_bogus.json", {"version": 1, "stage": "zzz"})
    resp = local_server.resume_pipeline_state()
    by_file = {e["file"]: e for e in resp["expand"]}
    assert by_file["expand_checkpoint_2026-09-12.json"]["banked"] == 2
    assert by_file["expand_checkpoint_2026-09-12.json"]["target_count"] == 100
    assert by_file["expand_checkpoint_legacy.json"]["banked"] == 1
    assert by_file["expand_checkpoint_legacy.json"]["target_count"] == 100
    assert "expand_checkpoint_broken.json" not in by_file
    sync_by_file = {e["file"]: e for e in resp["sync"]}
    assert sync_by_file["sync_progress_2026-09-12.json"]["banked"] == 1
    assert sync_by_file["sync_progress_2026-09-12.json"]["creators_visited"] == 2
    assert sync_by_file["sync_progress_ranked.json"]["banked"] == 1
    assert "sync_progress_bogus.json" not in sync_by_file


def test_resume_state_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert local_server.resume_pipeline_state() == {"expand": [], "sync": []}


class _LiveServer:
    """Real LocalDigestHandler on an ephemeral port with patched content dirs."""

    def __init__(self, tmp_path, monkeypatch):
        site = tmp_path / "site"
        site.mkdir()
        (site / "local_index.html").write_text(
            "<html><body><div id=\"topChrome\">viewer</div></body></html>", encoding="utf-8")
        monkeypatch.setattr(config, "SITE_DIR", site)
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            monkeypatch.delenv(var, raising=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), local_server.LocalDigestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as res:
            return res.status, res.read().decode("utf-8")

    def post(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=b"{}", method="POST")
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.read().decode("utf-8")

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()


def test_dashboard_route_serves_ops_page(tmp_path, monkeypatch):
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/dashboard")
    finally:
        srv.close()
    assert status == 200
    for marker in ("resumeLane", "expandCount", "cookieBtn", "activityBody",
                   "/api/resume-state", "/api/expand?count=", "/api/cookies/refresh",
                   "/api/sync-adhoc", "/api/server/shutdown", "serverKillBtn",
                   "/viewer", "/channels"):
        assert marker in body, marker


def test_server_shutdown_route_schedules_kill(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(local_server, "_schedule_server_shutdown",
                        lambda delay=0.5: calls.append(delay) or 4321)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.post("/api/server/shutdown")
    finally:
        srv.close()
    assert status == 200
    data = json.loads(body)
    assert data["success"] is True
    assert data["pid"] == 4321
    assert calls == [0.5]


def test_schedule_server_shutdown_signals_self(monkeypatch):
    import os
    import signal as sigmod
    timers = []
    kills = []

    class _FakeTimer:
        def __init__(self, delay, fn):
            timers.append((delay, fn))
        daemon = False
        def start(self):
            pass

    monkeypatch.setattr(local_server.threading, "Timer", _FakeTimer)
    monkeypatch.setattr(local_server.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    pid = local_server._schedule_server_shutdown(delay=0.5)
    assert pid == os.getpid()
    assert len(timers) == 1 and timers[0][0] == 0.5
    timers[0][1]()  # fire the deferred kill
    assert kills == [(os.getpid(), sigmod.SIGTERM)]


def test_viewer_route_serves_viewer(tmp_path, monkeypatch):
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/viewer")
    finally:
        srv.close()
    assert status == 200
    assert "topChrome" in body


def test_resume_state_route_reflects_disk(tmp_path, monkeypatch):
    _stage(tmp_path, "expand_checkpoint_2026-09-12.json",
           {"version": 1, "target_count": 40, "reels": [{"id": "x"}]})
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/resume-state")
    finally:
        srv.close()
    assert status == 200
    data = json.loads(body)
    assert data["success"] is True
    assert data["expand"][0]["target_count"] == 40
    assert data["expand"][0]["banked"] == 1
    assert data["sync"] == []


def test_viewer_header_contract():
    header = (config.TEMPLATES_DIR / "partials" / "header.html").read_text(encoding="utf-8")
    for gone in ("expand100Btn", "cookieRefreshBtn", "adhocSyncViewerBtn",
                 "triggerExpand100", "triggerCookieRefresh", "triggerAdhocSyncViewer"):
        assert gone not in header, gone
    assert "offlineDownloadBtn" in header  # download stays everywhere
    assert 'href="/dashboard"' in header  # local-only dashboard link


def test_viewer_js_has_no_ops_triggers():
    player = (config.TEMPLATES_DIR / "partials" / "player.js").read_text(encoding="utf-8")
    for gone in ("triggerExpand100", "triggerCookieRefresh", "triggerAdhocSyncViewer",
                 "expand100Btn", "cookieRefreshBtn", "adhocSyncViewerBtn"):
        assert gone not in player, gone


def test_launcher_opens_dashboard():
    launch = Path(config.ROOT_DIR, "launch.sh").read_text(encoding="utf-8")
    assert "PORT=8080" in launch
    assert "/dashboard" in launch
    assert "xdg-open" in launch


def test_launcher_single_instance_guard():
    launch = Path(config.ROOT_DIR, "launch.sh").read_text(encoding="utf-8")
    assert "/api/sync-status" in launch  # already-running check
    assert "flock" in launch  # concurrent-launch serialization
    assert 'xdg-open "http://127.0.0.1:8080/"' not in launch
