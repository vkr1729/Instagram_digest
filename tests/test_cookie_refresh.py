"""
test_cookie_refresh.py — Focused tests for the manual cookie-refresh endpoint.

Covers local_server.refresh_cookies_status() (backs POST /api/cookies/refresh
and the dashboard "Cookies" button) without touching Chrome or the network:
- healthy export with a login session reports success + session signal,
- export without a sessionid still succeeds but flags the missing session,
- empty export, exporter crashes, and nonzero exits report failure.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import config
import local_server


def _stage_cookies(tmp_path, cookies_dict):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "cookies.json").write_text(
        json.dumps({"cookies_playwright": [], "cookies_dict": cookies_dict}),
        encoding="utf-8",
    )
    return data_dir


def _ok_run(*args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")


def test_refresh_reports_healthy_session(tmp_path, monkeypatch):
    data_dir = _stage_cookies(tmp_path, {"sessionid": "abc", "csrftoken": "def"})
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    with patch("subprocess.run", side_effect=_ok_run):
        resp = local_server.refresh_cookies_status()
    assert resp["success"] is True
    assert resp["cookie_count"] == 2
    assert resp["has_sessionid"] is True
    assert resp["refreshed_at"]


def test_refresh_flags_missing_login_session(tmp_path, monkeypatch):
    data_dir = _stage_cookies(tmp_path, {"csrftoken": "def"})
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    with patch("subprocess.run", side_effect=_ok_run):
        resp = local_server.refresh_cookies_status()
    assert resp["success"] is True
    assert resp["has_sessionid"] is False


def test_refresh_fails_on_empty_export(tmp_path, monkeypatch):
    data_dir = _stage_cookies(tmp_path, {})
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    with patch("subprocess.run", side_effect=_ok_run):
        resp = local_server.refresh_cookies_status()
    assert resp["success"] is False
    assert "Log into" in resp["error"]


def test_refresh_fails_on_exporter_crash(monkeypatch):
    with patch("subprocess.run", side_effect=OSError("dbus down")):
        resp = local_server.refresh_cookies_status()
    assert resp["success"] is False
    assert "dbus down" in resp["error"]


def test_refresh_fails_on_nonzero_exit():
    bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with patch("subprocess.run", return_value=bad):
        resp = local_server.refresh_cookies_status()
    assert resp["success"] is False
    assert "boom" in resp["error"]
