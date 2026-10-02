"""
test_stalled_job_safeguards.py — Unit tests for stalled job and testing safeguards.

Verifies:
1. Sync progress TTL rejection (> 3 hours).
2. Expand checkpoint TTL discard (> 3 hours).
3. Transient checkpoint cleanup on KeyboardInterrupt.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
import extractor
import main


def _ckpt(stage="ranked", limit_per_creator=15, since_timestamp=1000,
          cookie_free=False, kind="weekly", saved_at=None):
    payload = {
        "version": 1,
        "week_id": "2026-10-02",
        "stage": stage,
        "limit_per_creator": limit_per_creator,
        "since_timestamp": since_timestamp,
        "kind": kind,
        "cookie_free": cookie_free,
        "ranked": [{"id": "r1", "creator_handle": "alice", "url": "https://instagram.com/reel/r1/"}],
    }
    if saved_at is not None:
        payload["saved_at"] = saved_at
    return payload


def test_sync_progress_ttl_rejection():
    # Fresh within 10 hours: usable (default TTL 24h)
    fresh_ckpt = _ckpt(saved_at=time.time() - (10 * 3600))
    assert main._sync_progress_usable(fresh_ckpt, 15, 1000, True, 0, kind="weekly") is True

    # Stale: 26 hours old (> 24.0h TTL): rejected
    stale_ckpt = _ckpt(saved_at=time.time() - (26 * 3600))
    assert main._sync_progress_usable(stale_ckpt, 15, 1000, True, 0, kind="weekly") is False

    # Explicit banked_age_hours argument
    assert main._sync_progress_usable(_ckpt(), 15, 1000, True, 0, kind="weekly", banked_age_hours=25.0) is False
    assert main._sync_progress_usable(_ckpt(), 15, 1000, True, 0, kind="weekly", banked_age_hours=2.0) is True


def test_expand_checkpoint_ttl_discard(tmp_path):
    stale_file = tmp_path / "expand_checkpoint_2026-09-26.json"
    stale_file.write_text(json.dumps({
        "version": 1,
        "target_count": 3,
        "saved_at": time.time() - (28 * 3600),  # 28 hours old (> 24h TTL)
        "reels": [{"id": "FAIL", "creator_handle": "nf", "url": "https://instagram.com/reel/FAIL/"}],
    }), encoding="utf-8")

    existing = [{
        "id": "old1",
        "url": "https://www.instagram.com/reel/old1/",
        "creator_handle": "followed",
        "rank": 1,
        "rank_display": "#01",
        "r2_url": "https://r2.example/videos/2026-10-02/01_followed_old1.mp4",
    }]
    batch = tmp_path / "top100_digest.json"
    batch.write_text(json.dumps({"run_date": "2026-10-02", "items": existing}), encoding="utf-8")

    def _fake_download(reel_url, dest, video_cdn_url=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"bytes")
        return True

    fresh = [{"id": "fresh1", "url": "https://instagram.com/reel/fresh1/", "creator_handle": "alice"}]
    seen = {}
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
                     side_effect=lambda items, run_date: seen.setdefault("items", items)),
        patch.object(main.site_builder, "build_site", return_value=(Path("a"), Path("b"))),
        patch.object(main.storage_r2, "get_existing_r2_keys", return_value=set()),
        patch.object(main.storage_r2, "upload_reel_to_r2", return_value="https://r2.example/fresh1.mp4"),
        patch.object(extractor, "download_reel_video", side_effect=_fake_download),
        patch.object(extractor, "extract_external_reels_from_feed", return_value=fresh),
    ):
        ret = main.run_expand(target_count=1, deploy=False)

    assert ret == 0
    # Stale checkpoint must have been retired (no longer active expand_checkpoint_*.json)
    assert stale_file.exists() is False
    # None of the stale "FAIL" reels should be included
    assert "FAIL" not in [r.get("id") for r in seen.get("items", [])]
    assert "fresh1" in [r.get("id") for r in seen.get("items", [])]


def test_cleanup_transient_checkpoints(tmp_path):
    with patch.object(config, "DATA_DIR", tmp_path):
        week = "2026-10-02"
        f1 = tmp_path / f"sync_progress_{week}.json"
        f2 = tmp_path / f"expand_checkpoint_{week}.json"
        f3 = tmp_path / f"expand_progress_{week}.json"
        f1.write_text("{}", encoding="utf-8")
        f2.write_text("{}", encoding="utf-8")
        f3.write_text("{}", encoding="utf-8")

        assert f1.exists() and f2.exists() and f3.exists()
        main.cleanup_transient_checkpoints(week)
        # Active files removed
        assert not f1.exists()
        assert not f2.exists()
        assert not f3.exists()
        # Retired files preserved for forensics
        retired = list(tmp_path.glob("sync_progress_*.json.retired-*"))
        assert len(retired) == 1


def test_keyboard_interrupt_handling(tmp_path):
    with patch.object(config, "DATA_DIR", tmp_path):
        week = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        f1 = tmp_path / f"sync_progress_{week}.json"
        f1.write_text("{}", encoding="utf-8")

        with patch.object(main, "_main_inner", side_effect=KeyboardInterrupt):
            rc = main.main()

        assert rc == 130
        assert not f1.exists(), "Active transient progress file must be retired on SIGINT"
        assert len(list(tmp_path.glob("sync_progress_*.json.retired-*"))) == 1


def test_email_suppression_predicates(monkeypatch):
    import notifier
    # Unset
    monkeypatch.delenv("SKIP_EMAIL", raising=False)
    monkeypatch.setattr(config, "SKIP_EMAIL", False)
    assert notifier.email_suppressed() is False

    # SKIP_EMAIL env var
    for val in ("1", "true", "yes", "on", "TRUE", "Yes"):
        monkeypatch.setenv("SKIP_EMAIL", val)
        assert notifier.email_suppressed() is True

    # config.SKIP_EMAIL attribute
    monkeypatch.delenv("SKIP_EMAIL", raising=False)
    monkeypatch.setattr(config, "SKIP_EMAIL", True)
    assert notifier.email_suppressed() is True
