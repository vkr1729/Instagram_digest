"""
tests/test_adhoc_sync.py — Test suite for ad-hoc midweek sync and last run timestamp persistence.
"""

import json
import time
import unittest.mock as mock
from datetime import datetime, timezone
from pathlib import Path

import config
import main
import local_server


def test_last_run_persistence(tmp_path, monkeypatch):
    """Verify save_last_run_info and get_last_run_info accurately persist and retrieve timestamps."""
    dummy_last_run = tmp_path / "last_run.json"
    monkeypatch.setattr(config, "LAST_RUN_FILE", dummy_last_run)

    # Initially None
    assert main.get_last_run_info() is None

    # Save a run
    fixed_ts = 1757560000.0
    info = main.save_last_run_info("2026-09-11", timestamp=fixed_ts)
    assert dummy_last_run.exists()
    assert info["timestamp"] == fixed_ts
    assert info["week_id"] == "2026-09-11"

    # Retrieve saved run
    retrieved = main.get_last_run_info()
    assert retrieved is not None
    assert retrieved["timestamp"] == fixed_ts
    assert retrieved["week_id"] == "2026-09-11"


def test_adhoc_sync_since_timestamp_plumbing(monkeypatch):
    """Verify run_full_sync respects since_timestamp when provided."""
    calls = []

    def dummy_run_full_sync(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(main, "run_full_sync", dummy_run_full_sync)

    # Simulate running main with --ad-hoc with a stored last run 3 days ago
    past_ts = time.time() - (3 * 86400)
    monkeypatch.setattr(main, "get_last_run_info", lambda: {
        "timestamp": past_ts,
        "last_run_utc": datetime.fromtimestamp(past_ts, tz=timezone.utc).isoformat(),
        "week_id": "2026-09-08"
    })

    test_args = ["main.py", "--ad-hoc", "--dry-run"]
    monkeypatch.setattr("sys.argv", test_args)

    ret = main.main()
    assert ret == 0
    assert len(calls) == 1
    assert calls[0]["since_timestamp"] == int(past_ts)
    assert calls[0]["days_back"] == 3


def test_adhoc_sync_fallback_when_no_previous_run(monkeypatch):
    """Verify --ad-hoc falls back to default days_back when no previous run exists."""
    calls = []

    def dummy_run_full_sync(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(main, "run_full_sync", dummy_run_full_sync)
    monkeypatch.setattr(main, "get_last_run_info", lambda: None)

    test_args = ["main.py", "--ad-hoc", "--days-back", "5", "--dry-run"]
    monkeypatch.setattr("sys.argv", test_args)

    ret = main.main()
    assert ret == 0
    assert len(calls) == 1
    assert calls[0]["since_timestamp"] is None
    assert calls[0]["days_back"] == 5


def test_local_server_trigger_adhoc_sync(monkeypatch):
    """Verify trigger_adhoc_sync_task manages thread lifecycle and duplicate prevention."""
    sync_called = []

    def dummy_run_full_sync(**kwargs):
        sync_called.append(kwargs)
        time.sleep(0.05)
        return 0

    monkeypatch.setattr(main, "run_full_sync", dummy_run_full_sync)
    monkeypatch.setattr(main, "get_last_run_info", lambda: {"timestamp": time.time() - 86400, "last_run_utc": "2026-09-10T00:00:00Z", "week_id": "2026-09-10"})

    # Reset state
    with local_server._SYNC_LOCK:
        local_server._SYNC_STATE["is_running"] = False
        local_server._SYNC_STATE["status"] = "idle"

    res1 = local_server.trigger_adhoc_sync_task()
    assert res1["success"] is True
    assert res1["status"] == "started"

    # Immediate duplicate trigger while running
    res2 = local_server.trigger_adhoc_sync_task()
    assert res2["success"] is True
    assert res2["status"] == "already_running"

    # Wait for thread to complete
    time.sleep(0.3)
    with local_server._SYNC_LOCK:
        assert local_server._SYNC_STATE["is_running"] is False
        assert local_server._SYNC_STATE["status"] == "completed"
