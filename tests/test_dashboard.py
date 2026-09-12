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
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as res:
                return res.status, res.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def post(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=b"{}", method="POST",
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

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
                   "server_build", "/api/live-progress", "/api/cookie-attention",
                   "cookieLane", "pbar", "renderExpandProgress",
                   "renderCookieBanner", "publishing", "brand-mark",
                   "Instagram Digest", "themeToggle", "data-theme",
                   "applyTheme", "banked", "cooling_down",
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
    assert 'xdg-open "${BASE_URL}/dashboard"' in launch
    assert 'xdg-open "${BASE_URL}"' not in launch  # dashboard, never bare root


def test_launcher_single_instance_guard():
    launch = Path(config.ROOT_DIR, "launch.sh").read_text(encoding="utf-8")
    assert "/api/sync-status" in launch  # already-running check
    assert "flock" in launch  # concurrent-launch serialization


def test_launcher_reclaims_stale_server():
    launch = Path(config.ROOT_DIR, "launch.sh").read_text(encoding="utf-8")
    assert "server_build" in launch  # current-vs-stale fingerprint
    assert "rev-parse" in launch
    assert "pkill" in launch  # reclaim port from our own old servers


def test_live_progress_reports_expand_and_sync(tmp_path, monkeypatch):
    (tmp_path / "expand_progress_2026-W37.json").write_text(json.dumps({
        "version": 1, "week_id": "2026-W37", "target_count": 100,
        "phase": "discovering", "done": 42, "total": 100, "banked": 30,
    }), encoding="utf-8")
    (tmp_path / "sync_progress_2026-W37.json").write_text(json.dumps({
        "version": 1, "week_id": "2026-W37", "stage": "extracting",
        "done": {f"creator{i}": True for i in range(12)},
        "candidates": [{"id": f"r{i}"} for i in range(50)],
        "extraction_complete": False, "total_sources": 40,
    }), encoding="utf-8")
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/live-progress")
    finally:
        srv.close()
    assert status == 200
    data = json.loads(body)
    assert data["success"] is True
    assert data["expand"]["phase"] == "discovering"
    assert (data["expand"]["done"], data["expand"]["total"]) == (42, 100)
    assert data["expand"]["banked"] == 30
    assert data["expand"]["active"] is True  # freshly written
    assert data["sync"]["stage"] == "extracting"
    assert (data["sync"]["visited"], data["sync"]["total"]) == (12, 40)
    assert data["sync"]["candidates"] == 50
    assert data["sync"]["active"] is True


def test_live_progress_reports_publishing(tmp_path, monkeypatch):
    (tmp_path / "sync_progress_2026-W37.json").write_text(json.dumps({
        "version": 1, "week_id": "2026-W37", "stage": "publishing",
        "ranked": [{"id": f"r{i}"} for i in range(120)],
        "published": 30, "published_total": 120,
    }), encoding="utf-8")
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/live-progress")
    finally:
        srv.close()
    assert status == 200
    sync = json.loads(body)["sync"]
    assert sync["stage"] == "publishing"
    assert (sync["published"], sync["published_total"]) == (30, 120)
    assert sync["ranked"] == 120
    assert sync["active"] is True


def test_cooling_down_stays_visible_during_backoff(tmp_path, monkeypatch):
    import os
    import time
    path = tmp_path / "sync_progress_2026-W37.json"
    path.write_text(json.dumps({
        "version": 1, "week_id": "2026-W37", "stage": "cooling_down",
        "done": {"a": True}, "candidates": [], "extraction_complete": False,
        "total_sources": 60, "blocked_handle": "@x", "resumes_in_min": 40,
    }), encoding="utf-8")
    backdated = time.time() - 30 * 60  # 30 min ago: past crash window, inside backoff
    os.utime(path, (backdated, backdated))
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/live-progress")
    finally:
        srv.close()
    assert status == 200
    sync = json.loads(body)["sync"]
    assert sync["stage"] == "cooling_down"
    assert sync["active"] is True
    assert sync["resumes_in_min"] == 40


def test_live_progress_hides_stale_crash_leftovers(tmp_path, monkeypatch):
    import os
    import time
    stale = tmp_path / "expand_progress_2026-W37.json"
    stale.write_text(json.dumps({
        "version": 1, "phase": "downloading", "done": 40, "total": 100,
    }), encoding="utf-8")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/live-progress")
    finally:
        srv.close()
    assert status == 200
    data = json.loads(body)
    assert data["expand"]["active"] is False  # crash leftover, server idle
    assert data["sync"] is None


def test_cookie_attention_flag_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(local_server, "COOKIE_ATTENTION_FILE",
                        tmp_path / "cookie_attention.json")
    for var in ("DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    assert local_server.cookie_attention_state() is None
    popped = local_server.raise_cookie_attention(
        reason="session expired in test", pipeline="expand")
    assert popped is False  # headless test env: flag only, no popup
    att = local_server.cookie_attention_state()
    assert att["reason"] == "session expired in test"
    assert att["pipeline"] == "expand"
    local_server.clear_cookie_attention()
    assert local_server.cookie_attention_state() is None


def _stubbed_popup(monkeypatch, tmp_path, returncode=0):
    import subprocess
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(local_server, "COOKIE_ATTENTION_FILE",
                        tmp_path / "cookie_attention.json")
    monkeypatch.setenv("DISPLAY", ":0")

    def _fake_run(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls


def test_cookie_popup_opens_dashboard_with_display(tmp_path, monkeypatch):
    calls = _stubbed_popup(monkeypatch, tmp_path, returncode=0)
    assert local_server.raise_cookie_attention(reason="x", pipeline="y") is True
    assert calls == [["xdg-open", "http://127.0.0.1:8080/dashboard"]]


def test_cookie_popup_reports_launcher_failure(tmp_path, monkeypatch):
    calls = _stubbed_popup(monkeypatch, tmp_path, returncode=3)
    assert local_server.raise_cookie_attention(reason="x", pipeline="y") is False
    assert len(calls) == 1


def test_corrupt_attention_flag_does_not_suppress_popup(tmp_path, monkeypatch):
    (tmp_path / "cookie_attention.json").write_text("{}", encoding="utf-8")
    calls = _stubbed_popup(monkeypatch, tmp_path, returncode=0)
    assert local_server.cookie_attention_state() is None
    assert local_server.raise_cookie_attention(reason="x", pipeline="y") is True
    assert len(calls) == 1


def test_cookie_popup_fires_once_per_flag(tmp_path, monkeypatch):
    calls = _stubbed_popup(monkeypatch, tmp_path, returncode=0)
    assert local_server.raise_cookie_attention(reason="x", pipeline="y") is True
    assert local_server.raise_cookie_attention(reason="x", pipeline="y") is False
    assert len(calls) == 1  # no stacked tabs while the banner is pending


def test_cookie_failure_sites_raise_attention():
    import pathlib
    main_src = pathlib.Path(config.ROOT_DIR, "main.py").read_text(encoding="utf-8")
    # Cookie-alert email sites (weekly feed, expand feed, creator-path login
    # redirect) must all raise the dashboard popup, plus the validation gate.
    assert main_src.count("notifier.send_cookie_alert_email()") == 3
    assert main_src.count("local_server.raise_cookie_attention(") == 4
    assert '"/accounts/login" in str(exc)' in main_src


def test_sync_status_reports_server_build(tmp_path, monkeypatch):
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/sync-status")
    finally:
        srv.close()
    assert status == 200
    data = json.loads(body)
    assert isinstance(data.get("server_build"), str) and data["server_build"]
    assert local_server.server_build() == data["server_build"]  # cached, stable


def test_resume_stage_gates_agree_on_publishing():
    import main as main_module
    assert "publishing" in main_module.RESUMABLE_SYNC_STAGES
    assert "publishing" in main_module.RANKED_SYNC_STAGES
    assert set(main_module.RANKED_SYNC_STAGES) <= set(main_module.RESUMABLE_SYNC_STAGES)


def test_expand_count_clamped():
    assert local_server._clamp_expand_count("100") == 100
    assert local_server._clamp_expand_count("9999") == 500
    assert local_server._clamp_expand_count("-5") == 1
    for bad in ("abc", "", None):
        try:
            local_server._clamp_expand_count(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_dashboard_adhoc_deploys_to_pages(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        local_server, "trigger_adhoc_sync_task",
        lambda deploy=False, **kwargs: seen.setdefault("deploy", deploy) or {"success": True},
    )
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, body = srv.post("/api/sync-adhoc")
    finally:
        srv.close()
    assert status == 200
    assert seen["deploy"] is True  # midweek reels must reach the mobile PWA


def test_expand_route_rejects_garbage_count(tmp_path, monkeypatch):
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, _ = srv.post("/api/expand?count=abc")
    finally:
        srv.close()
    assert status == 400  # no pipeline started, connection stays clean


def test_cross_origin_post_rejected(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(local_server, "_schedule_server_shutdown",
                        lambda delay=0.5: calls.append(delay) or 4321)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, _ = srv.post("/api/server/shutdown",
                             headers={"Origin": "https://evil.example"})
    finally:
        srv.close()
    assert status == 403
    assert calls == []  # scheduler never invoked


def test_unknown_api_path_is_not_shadowed(tmp_path, monkeypatch):
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, _ = srv.get("/api/does-not-exist")
    finally:
        srv.close()
    assert status == 404


def test_launcher_reclaim_is_locked_and_anchored():
    launch = Path(config.ROOT_DIR, "launch.sh").read_text(encoding="utf-8")
    assert "flock -n 9" in launch
    # Reclaim happens under the won lock, never on a pre-lock snapshot.
    assert launch.index("flock -n 9") < launch.index("pkill -f")
    # Anchored to a python interpreter so editors/greps cannot match.
    assert 'pkill -f "^[^ ]*python[^ ]* .*/main\\.py --serve( |$)"' in launch
    # port_answers degrades closed without curl (no spurious reclaim).
    assert "port_answers() {\n    # Any HTTP reply" in launch
    assert launch.count("command -v curl") >= 2
    assert "LAUNCH_LOCK_FILE" in launch
    # The losing launcher only opens a browser that answers, else exits nonzero.
    assert """    if server_up; then
        open_browser
        exit 0
    fi
    exit 1""" in launch


def test_resume_script_probes_server_threads():
    script = Path(config.ROOT_DIR, "resume_pending.sh").read_text(encoding="utf-8")
    assert "/api/sync-status" in script
    assert "/api/expand/status" in script
    assert "is_running" in script


def test_expand_checkpoint_spares_active_read_path():
    import pathlib
    main_src = pathlib.Path(config.ROOT_DIR, "main.py").read_text(encoding="utf-8")
    assert "stale != checkpoint_file and stale != read_path" in main_src
    # ...and the spared file is dropped once its items are integrated.
    assert "for done_file in {checkpoint_file, read_path}:" in main_src
