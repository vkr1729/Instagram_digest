"""
test_expand_resume.py — Focused tests for +100 checkpoint/resume.

A +100 run killed by cookie expiry mid-discovery must not waste the reels it
already found: they are checkpointed to data/expand_checkpoint_<week>.json,
and the next +100 tops up the remainder instead of starting over. No Chrome,
network, or mail is touched.
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


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Pacing/backoff sleeps are real minutes; record instead of waiting."""
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    return calls


def _reel(rid, handle="somecreator"):
    return {"id": rid, "url": f"https://www.instagram.com/reel/{rid}/", "creator_handle": handle}


def _existing(rid, rank):
    return {
        "id": rid,
        "url": f"https://www.instagram.com/reel/{rid}/",
        "creator_handle": "followed",
        "rank": rank,
        "rank_display": f"#{rank:02d}",
        "r2_url": f"https://r2.example/videos/2026-09-12/{rank:02d}_followed_{rid}.mp4",
    }


@contextmanager
def _expand_env(tmp_path, existing):
    """Point run_expand at tmp dirs/files and stub every external side effect."""
    batch = tmp_path / "top100_digest.json"
    batch.write_text(json.dumps({"run_date": "2026-09-12", "items": existing}), encoding="utf-8")
    saved = {}
    with (
        patch.object(config, "DIGEST_BATCH_FILE", batch),
        patch.object(config, "DATA_DIR", tmp_path),
        patch.object(config, "VIDEOS_DIR", tmp_path / "videos"),
        patch.object(extractor, "InstagramSession", return_value=MagicMock(
            __enter__=MagicMock(return_value=MagicMock()),
            __exit__=MagicMock(return_value=False),
        )),
        patch.object(extractor, "load_sources", return_value=[{"handle": "a", "enabled": True}]),
        patch.object(main.ranker, "save_digest_batch",
                     side_effect=lambda items, run_date: saved.setdefault("items", items)),
        patch.object(main.site_builder, "build_site",
                     return_value=(Path("r2_idx"), Path("local_idx"))),
        patch.object(main.storage_r2, "get_existing_r2_keys", return_value=set()),
    ):
        yield saved




def _checkpoint_reels(path):
    """Unwrap the expand-checkpoint envelope to its reel list."""
    return json.loads(Path(path).read_text(encoding="utf-8"))["reels"]


def _fake_download_ok(reel_url, dest, video_cdn_url=None):
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(b"fake-video-bytes")
    return True


def test_cookie_abort_checkpoints_partials_and_sends_alert(tmp_path):
    partials = [_reel(f"new{i:02d}") for i in range(53)]
    with _expand_env(tmp_path, [_existing("old1", 1)]) as saved:
        with (
            patch.object(extractor, "extract_external_reels_from_feed",
                         side_effect=extractor.CookieExpiredException("expired", partial=partials)),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
            patch("notifier.send_cookie_alert_email", return_value=True) as mail,
        ):
            ret = main.run_expand(target_count=100, deploy=False)
    assert ret == 2
    mail.assert_called_once_with()
    assert saved == {}, "aborted run must not touch the digest"
    checkpoints = list(tmp_path.glob("expand_checkpoint_*.json"))
    assert len(checkpoints) == 1
    assert [r["id"] for r in _checkpoint_reels(checkpoints[0])] == [r["id"] for r in partials]


