"""
test_sync_resume.py — Focused tests for weekly-sync staged resume.

A weekly sync killed mid-extraction (block), mid-downloads (crash), or with
changed parameters must respectively: bank progress and skip visited creators
on retry; jump straight to downloads; or ignore the banked work. Dry runs
never touch progress. No browser, network, R2, or mail is touched.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
import extractor
import main


class _Crash(BaseException):
    """Simulates SIGKILL/power-loss: bypasses `except Exception` like death."""


SOURCES = [
    {"handle": "alice", "category": "entertainment", "enabled": True},
    {"handle": "bob", "category": "finance", "enabled": True},
    {"handle": "cara", "category": "ai_tech", "enabled": True},
]


def _cand(rid, handle="alice"):
    return {"id": rid, "url": f"https://www.instagram.com/reel/{rid}/", "creator_handle": handle}


def _real_week_id():
    return main.datetime.now(main.timezone.utc).strftime("%Y-%m-%d")


@contextmanager
def _sync_env(tmp_path):
    batch = tmp_path / "top100_digest.json"
    batch.write_text(json.dumps({"run_date": "2026-09-12", "items": []}), encoding="utf-8")
    saved = {}
    session_mock = MagicMock()
    session_mock.validate.return_value = True
    with (
        patch.object(config, "DIGEST_BATCH_FILE", batch),
        patch.object(config, "DATA_DIR", tmp_path),
        patch.object(config, "VIDEOS_DIR", tmp_path / "videos"),
        patch.object(config, "LAST_RUN_FILE", tmp_path / "last_run.json"),
        patch.object(config, "R2_ACCOUNT_ID", ""),
        patch.object(extractor, "InstagramSession", return_value=MagicMock(
            __enter__=MagicMock(return_value=session_mock),
            __exit__=MagicMock(return_value=False),
        )),
        patch.object(extractor, "load_sources", return_value=[dict(s) for s in SOURCES]),
        patch.object(extractor, "human_pause", lambda *a, **k: None),
        patch.object(main.ranker, "rank_top_reels",
                     side_effect=lambda **kw: list(kw.get("candidates", []))[:12]),
        patch.object(extractor, "extract_external_reels_from_feed", return_value=[]),
        patch.object(main.storage_r2, "purge_expired_r2_objects", return_value=None),
        patch.object(main.storage_r2, "purge_unreferenced_r2_videos", return_value=None),
        patch.object(main.storage_r2, "purge_expired_local_videos", return_value=None),
        patch.object(main.storage_r2, "get_existing_r2_keys", return_value=set()),
        patch.object(main.site_builder, "build_site",
                     return_value=(Path("r2_idx"), Path("local_idx"))),
        patch("notifier.send_digest_email", return_value=True),
        patch("notifier.send_failure_alert_email", return_value=True),
        patch("notifier.send_cookie_alert_email", return_value=True),
    ):
        yield saved


def _fake_download_ok(reel_url, dest, video_cdn_url=None):
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(b"fake-video-bytes")
    return True


def _progress_files(tmp_path):
    return sorted(tmp_path.glob("sync_progress_*.json"))


def test_block_abort_banks_progress_and_retry_skips_visited(tmp_path):
    calls = []

    def _extract_first(handle, **kw):
        calls.append(handle)
        if handle == "alice":
            return [_cand("a1", "alice"), _cand("a2", "alice")]
        raise extractor.InstagramBlocked("walled")

    with _sync_env(tmp_path):
        with (
            patch.object(extractor, "extract_creator_reels", side_effect=_extract_first),
            patch.object(extractor, "extract_single_reel_metadata", return_value=None),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            assert main.run_full_sync(deploy=False) == 2

    progress = _progress_files(tmp_path)
    assert len(progress) == 1
    data = json.loads(progress[0].read_text(encoding="utf-8"))
    assert data["stage"] == "extracting"
    assert data["done"] == {"alice": False}
    assert sorted(r["id"] for r in data["candidates"]) == ["a1", "a2"]
    assert not (tmp_path / "candidates_cache.json").exists()

    # Retry: alice must not be revisited; enrichment finds nothing -> abort,
    # and the hopeless run clears its own progress.
    calls.clear()

    def _extract_retry(handle, **kw):
        calls.append(handle)
        return [_cand(f"{handle}-1", handle), _cand(f"{handle}-2", handle),
                _cand(f"{handle}-3", handle)]

    with _sync_env(tmp_path):
        with (
            patch.object(extractor, "extract_creator_reels", side_effect=_extract_retry),
            patch.object(extractor, "extract_single_reel_metadata", return_value=None),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            assert main.run_full_sync(deploy=False) == 2
    assert sorted(calls) == ["bob", "cara"]
    assert _progress_files(tmp_path) == []


def test_crash_after_ranking_resumes_at_downloads(tmp_path):
    want_ids = sorted(f"{h}-{i}" for h in ("alice", "bob", "cara") for i in (1, 2, 3))

    def _extract_three(handle, **kw):
        return [_cand(f"{handle}-{i}", handle) for i in (1, 2, 3)]

    def _enrich(r, session=None):
        return {**r, "timestamp": int(time.time()), "like_count": 30000}

    def _die_on_download(reel_url, dest, video_cdn_url=None):
        raise _Crash()

    with _sync_env(tmp_path):
        with (
            patch.object(extractor, "extract_creator_reels", side_effect=_extract_three),
            patch.object(extractor, "extract_single_reel_metadata", side_effect=_enrich),
            patch.object(extractor, "download_reel_video", side_effect=_die_on_download),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            try:
                main.run_full_sync(deploy=False)
            except _Crash:
                pass
            else:
                raise AssertionError("expected the simulated crash to propagate")

    progress = _progress_files(tmp_path)
    assert len(progress) == 1
    data = json.loads(progress[0].read_text(encoding="utf-8"))
    assert data["stage"] == "ranked"
    assert sorted(r["id"] for r in data["ranked"]) == want_ids

    # Resume jumps straight to downloads and completes.
    with _sync_env(tmp_path):
        with (
            patch.object(extractor, "extract_creator_reels",
                         side_effect=AssertionError("extraction must be skipped")),
            patch.object(extractor, "extract_single_reel_metadata",
                         side_effect=AssertionError("enrichment must be skipped")),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            assert main.run_full_sync(deploy=False) == 0
    assert _progress_files(tmp_path) == []
    digest = json.loads((tmp_path / "top100_digest.json").read_text(encoding="utf-8"))
    assert sorted(r["id"] for r in digest["items"]) == want_ids


def test_params_mismatch_starts_new_operation(tmp_path):
    staged = {
        "version": 1, "week_id": _real_week_id(), "days_back": 7,
        "limit_per_creator": 999, "since_timestamp": None,
        "stage": "extracting", "done": {"alice": False},
        "candidates": [_cand("stale")], "extraction_complete": False,
    }
    target = tmp_path / f"sync_progress_{_real_week_id()}.json"
    target.write_text(json.dumps(staged), encoding="utf-8")
    calls = []
    seen_progress = {}

    def _extract(handle, **kw):
        calls.append(handle)
        return [_cand(f"{handle}-1", handle), _cand(f"{handle}-2", handle)]

    def _observe_then_none(r, session=None):
        # Enrichment runs after the new operation banks extraction progress.
        if "took_over" not in seen_progress and target.exists():
            seen_progress["took_over"] = json.loads(target.read_text(encoding="utf-8"))
        return None

    with _sync_env(tmp_path):
        with (
            patch.object(extractor, "extract_creator_reels", side_effect=_extract),
            patch.object(extractor, "extract_single_reel_metadata", side_effect=_observe_then_none),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            main.run_full_sync(deploy=False, limit_per_creator=3)
    assert sorted(calls) == ["alice", "bob", "cara"]
    took_over = seen_progress["took_over"]
    assert took_over["limit_per_creator"] == 3  # new operation banked its own progress
    assert took_over["extraction_complete"] is True
    assert sorted(took_over["done"]) == ["alice", "bob", "cara"]


def test_dry_run_leaves_progress_untouched(tmp_path):
    staged = {
        "version": 1, "week_id": _real_week_id(), "days_back": 7,
        "limit_per_creator": 15, "since_timestamp": None,
        "stage": "extracting", "done": {"alice": False},
        "candidates": [_cand("a1", "alice"), _cand("a2", "alice")],
        "extraction_complete": False,
    }
    target = tmp_path / f"sync_progress_{_real_week_id()}.json"
    before = json.dumps(staged, sort_keys=True)
    target.write_text(before, encoding="utf-8")
    calls = []

    def _extract(handle, **kw):
        calls.append(handle)
        return [_cand(f"{handle}-1", handle), _cand(f"{handle}-2", handle),
                _cand(f"{handle}-3", handle)]

    with _sync_env(tmp_path):
        with (
            patch.object(extractor, "extract_creator_reels", side_effect=_extract),
            patch.object(extractor, "extract_single_reel_metadata", return_value=None),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            main.run_full_sync(deploy=False, dry_run=True)
    assert sorted(calls) == ["alice", "bob", "cara"]
    assert target.read_text(encoding="utf-8") == before
