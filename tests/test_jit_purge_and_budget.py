"""
test_jit_purge_and_budget.py — Tests for Option A JIT pre-upload purge and 5.8GB feed byte-budget guard.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import config
import storage_r2


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, Bucket, Prefix=None, **kwargs):
        return self._pages


class _FakeS3:
    def __init__(self, pages):
        self._pages = pages
        self.deleted = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakePaginator(self._pages)

    def delete_objects(self, Bucket, Delete, **kwargs):
        keys = [o["Key"] for o in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Deleted": [{"Key": k} for k in keys], "Errors": []}


def _obj(key):
    return {"Key": key, "Size": 15 * 1024 * 1024, "LastModified": datetime.now(timezone.utc)}


def test_jit_purge_previous_weeks_preserves_current_and_bookmarks(monkeypatch):
    current_week = "2026-09-17"
    pages = [
        {"Contents": [
            _obj("videos/2026-09-10/01_alice_abc.mp4"),
            _obj("videos/2026-09-10/02_bob_def.mp4"),
            _obj(f"videos/{current_week}/01_current_keep.mp4"),
            _obj("bookmarks/bm123.mp4"),
            _obj("bookmarks/bm123_portrait.jpg"),
            _obj("bookmarks/manifest.json"),
        ]}
    ]
    fake = _FakeS3(pages)
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: fake)

    purged = storage_r2.purge_previous_weeks_videos(current_week_id=current_week)

    # Previous week files must be purged
    assert "videos/2026-09-10/01_alice_abc.mp4" in purged
    assert "videos/2026-09-10/02_bob_def.mp4" in purged

    # Current week and bookmarks must NEVER be purged
    assert f"videos/{current_week}/01_current_keep.mp4" not in purged
    assert all(not k.startswith(f"videos/{current_week}/") for k in fake.deleted)
    assert all(not k.startswith("bookmarks/") for k in fake.deleted)


def test_jit_purge_empty_week_guard(monkeypatch):
    fake = _FakeS3([])
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: fake)
    assert storage_r2.purge_previous_weeks_videos(current_week_id="") == []
    assert len(fake.deleted) == 0


def test_feed_batch_byte_budget_constant():
    assert hasattr(config, "MAX_FEED_BATCH_BYTES")
    assert config.MAX_FEED_BATCH_BYTES == int(5.8 * 1024 * 1024 * 1024)
    assert config.R2_STORAGE_QUOTA_BYTES == 8 * 1024 * 1024 * 1024


def test_worker_bookmark_cap_is_2gb():
    worker_js = (Path(__file__).resolve().parent.parent / "cloudflare" / "worker.js").read_text()
    assert "const MAX_BYTES = 2.0 * 1024 * 1024 * 1024;" in worker_js
    assert "3.5 * 1024 * 1024 * 1024" not in worker_js


def test_preflight_quota_with_jit_2gb_bookmarks_and_5_8gb_feed(monkeypatch):
    # Bookmarks at full 2.0 GB cap
    monkeypatch.setattr(
        storage_r2, "get_bucket_storage_usage",
        lambda: (int(2.0 * 1024 * 1024 * 1024), 200),
    )
    # A full 5.8 GB incoming feed batch must pass under the 8.0 GB quota (2.0 + 5.8 = 7.8 GB < 8.0 GB)
    assert storage_r2.check_preflight_quota(
        estimated_new_bytes=int(5.8 * 1024 * 1024 * 1024)
    ) is True

    # A 6.1 GB incoming feed batch would exceed the 8.0 GB quota (2.0 + 6.1 = 8.1 GB >= 8.0 GB) -> fails
    assert storage_r2.check_preflight_quota(
        estimated_new_bytes=int(6.1 * 1024 * 1024 * 1024)
    ) is False
