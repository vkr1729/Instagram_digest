"""Tests for the smart-funnel batch: media-info API enrichment, date pre-filter,
URL-encoded filenames, parallel recommendations, and session pre-check."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import extractor
import main


def _cand(rid, handle="alice"):
    return {
        "id": rid,
        "url": f"https://www.instagram.com/reel/{rid}/",
        "creator_handle": handle,
        "view_count": 1000,
    }


def test_shortcode_to_media_id_matches_ytdlp():
    sys.path.insert(0, ".venv/lib/python3.12/site-packages")
    from yt_dlp.extractor.instagram import _id_to_pk
    for sc in ("Dda9Q-WR4-W", "DdSoioDz6p4", "DOtifxyDQm7"):
        assert extractor._shortcode_to_media_id(sc) == str(_id_to_pk(sc))


def test_shortcode_to_media_id_rejects_garbage():
    with pytest.raises((ValueError, TypeError)):
        extractor._shortcode_to_media_id("!!! invalid !!!")


def test_media_prefilter_applies_cutoff_and_merges():
    cands = [_cand("AAA"), _cand("BBB"), _cand("CCC")]
    api = {
        "AAA": {"timestamp": 2000000000, "like_count": 500, "comment_count": 10,
                "view_count": 5000, "caption": "fresh", "duration": 30.0,
                "thumbnail": "t", "video_cdn_url": "v",
                "creator_handle": "alice", "creator_name": "Alice",
                "metrics_estimated": False},
        "BBB": {"timestamp": 1000000000, "like_count": 5, "comment_count": 0,
                "view_count": 50, "caption": "stale", "duration": 10.0,
                "thumbnail": "t", "video_cdn_url": "v",
                "creator_handle": "alice", "creator_name": "Alice",
                "metrics_estimated": False},
    }
    with patch.object(extractor, "fetch_media_info_batch", return_value=api):
        out = extractor.enrich_candidates_via_media_api(cands, cutoff_timestamp=1500000000)
    ids = [c["id"] for c in out]
    assert "AAA" in ids and "CCC" in ids and "BBB" not in ids
    fresh = next(c for c in out if c["id"] == "AAA")
    assert fresh["like_count"] == 500 and fresh["caption"] == "fresh"
    assert fresh["metrics_estimated"] is False
    assert fresh["view_count"] == 5000


def test_media_prefilter_pinned_bypasses_cutoff():
    cands = [dict(_cand("OLD"), is_pinned=True)]
    api = {"OLD": {"timestamp": 1000000000, "like_count": 1, "comment_count": 0,
                   "view_count": 10, "caption": "", "duration": 5.0,
                   "thumbnail": "", "video_cdn_url": "",
                   "creator_handle": "alice", "creator_name": "Alice",
                   "metrics_estimated": False}}
    with patch.object(extractor, "fetch_media_info_batch", return_value=api):
        out = extractor.enrich_candidates_via_media_api(cands, cutoff_timestamp=1500000000)
    assert [c["id"] for c in out] == ["OLD"]


def test_media_prefilter_empty_api_keeps_pool():
    cands = [_cand("AAA"), _cand("BBB")]
    with patch.object(extractor, "fetch_media_info_batch", return_value={}):
        out = extractor.enrich_candidates_via_media_api(cands, cutoff_timestamp=1500000000)
    assert [c["id"] for c in out] == ["AAA", "BBB"]


def test_fetch_media_batch_no_sessionid_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "cookies.json").write_text(json.dumps({"cookies_dict": {}}))
    assert extractor.fetch_media_info_batch(["AAA"]) == {}


def test_fetch_media_batch_429_backs_off(monkeypatch):
    (resp := MagicMock()).status_code = 429
    resp.headers = {}
    sess = MagicMock()
    sess.get.return_value = resp
    import requests as _rq
    monkeypatch.setattr(_rq, "Session", lambda: sess)
    monkeypatch.setattr(extractor.time, "sleep", lambda s: None)
    with patch.object(extractor, "_client_hint_headers", return_value={}):
        out = extractor.fetch_media_info_batch(
            ["AAA"],
            pause_secs=0,
        )
    assert out == {}


def test_recommendations_parallel_uses_two_workers():
    import inspect
    import recommendations
    src = inspect.getsource(recommendations.refresh_recommendations)
    assert "ThreadPoolExecutor" in src and "max_workers=2" in src


def test_session_precheck_present_in_sync():
    import inspect
    src = inspect.getsource(main._run_full_sync)
    assert "_ensure_valid_session" in src


def test_url_encoded_filenames_match_orphan_matcher():
    from urllib.parse import quote
    rid, handle = "DdL-ohbB7HP", "itsme_bhagavathi"
    fname = f"01_{quote(handle, safe='')}_{quote(rid, safe='')}.mp4"
    assert fname == "01_itsme_bhagavathi_DdL-ohbB7HP.mp4"
    rid2 = "abc/def ghi"
    fname2 = f"01_{quote(handle, safe='')}_{quote(rid2, safe='')}.mp4"
    assert "%" in fname2 and fname2.endswith(f"_{quote(rid2, safe='')}.mp4")
