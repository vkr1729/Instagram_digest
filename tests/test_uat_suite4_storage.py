"""
test_uat_suite4_storage.py — Automated tests for Suite 4: Storage, Quota Guard, & Purge.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
import config
import storage_r2


def test_uat_4_2_preflight_quota_guard(monkeypatch):
    """UAT-4.2: Pre-flight check rejects batches that would exceed 5 GB quota."""
    # Mock current usage as 4.8 GB
    usage_bytes = int(4.8 * 1024 * 1024 * 1024)
    monkeypatch.setattr(storage_r2, "get_bucket_storage_usage", lambda: (usage_bytes, 100))

    # A 300 MB batch pushes it to 5.1 GB -> must abort
    new_batch_bytes = int(300 * 1024 * 1024)
    assert storage_r2.check_preflight_quota(new_batch_bytes) is False

    # A 50 MB batch stays at 4.85 GB -> allowed
    small_batch_bytes = int(50 * 1024 * 1024)
    assert storage_r2.check_preflight_quota(small_batch_bytes) is True


def test_uat_4_4_local_14day_purge(tmp_path, monkeypatch):
    """UAT-4.4: 14-Day Rolling Purge on local filesystem."""
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path)

    today = datetime.now(timezone.utc).date()
    stale_date = today - timedelta(days=15)
    fresh_date = today - timedelta(days=7)

    stale_file = tmp_path / f"video_{stale_date.strftime('%Y-%m-%d')}.mp4"
    fresh_file = tmp_path / f"video_{fresh_date.strftime('%Y-%m-%d')}.mp4"

    stale_file.write_text("old")
    fresh_file.write_text("new")

    purged = storage_r2.purge_expired_local_videos(max_age_days=14)

    assert stale_file.name in purged
    assert not stale_file.exists()
    assert fresh_file.exists()


def test_uat_4_6_local_fallback_mode(tmp_path, monkeypatch):
    """UAT-4.6: If R2 is unconfigured or fails, returns local streamable path."""
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "R2_PUBLIC_DOMAIN", "")

    dummy_video = tmp_path / "test_reel.mp4"
    dummy_video.write_text("data")

    url = storage_r2.upload_reel_to_r2(dummy_video, week_id="2026-09-06", key_name="test_reel.mp4")
    assert url == "/videos/2026-09-06/test_reel.mp4"
