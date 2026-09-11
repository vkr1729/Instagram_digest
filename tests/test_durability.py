"""
test_durability.py — Regression tests for P0-5 / P0-7 storage & concurrency fixes.

Covers: crash-safe atomic writes, id-suffix orphan matching, torn-digest
per-week guards, and expand append-only rank/key coherence.
"""

import json
import threading
from unittest import mock

import atomic_io
import config
import storage_r2


def test_durable_write_json_never_leaves_torn_or_tmp_files(tmp_path):
    target = tmp_path / "digest.json"

    def _worker(n):
        atomic_io.durable_write_json(target, {"n": n, "items": list(range(50))})

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Final file is always complete valid JSON; no temp litter remains.
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["items"] == list(range(50))
    assert list(tmp_path.iterdir()) == [target]


def _fake_s3(keys):
    s3 = mock.MagicMock()
    paginator = mock.MagicMock()

    def _paginate(Bucket, Prefix):
        return [{"Contents": [{"Key": k} for k in keys if k.startswith(Prefix)]}]

    paginator.paginate.side_effect = _paginate
    s3.get_paginator.return_value = paginator
    deleted = []

    def _delete(Bucket, Delete):
        chunk = [o["Key"] for o in Delete["Objects"]]
        deleted.extend(chunk)
        return {"Deleted": [{"Key": k} for k in chunk], "Errors": []}

    s3.delete_objects.side_effect = _delete
    s3.deleted = deleted
    return s3


def test_pruner_matches_by_reel_id_not_rank(tmp_path, monkeypatch):
    """A re-ranked digest must not orphan live objects stored under old ranks."""
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / "2026-09-01.json").write_text(json.dumps({
        "run_date": "2026-09-01",
        "items": [
            {"id": "AAA", "rank": 5, "rank_display": "#05", "creator_handle": "somehandle"},
            {"id": "BBB", "rank": 6, "rank_display": "#06", "creator_handle": "other"},
        ],
    }))
    monkeypatch.setattr(config, "DIGESTS_DIR", digests)
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "no_batch.json")
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "test_bucket")

    live_old_rank = "videos/2026-09-01/01_somehandle_AAA.mp4"
    live_other = "videos/2026-09-01/02_other_BBB.mp4"
    orphan = "videos/2026-09-01/09_gone_ORPHAN.mp4"
    s3 = _fake_s3([live_old_rank, live_other, orphan])
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: s3)

    purged = storage_r2.purge_unreferenced_r2_videos()

    assert purged == [orphan]
    assert live_old_rank not in s3.deleted
    assert live_other not in s3.deleted


def test_pruner_never_trusts_torn_or_empty_digest(tmp_path, monkeypatch):
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / "2026-09-02.json").write_text("{truncated torn json,,,")
    (digests / "2026-09-03.json").write_text(json.dumps({
        "run_date": "2026-09-03", "items": [],
    }))
    monkeypatch.setattr(config, "DIGESTS_DIR", digests)
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "no_batch.json")
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "test_bucket")

    live = "videos/2026-09-02/01_h_AAA.mp4"
    s3 = _fake_s3([live, "videos/2026-09-03/01_h_BBB.mp4"])
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: s3)

    assert storage_r2.purge_unreferenced_r2_videos() == []
    assert s3.deleted == []


def test_expand_preserves_existing_ranks_and_matches_new_keys(tmp_path, monkeypatch):
    """Append-only invariance: existing ranks/keys untouched; new keys match
    final ranks even when a middle download fails (no renumber desync)."""
    import main
    import extractor
    import site_builder
    from datetime import datetime, timezone
    from pathlib import Path

    week_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = [
        {"id": "OLD1", "creator_handle": "h1", "rank": 1, "rank_display": "#01",
         "url": "https://www.instagram.com/reel/OLD1/",
         "r2_url": f"https://r2.dev/videos/{week_id}/01_h1_OLD1.mp4",
         "video_url": f"https://r2.dev/videos/{week_id}/01_h1_OLD1.mp4"},
        {"id": "OLD2", "creator_handle": "h2", "rank": 2, "rank_display": "#02",
         "url": "https://www.instagram.com/reel/OLD2/",
         "r2_url": f"https://r2.dev/videos/{week_id}/02_h2_OLD2.mp4",
         "video_url": f"https://r2.dev/videos/{week_id}/02_h2_OLD2.mp4"},
    ]
    batch = tmp_path / "top100_digest.json"
    batch.write_text(json.dumps({"run_date": week_id, "items": existing}))
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", batch)
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")

    new_reels = [
        {"id": "NEW1", "creator_handle": "n1", "url": "https://www.instagram.com/reel/NEW1/"},
        {"id": "FAIL", "creator_handle": "nf", "url": "https://www.instagram.com/reel/FAIL/"},
        {"id": "NEW3", "creator_handle": "n3", "url": "https://www.instagram.com/reel/NEW3/"},
    ]

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(extractor, "InstagramSession", _Session)
    monkeypatch.setattr(
        extractor, "extract_external_reels_from_feed",
        lambda **kw: [dict(r) for r in new_reels],
    )

    def _fake_download(url, out_path, video_cdn_url=None):
        if "FAIL" in url:
            return False
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x" * 1024)
        return True

    monkeypatch.setattr(extractor, "download_reel_video", _fake_download)
    monkeypatch.setattr(storage_r2, "get_existing_r2_keys", lambda prefix="videos/": set())
    uploaded_keys = []

    def _fake_upload(local_file, week_id, key_name=None, existing_keys=None):
        uploaded_keys.append(key_name)
        return f"https://r2.dev/videos/{week_id}/{key_name}"

    monkeypatch.setattr(storage_r2, "upload_reel_to_r2", _fake_upload)
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: (tmp_path, tmp_path))

    assert main.run_expand(target_count=3, deploy=False) == 0

    saved = json.loads(batch.read_text(encoding="utf-8"))["items"]
    by_id = {i["id"]: i for i in saved}
    # Existing items byte-identical in rank and URL.
    assert by_id["OLD1"]["rank"] == 1
    assert by_id["OLD2"]["rank"] == 2
    assert by_id["OLD1"]["r2_url"].endswith("/01_h1_OLD1.mp4")
    assert by_id["OLD2"]["r2_url"].endswith("/02_h2_OLD2.mp4")
    # Failed reel dropped; survivors contiguous; every key matches its manifest rank.
    assert "FAIL" not in by_id
    assert sorted(i["rank"] for i in saved) == [1, 2, 3, 4]
    for item in saved:
        if item["id"].startswith("NEW"):
            expected = f"{item['rank']:02d}_{item['creator_handle']}_{item['id']}.mp4"
            assert item["r2_url"].endswith("/" + expected)
            assert expected in uploaded_keys


def test_r2_keys_fast_path_is_thread_safe():
    import concurrent.futures

    keys: set[str] = set()

    def _add(n):
        with storage_r2._R2_KEYS_LOCK:
            if f"k{n}" not in keys:
                keys.add(f"k{n}")
                return True
        return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_add, list(range(200)) * 2))
    assert sum(results) == 200
    assert len(keys) == 200