def test_retry_tops_up_from_checkpoint_and_clears_it(tmp_path):
    resumed = [_reel(f"new{i:02d}") for i in range(2)]
    real_today = main.datetime.now(main.timezone.utc).strftime("%Y-%m-%d")
    (tmp_path / f"expand_checkpoint_{real_today}.json").write_text(
        json.dumps({"version": 1, "target_count": 5, "reels": resumed}), encoding="utf-8")
    (tmp_path / "expand_checkpoint_2099-01-01.json").write_text(
        json.dumps({"version": 1, "target_count": 5, "reels": [_reel("stale")]}), encoding="utf-8")
    fresh = [_reel(f"top{i:02d}") for i in range(3)]
    seen = {}

    def _discover(*, session, target_count, existing_ids, active_sources, on_progress=None):
        seen["target_count"] = target_count
        seen["existing_ids"] = set(existing_ids)
        return fresh

    with _expand_env(tmp_path, [_existing("old1", 1)]) as saved:
        with (
            patch.object(extractor, "extract_external_reels_from_feed", side_effect=_discover),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            ret = main.run_expand(target_count=5, deploy=False)
    assert ret == 0
    # Only the remainder is discovered, and resumed ids are excluded up front.
    assert seen["target_count"] == 3
    assert {"new00", "new01"} <= seen["existing_ids"]
    # Stale week checkpoint pruned; fulfilled checkpoint cleared.
    assert list(tmp_path.glob("expand_checkpoint_*.json")) == []
    assert (tmp_path / f"expand_checkpoint_{real_today}.json").exists() is False
    saved_ids = [r["id"] for r in saved["items"]]
    assert saved_ids[0] == "old1"
    assert sorted(saved_ids[1:]) == ["new00", "new01", "top00", "top01", "top02"]


def test_cookie_exception_carries_partials_by_default():
    exc = extractor.CookieExpiredException("boom")
    assert exc.partial == []
    assert "boom" in str(exc)


def test_internet_drop_salvages_progress_snapshot_without_cookie_mail(tmp_path):
    """A non-cookie discovery failure checkpoints the last progress snapshot
    (no exception partials needed) and sends no cookie email."""
    snapshots = [[_reel(f"net{i:02d}") for i in range(30)]]

    def _flaky_discover(*, session, target_count, existing_ids, active_sources, on_progress=None):
        on_progress(list(snapshots[0]))
        raise RuntimeError("net down")

    with _expand_env(tmp_path, [_existing("old1", 1)]) as saved:
        with (
            patch.object(extractor, "extract_external_reels_from_feed", side_effect=_flaky_discover),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
            patch("notifier.send_cookie_alert_email", return_value=True) as mail,
        ):
            ret = main.run_expand(target_count=100, deploy=False)
    assert ret == 2
    mail.assert_not_called()
    assert saved == {}
    checkpoints = list(tmp_path.glob("expand_checkpoint_*.json"))
    assert len(checkpoints) == 1
    assert len(_checkpoint_reels(checkpoints[0])) == 30


class _Crash(BaseException):
    """Simulates SIGKILL/power-loss: bypasses `except Exception` like death."""


def test_crash_during_downloads_keeps_full_checkpoint(tmp_path):
    found = [_reel(f"c{i:02d}") for i in range(5)]

    def _die_on_download(reel_url, dest, video_cdn_url=None):
        raise _Crash()

    with _expand_env(tmp_path, [_existing("old1", 1)]):
        with (
            patch.object(extractor, "extract_external_reels_from_feed", return_value=list(found)),
            patch.object(extractor, "download_reel_video", side_effect=_die_on_download),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            try:
                main.run_expand(target_count=5, deploy=False)
            except _Crash:
                pass
            else:
                raise AssertionError("expected the simulated crash to propagate")
    checkpoints = list(tmp_path.glob("expand_checkpoint_*.json"))
    assert len(checkpoints) == 1
    assert [r["id"] for r in _checkpoint_reels(checkpoints[0])] == [r["id"] for r in found]


def test_failed_downloads_stay_checkpointed_for_retry(tmp_path):
    found = [_reel(f"d{i:02d}") for i in range(5)]

    def _flaky_download(reel_url, dest, video_cdn_url=None):
        if "d03" in reel_url or "d04" in reel_url:
            return False
        return _fake_download_ok(reel_url, dest, video_cdn_url)

    with _expand_env(tmp_path, [_existing("old1", 1)]) as saved:
        with (
            patch.object(extractor, "extract_external_reels_from_feed", return_value=list(found)),
            patch.object(extractor, "download_reel_video", side_effect=_flaky_download),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            ret = main.run_expand(target_count=5, deploy=False)
    assert ret == 0
    assert sorted(r["id"] for r in saved["items"][1:]) == ["d00", "d01", "d02"]
    checkpoints = list(tmp_path.glob("expand_checkpoint_*.json"))
    assert len(checkpoints) == 1
    assert sorted(r["id"] for r in _checkpoint_reels(checkpoints[0])) == ["d03", "d04"]


def test_old_week_checkpoint_is_picked_up_and_migrated(tmp_path):
    (tmp_path / "expand_checkpoint_2000-01-01.json").write_text(
        json.dumps({"version": 1, "target_count": 2, "reels": [_reel("ancient")]}), encoding="utf-8")
    seen = {}

    def _discover(*, session, target_count, existing_ids, active_sources, on_progress=None):
        seen["target_count"] = target_count
        return [_reel("brandnew")]

    with _expand_env(tmp_path, [_existing("old1", 1)]) as saved:
        with (
            patch.object(extractor, "extract_external_reels_from_feed", side_effect=_discover),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            ret = main.run_expand(target_count=2, deploy=False)
    assert ret == 0
    assert seen["target_count"] == 1  # 1 resumed + 1 fresh = target 2
    assert sorted(r["id"] for r in saved["items"][1:]) == ["ancient", "brandnew"]
    assert list(tmp_path.glob("expand_checkpoint_*.json")) == []


def test_legacy_bare_list_checkpoint_still_resumes(tmp_path):
    (tmp_path / "expand_checkpoint_2000-01-01.json").write_text(
        json.dumps([_reel("legacy")]), encoding="utf-8")
    seen = {}

    def _discover(*, session, target_count, existing_ids, active_sources, on_progress=None):
        seen["target_count"] = target_count
        return []

    with _expand_env(tmp_path, [_existing("old1", 1)]):
        with (
            patch.object(extractor, "extract_external_reels_from_feed", side_effect=_discover),
            patch.object(extractor, "download_reel_video", side_effect=_fake_download_ok),
            patch.object(main.storage_r2, "upload_reel_to_r2",
                         side_effect=lambda p, week_id, key_name, existing_keys: f"https://r2.example/{key_name}"),
        ):
            ret = main.run_expand(target_count=2, deploy=False)
    assert ret == 0
    assert seen["target_count"] == 1  # 1 legacy reel resumed, 1 fresh (none found)
