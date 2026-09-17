"""
test_storage.py — Unit tests for R2 storage, pre-flight quota, and 14-day rolling purge.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import pytest
from storage_r2 import (
    check_preflight_quota,
    purge_expired_local_videos,
)


def test_purge_expired_local_videos(tmp_path, monkeypatch):
    # Set config.VIDEOS_DIR to tmp_path
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path)

    today = datetime.now(timezone.utc).date()
    old_date = today - timedelta(days=16)
    recent_date = today - timedelta(days=5)

    # 1. Stale video file with old date in filename
    stale_file = tmp_path / f"01_creator_{old_date.strftime('%Y-%m-%d')}_abc.mp4"
    stale_file.write_text("dummy video")

    # 2. Fresh video file with recent date in filename
    fresh_file = tmp_path / f"02_creator_{recent_date.strftime('%Y-%m-%d')}_def.mp4"
    fresh_file.write_text("dummy video")

    # 3. File without date in filename, but old mtime
    stale_mtime_file = tmp_path / "old_video.mp4"
    stale_mtime_file.write_text("dummy video")
    old_epoch = time.time() - (20 * 86400)
    os.utime(stale_mtime_file, (old_epoch, old_epoch))

    purged = purge_expired_local_videos(max_age_days=14)

    assert stale_file.name in purged
    assert stale_mtime_file.name in purged
    assert not stale_file.exists()
    assert not stale_mtime_file.exists()
    assert fresh_file.exists()
    assert fresh_file.name not in purged


def test_check_preflight_quota_logic(monkeypatch):
    # Quota is 8 GB: up to 5.8 GB weekly digest + 2.0 GB bookmarks cap + buffer (of 10 GB free).
    import storage_r2
    monkeypatch.setattr(storage_r2, "get_bucket_storage_usage", lambda: (int(7.5 * 1024 * 1024 * 1024), 500))

    # A 1 GB new batch would push it to 8.5 GB (above 8 GB quota) -> should return False
    assert check_preflight_quota(estimated_new_bytes=int(1.0 * 1024 * 1024 * 1024)) is False

    # A 100 MB new batch would be 7.6 GB (under 8 GB quota) -> should return True
    assert check_preflight_quota(estimated_new_bytes=int(100 * 1024 * 1024)) is True

    # When JIT purge runs, bucket only holds bookmarks (<= 2.0 GB). A 5.5 GB batch must pass.
    monkeypatch.setattr(storage_r2, "get_bucket_storage_usage", lambda: (int(2.0 * 1024 * 1024 * 1024), 200))
    assert check_preflight_quota(estimated_new_bytes=int(5.5 * 1024 * 1024 * 1024)) is True
