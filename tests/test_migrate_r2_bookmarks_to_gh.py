"""tests/test_migrate_r2_bookmarks_to_gh.py — Unit tests for bookmark migration to GitHub Releases."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import scripts.migrate_r2_bookmarks_to_gh as migrator


def test_migrate_bookmarks_populates_asset_fields_and_defaults_timestamp(monkeypatch, tmp_path):
    """Verify AUDIT-014:
    1. Successful / already-present assets get video_asset_name and release_tag.
    2. Failed assets omit video_asset_name and release_tag.
    3. Empty or missing bookmarked_at is defaulted to ISO timestamp, not empty string.
    """
    output_file = tmp_path / "bookmarks_migrated.json"

    # Mock run_gh_cmd to simulate:
    # - release view returning existing asset for reel_already_present
    # - release upload succeeding for reel_upload_success
    # - release upload failing for reel_upload_fail
    def mock_run_gh_cmd(cmd, **kwargs):
        res = MagicMock()
        res.returncode = 0
        if "view" in cmd:
            res.stdout = json.dumps({"assets": [{"name": "reel_already_present.mp4"}]})
        elif "upload" in cmd:
            # Check which file is being uploaded
            local_path = str(cmd[4]) if len(cmd) > 4 else ""
            if "reel_upload_fail" in local_path:
                raise RuntimeError("Upload error simulated")
            res.stdout = "{}"
        elif "api" in cmd:
            res.stdout = json.dumps({"sha": "dummy_sha"})
        return res

    monkeypatch.setattr(migrator, "run_gh_cmd", mock_run_gh_cmd)

    # Mock S3 client
    mock_s3 = MagicMock()

    def mock_download_file(bucket, key, local_path):
        if "reel_upload_fail" in key:
            # Create a file so it attempts upload and fails there
            p = Path(local_path)
            p.write_bytes(b"dummy video content")
        elif "reel_download_fail" in key:
            raise RuntimeError("S3 download failure simulated")
        else:
            p = Path(local_path)
            p.write_bytes(b"dummy video content")

    mock_s3.download_file = mock_download_file

    items = [
        {
            "id": "reel_already_present",
            "creator_handle": "creator1",
            "caption": "Already in release",
            "category": "tech",
            "size_bytes": 100,
            "bookmarked_at": "2026-05-01T12:00:00Z",
        },
        {
            "id": "reel_upload_success",
            "creator_handle": "creator2",
            "caption": "Upload succeeds",
            "category": "health",
            "size_bytes": 19,
            "bookmarked_at": "",  # Empty string: should default to ISO timestamp
        },
        {
            "id": "reel_upload_fail",
            "creator_handle": "creator3",
            "caption": "Upload fails",
            "category": "comedy",
            "size_bytes": 19,
            "bookmarked_at": None,  # None: should default to ISO timestamp
        },
        {
            "id": "reel_download_fail",
            "creator_handle": "creator4",
            "caption": "Download fails",
            "category": "art",
            "size_bytes": 19,
            # Missing bookmarked_at key completely
        },
    ]

    ret = migrator.migrate_bookmarks(
        repo="test-org/test-bookmarks",
        release_tag="v2026",
        s3=mock_s3,
        items=items,
        output_path=output_file,
        push_to_gh=False,
    )

    # Since two reels failed, ret must be 1
    assert ret == 1
    assert output_file.exists()

    migrated = json.loads(output_file.read_text(encoding="utf-8"))
    assert len(migrated) == 4

    by_id = {bm["id"]: bm for bm in migrated}

    # 1. reel_already_present: video_asset_name and release_tag populated
    bm_present = by_id["reel_already_present"]
    assert bm_present["video_asset_name"] == "reel_already_present.mp4"
    assert bm_present["release_tag"] == "v2026"
    assert bm_present["bookmarked_at"] == "2026-05-01T12:00:00Z"

    # 2. reel_upload_success: video_asset_name and release_tag populated, empty bookmarked_at defaulted
    bm_success = by_id["reel_upload_success"]
    assert bm_success["video_asset_name"] == "reel_upload_success.mp4"
    assert bm_success["release_tag"] == "v2026"
    assert bm_success["bookmarked_at"] != ""
    assert bm_success["bookmarked_at"].endswith("Z")
    assert "T" in bm_success["bookmarked_at"]

    # 3. reel_upload_fail: video_asset_name and release_tag NOT present, None bookmarked_at defaulted
    bm_up_fail = by_id["reel_upload_fail"]
    assert "video_asset_name" not in bm_up_fail
    assert "release_tag" not in bm_up_fail
    assert bm_up_fail["bookmarked_at"] is not None
    assert bm_up_fail["bookmarked_at"] != ""
    assert bm_up_fail["bookmarked_at"].endswith("Z")

    # 4. reel_download_fail: video_asset_name and release_tag NOT present, missing bookmarked_at defaulted
    bm_dl_fail = by_id["reel_download_fail"]
    assert "video_asset_name" not in bm_dl_fail
    assert "release_tag" not in bm_dl_fail
    assert bm_dl_fail["bookmarked_at"] != ""
    assert bm_dl_fail["bookmarked_at"].endswith("Z")


def test_migrate_bookmarks_all_success_returns_zero(monkeypatch, tmp_path):
    output_file = tmp_path / "bookmarks_migrated_success.json"

    def mock_run_gh_cmd(cmd, **kwargs):
        res = MagicMock()
        res.returncode = 0
        if "view" in cmd:
            res.stdout = json.dumps({"assets": [{"name": "reel_1.mp4"}]})
        elif "api" in cmd:
            res.stdout = json.dumps({"sha": "sha1"})
        return res

    monkeypatch.setattr(migrator, "run_gh_cmd", mock_run_gh_cmd)

    items = [
        {
            "id": "reel_1",
            "creator_handle": "creator1",
            "caption": "Reel 1",
            "bookmarked_at": "2026-01-01T00:00:00Z",
        }
    ]

    ret = migrator.migrate_bookmarks(
        repo="test-org/test-repo",
        release_tag="v2026",
        s3=MagicMock(),
        items=items,
        output_path=output_file,
        push_to_gh=False,
    )

    assert ret == 0
    migrated = json.loads(output_file.read_text(encoding="utf-8"))
    assert len(migrated) == 1
    assert migrated[0]["video_asset_name"] == "reel_1.mp4"
    assert migrated[0]["release_tag"] == "v2026"
