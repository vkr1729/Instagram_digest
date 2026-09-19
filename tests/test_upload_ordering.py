"""
test_upload_ordering.py — Owner-mandated R2 mutation order for weekly sync.

Order under test (main._run_full_sync, non-dry-run):
  1. Prepare digest locally (download ALL ranked reels, byte budget, drop failures)
  2. Shortfall gate BEFORE any R2 mutation -> checkpoint shortfall_paused, return 2
  3. JIT purge_previous_weeks_videos (scoped: everything under videos/ except
     current week + live week), then current-week stray purge, then
     preflight quota check
  4. Upload to R2
  5. save_digest_batch + build_site (+ deploy)

Asserts:
  (a) shortfall gate fires before any purge/upload call
  (b) purge_previous_weeks_videos is called before the first upload_reel_to_r2
  (c) current-week stray deletion happens before the first upload
  (d) _run_expand never purges (live week safe) — upload-then-rebuild is kept
  (e) stray helper uses reel-id suffix matching scoped to the current week
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
import extractor
import main


SOURCES = [
    {"handle": "alice", "category": "entertainment", "enabled": True},
]


def _cand(rid, handle="alice"):
    return {
        "id": rid,
        "url": f"https://www.instagram.com/reel/{rid}/",
        "creator_handle": handle,
    }


def _real_week_id() -> str:
    return main.datetime.now(main.timezone.utc).strftime("%Y-%m-%d")


def _fake_download_ok(reel_url, dest, video_cdn_url=None):
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(b"fake-video-bytes")
    return True


class _FakeS3:
    """Records delete_objects chunks; returns Deleted confirmations."""

    def __init__(self, events):
        self._events = events

    def delete_objects(self, Bucket, Delete, **kwargs):
        keys = [o["Key"] for o in Delete["Objects"]]
        self._events.append(("s3_delete", tuple(keys)))
        return {"Deleted": [{"Key": k} for k in keys], "Errors": []}


@contextmanager
def _sync_env(tmp_path, monkeypatch, *, top_n=20, min_deploy=12):
    """Isolate filesystem + config for a sync run; caller adds behavior mocks."""
    batch = tmp_path / "top100_digest.json"
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", batch)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "LAST_RUN_FILE", tmp_path / "last_run.json")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "test-account")
    monkeypatch.setattr(config, "TOP_DIGEST_COUNT", top_n)
    monkeypatch.setattr(main, "MIN_DEPLOY_ITEMS", min_deploy)
    (tmp_path / "digests").mkdir(parents=True, exist_ok=True)
    yield batch


def _write_ranked_checkpoint(tmp_path, ranked, stage="ranked"):
    target = tmp_path / f"sync_progress_{_real_week_id()}.json"
    payload = {
        "version": 1, "week_id": _real_week_id(), "days_back": 7,
        "limit_per_creator": 15, "since_timestamp": None,
        "stage": stage, "ranked": ranked,
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _ranked(n, handle="alice"):
    out = []
    for i in range(n):
        r = _cand(f"reel{i:03d}", handle)
        r["rank"] = i + 1
        r["rank_display"] = f"#{i + 1:02d}"
        out.append(r)
    return out


def test_shortfall_gate_fires_before_any_r2_mutation(tmp_path, monkeypatch):
    """(a) playable-download count < MIN -> return 2 with zero R2 mutations."""
    top_n, min_deploy = 20, 12
    with _sync_env(tmp_path, monkeypatch, top_n=top_n, min_deploy=min_deploy) as batch:
        # Healthy live digest so the gate is armed (prev_count >= MIN).
        live = [{"id": f"live{i:03d}", "creator_handle": "alice"} for i in range(min_deploy)]
        batch.write_text(json.dumps({"run_date": "2026-09-10", "items": live}), encoding="utf-8")
        # Bank only 5 reels (< MIN). Stage "ranked" + deficit routes through
        # the shortfall-resume feed top-up, which we mock to find nothing.
        _write_ranked_checkpoint(tmp_path, _ranked(5), stage="ranked")

        events: list = []
        session_mock = MagicMock()
        session_mock.validate.return_value = True

        def _no_feed(*a, **k):
            return []

        with (
            patch.object(extractor, "InstagramSession", return_value=MagicMock(
                __enter__=MagicMock(return_value=session_mock),
                __exit__=MagicMock(return_value=False))),
            patch.object(main, "_ensure_valid_session", return_value=True),
            patch.object(extractor, "load_sources", return_value=[dict(s) for s in SOURCES]),
            patch.object(extractor, "human_pause", lambda *a, **k: None),
            patch.object(extractor, "extract_external_reels_from_feed", side_effect=_no_feed),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch("recommendations.refresh_recommendations", return_value=[]),
            patch.object(main.storage_r2, "get_bucket_storage_usage", return_value=(100, 10)),
            patch.object(main.storage_r2, "purge_previous_weeks_videos",
                         side_effect=lambda **kw: events.append(("purge_previous", kw)) or []),
            patch.object(main.storage_r2, "check_preflight_quota",
                         side_effect=lambda **kw: events.append(("preflight", kw)) or True),
            patch.object(main.storage_r2, "get_existing_r2_keys",
                         side_effect=lambda *a, **k: events.append(("list", a)) or set()),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda *a, **k: events.append(("upload", a)) or "https://r2.example/x.mp4"),
            patch.object(main.storage_r2, "purge_expired_r2_objects",
                         side_effect=lambda **kw: events.append(("purge_expired", kw)) or []),
            patch.object(main.storage_r2, "purge_unreferenced_r2_videos",
                         side_effect=lambda: events.append(("purge_orphan", None)) or []),
            patch.object(main.storage_r2, "purge_expired_local_videos",
                         side_effect=lambda **kw: events.append(("purge_local", kw)) or []),
            patch.object(main, "_purge_current_week_stray_r2_keys",
                         side_effect=lambda *a, **k: events.append(("stray", a)) or []),
            patch.object(main.ranker, "save_digest_batch",
                         side_effect=lambda *a, **k: events.append(("save", None))),
            patch.object(main.site_builder, "build_site",
                         return_value=(Path("r2"), Path("local"))),
        ):
            rc = main.run_full_sync(deploy=False)

        assert rc == 2
        # No R2 mutation of any kind may precede the gate.
        assert events == [], f"shortfall run must not touch R2, got {events!r}"
        # Checkpoint preserved for resume/top-up.
        progress = sorted(tmp_path.glob("sync_progress_*.json"))
        assert len(progress) == 1
        data = json.loads(progress[0].read_text(encoding="utf-8"))
        assert data["stage"] == "shortfall_paused"
        # Live digest untouched.
        assert len(json.loads(batch.read_text(encoding="utf-8"))["items"]) == min_deploy


def test_purge_before_upload_and_stray_before_upload(tmp_path, monkeypatch):
    """(b)+(c) purge_previous + current-week stray purge both precede uploads."""
    top_n, min_deploy = 20, 12
    week = _real_week_id()
    with _sync_env(tmp_path, monkeypatch, top_n=top_n, min_deploy=min_deploy) as batch:
        live = [{"id": f"live{i:03d}", "creator_handle": "alice"} for i in range(min_deploy)]
        batch.write_text(json.dumps({"run_date": "2026-09-10", "items": live}), encoding="utf-8")
        ranked = _ranked(top_n)
        _write_ranked_checkpoint(tmp_path, ranked, stage="ranked")

        events: list = []
        # Simulate the interrupted run: 42 stray partial uploads under the
        # current week plus the keys the final ranked list will own.
        ranked_ids = {r["id"] for r in ranked}
        stray_keys = {f"videos/{week}/99_stray_stray{i:03d}.mp4" for i in range(42)}
        valid_keys = {f"videos/{week}/01_alice_{rid}.mp4" for rid in sorted(ranked_ids)[:3]}
        listing = set(stray_keys) | set(valid_keys)
        fake_s3 = _FakeS3(events)

        def _fake_list(prefix="videos/"):
            events.append(("list", prefix))
            return set(listing)

        def _fake_purge_previous(*, current_week_id, keep_week_ids=None):
            events.append(("purge_previous", current_week_id, set(keep_week_ids or ())))
            return []

        def _fake_upload(local_file, week_id, key_name=None, existing_keys=None):
            events.append(("upload", f"videos/{week_id}/{key_name}"))
            return f"https://r2.example/{week_id}/{key_name}"

        with (
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main, "_ensure_valid_session", return_value=True),
            patch("recommendations.refresh_recommendations", return_value=[]),
            patch.object(main.storage_r2, "get_bucket_storage_usage", return_value=(100, 10)),
            patch.object(main.storage_r2, "purge_previous_weeks_videos", side_effect=_fake_purge_previous),
            patch.object(main.storage_r2, "check_preflight_quota",
                         side_effect=lambda **kw: events.append(("preflight", kw)) or True),
            patch.object(main.storage_r2, "get_existing_r2_keys", side_effect=_fake_list),
            patch.object(main.storage_r2, "get_s3_client", return_value=fake_s3),
            patch.object(main.storage_r2, "upload_reel_to_r2", side_effect=_fake_upload),
            patch.object(main.storage_r2, "purge_expired_r2_objects", return_value=[]),
            patch.object(main.storage_r2, "purge_unreferenced_r2_videos", return_value=[]),
            patch.object(main.storage_r2, "purge_expired_local_videos", return_value=[]),
            patch.object(main.ranker, "save_digest_batch",
                         side_effect=lambda items, run_date=None, **k: events.append(("save", run_date))),
            patch.object(main.site_builder, "build_site",
                         side_effect=lambda **kw: events.append(("build", None)) or (Path("r2"), Path("local"))),
        ):
            rc = main.run_full_sync(deploy=False)

        assert rc == 0
        kinds = [e[0] for e in events]
        assert "purge_previous" in kinds and "upload" in kinds and "s3_delete" in kinds

        first_purge = kinds.index("purge_previous")
        first_upload = kinds.index("upload")
        first_stray_delete = kinds.index("s3_delete")
        # (b) JIT purge of previous weeks runs before any upload.
        assert first_purge < first_upload, f"purge must precede upload: {kinds!r}"
        # (c) stray deletion runs before any upload.
        assert first_stray_delete < first_upload, f"stray purge must precede upload: {kinds!r}"
        # Preflight runs after the purge (quota math sees post-purge usage).
        assert kinds.index("preflight") > first_purge
        # Live week is kept by the JIT purge scoping.
        purge_evt = next(e for e in events if e[0] == "purge_previous")
        assert purge_evt[1] == week
        assert "2026-09-10" in purge_evt[2]
        assert week not in purge_evt[2]  # current week passed as arg, not keep-set
        # All 42 strays deleted; referenced keys never deleted.
        deleted = [k for e in events if e[0] == "s3_delete" for k in e[1]]
        assert set(deleted) == stray_keys
        assert not (set(deleted) & valid_keys)
        # Digest saved + site built after uploads.
        assert kinds.index("save") > first_upload
        assert kinds.index("build") > kinds.index("save")


def test_stray_helper_suffix_match_scoped_to_current_week(tmp_path, monkeypatch):
    """(e) helper deletes only unreferenced .mp4 keys under videos/<week>/."""
    week = "2026-09-18"
    events: list = []
    fake_s3 = _FakeS3(events)
    existing = {
        f"videos/{week}/01_alice_reelAAA.mp4",   # referenced -> keep
        f"videos/{week}/02_bob_stray999.mp4",    # stray -> delete
        f"videos/{week}/notes.txt",              # non-mp4 -> ignore
        "videos/2026-09-10/01_alice_old.mp4",    # other week -> ignore
        "bookmarks/bm1.mp4",                     # bookmarks -> ignore
    }
    monkeypatch.setattr(main.storage_r2, "get_existing_r2_keys", lambda prefix="videos/": set(existing))
    monkeypatch.setattr(main.storage_r2, "get_s3_client", lambda: fake_s3)

    purged = main._purge_current_week_stray_r2_keys(week, {"reelAAA"})
    assert purged == [f"videos/{week}/02_bob_stray999.mp4"]
    deleted = [k for e in events if e[0] == "s3_delete" for k in e[1]]
    assert deleted == [f"videos/{week}/02_bob_stray999.mp4"]


def test_expand_never_purges_live_week(tmp_path, monkeypatch):
    """(d) _run_expand uploads new reels then rebuilds; no purge touches R2."""
    week = "2026-09-10"
    with _sync_env(tmp_path, monkeypatch) as batch:
        existing = [
            {"id": "keep1", "creator_handle": "alice", "rank": 1,
             "r2_url": f"https://r2.example/videos/{week}/01_alice_keep1.mp4",
             "video_url": f"https://r2.example/videos/{week}/01_alice_keep1.mp4"},
            {"id": "keep2", "creator_handle": "bob", "rank": 2,
             "r2_url": f"https://r2.example/videos/{week}/02_bob_keep2.mp4",
             "video_url": f"https://r2.example/videos/{week}/02_bob_keep2.mp4"},
        ]
        batch.write_text(json.dumps({"run_date": week, "items": existing}), encoding="utf-8")
        fresh = [_cand("new1", "cara"), _cand("new2", "cara")]

        events: list = []

        def _fake_download(url, dest, video_cdn_url=None):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"x")
            return True

        with (
            patch.object(extractor, "InstagramSession", return_value=MagicMock(
                __enter__=MagicMock(return_value=MagicMock()),
                __exit__=MagicMock(return_value=False))),
            patch.object(extractor, "load_sources", return_value=[dict(s) for s in SOURCES]),
            patch.object(extractor, "extract_external_reels_from_feed", return_value=list(fresh)),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download),
            patch.object(main.storage_r2, "get_existing_r2_keys", return_value=set()),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: events.append(("upload", key_name)) or f"https://r2.example/{week_id}/{key_name}"),
            patch.object(main.storage_r2, "purge_previous_weeks_videos",
                         side_effect=lambda **kw: events.append(("purge_previous", kw)) or []),
            patch.object(main.storage_r2, "purge_unreferenced_r2_videos",
                         side_effect=lambda: events.append(("purge_orphan", None)) or []),
            patch.object(main.storage_r2, "purge_expired_r2_objects",
                         side_effect=lambda **kw: events.append(("purge_expired", kw)) or []),
            patch.object(main.storage_r2, "purge_expired_local_videos",
                         side_effect=lambda **kw: events.append(("purge_local", kw)) or []),
            patch.object(main.ranker, "save_digest_batch",
                         side_effect=lambda items, run_date=None, **k: events.append(("save", len(items)))),
            patch.object(main.site_builder, "build_site",
                         side_effect=lambda **kw: events.append(("build", None)) or (Path("r2"), Path("local"))),
        ):
            rc = main.run_expand(target_count=2, deploy=False)

        assert rc == 0
        kinds = [e[0] for e in events]
        # Uploads happen, rebuild happens, but NO purge of any kind.
        assert kinds.count("upload") == 2
        assert "build" in kinds
        assert "save" in kinds
        assert not any(k.startswith("purge") for k in kinds), f"expand must never purge: {kinds!r}"
        # Existing live reels preserved in the saved payload week.
        assert ("save", 4) in events
