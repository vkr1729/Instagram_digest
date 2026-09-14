"""
test_bookmarks_isolation.py — the weekly R2 purgers must never touch bookmarks/.

Bookmarks live under the `bookmarks/` prefix and are managed exclusively by the
Cloudflare Worker/D1 Dual-Safety Cap. These tests use a fake S3 client so no
credentials or network are needed.
"""

from datetime import datetime, timedelta, timezone

import config
import storage_r2

OLD_DT = datetime.now(timezone.utc) - timedelta(days=30)


class _FakePaginator:
    def __init__(self, fake):
        self._fake = fake

    def paginate(self, Bucket, Prefix=None, **kwargs):
        self._fake.prefixes.append(Prefix)
        return self._fake.pages


class _FakeS3:
    def __init__(self, pages):
        self.pages = pages
        self.prefixes = []
        self.deleted = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakePaginator(self)

    def delete_objects(self, Bucket, Delete, **kwargs):
        keys = [o["Key"] for o in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Deleted": [{"Key": k} for k in keys], "Errors": []}


def _obj(key, last_modified=OLD_DT):
    return {"Key": key, "Size": 10 * 1024 * 1024, "LastModified": last_modified}


def test_expired_purge_never_touches_bookmarks(monkeypatch):
    pages = [
        {"Contents": [
            _obj("videos/2020-01-01/01_alice_abc.mp4"),
            _obj("bookmarks/someid.mp4"),
            _obj("bookmarks/someid_portrait.jpg"),
            _obj("bookmarks/manifest.json"),
        ]}
    ]
    fake = _FakeS3(pages)
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: fake)

    purged = storage_r2.purge_expired_r2_objects(max_age_days=8)

    assert purged == ["videos/2020-01-01/01_alice_abc.mp4"]
    assert all(p == "videos/" or p.startswith("videos/") for p in fake.prefixes)
    assert all(not k.startswith("bookmarks/") for k in fake.deleted)


def test_unreferenced_purge_never_touches_bookmarks(monkeypatch, tmp_path):
    week = "2026-09-11"
    digest = {"run_date": week, "items": [{"id": "kept123"}]}
    (tmp_path / f"{week}.json").write_text(
        __import__("json").dumps(digest), encoding="utf-8"
    )
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path)
    monkeypatch.setattr(
        config, "DIGEST_BATCH_FILE", tmp_path / "no_such_batch.json"
    )

    pages = [
        {"Contents": [
            _obj(f"videos/{week}/01_alice_kept123.mp4"),
            _obj(f"videos/{week}/02_bob_orphan999.mp4"),
            _obj("bookmarks/orphan999.mp4"),
        ]}
    ]
    fake = _FakeS3(pages)
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: fake)

    purged = storage_r2.purge_unreferenced_r2_videos()

    assert purged == [f"videos/{week}/02_bob_orphan999.mp4"]
    assert fake.prefixes, "expected at least one R2 listing"
    assert all(p.startswith("videos/") for p in fake.prefixes)
    assert all(not k.startswith("bookmarks/") for k in fake.deleted)


def test_preflight_passes_at_design_peak(monkeypatch):
    # ~3.1 GB weekly + 3.5 GB bookmarks + 1 GB incoming must clear the 8 GB ceiling.
    monkeypatch.setattr(
        storage_r2, "get_bucket_storage_usage",
        lambda: (int(6.6 * 1024 * 1024 * 1024), 600),
    )
    assert storage_r2.check_preflight_quota(
        estimated_new_bytes=int(1.0 * 1024 * 1024 * 1024)
    ) is True
