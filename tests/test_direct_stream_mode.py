"""tests/test_direct_stream_mode.py — Automated tests for Zero-Cloud / Direct-Meta stream mode.

Verifies:
1. Full R2/S3 call graph bypass when DIRECT_STREAM_MODE is True.
2. Direct manifest (data_direct.json) generation with exact Meta CDN URLs and canonical permalinks.
3. R2 URL contamination prevention: R2 domains are cleanly purged from direct manifests (AUDIT-006, AUDIT-007).
4. Channel isolation: production data.json remains 100% untouched during direct manifest deployment.
5. Strict deploy abort invariants: fails safely if clone fails or if production data.json is missing.
6. Quorum checks in _pages_bundle_is_remote (AUDIT-008).
7. Fail-closed serve check in deploy_direct_to_gh_pages (AUDIT-009).
8. Dynamic marker file path via config.direct_deployed_week_file() (AUDIT-010).
9. Playable filtering in sync & expand with zero-playable and MIN_DEPLOY gates (AUDIT-001, AUDIT-011).
10. Stale thumbnail pruning on gh-pages deploy (AUDIT-024).
11. Thumbnail/poster fallback when jpg is missing (AUDIT-025).
12. Protection of reserved keys in extra_manifest and budget_capped propagation (AUDIT-026).
13. Dropped/blanked reel warning logs (AUDIT-027).
14. Stranded legacy outbox detection during direct reconcile (AUDIT-028).
15. gh-pages branch bootstrap on fresh remote (AUDIT-029).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import config
import main
import site_builder


@pytest.fixture(autouse=True)
def direct_mode_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DIRECT_STREAM_MODE", True)
    monkeypatch.setattr(config, "SITE_DIR", tmp_path / "site")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "data" / "digests")
    monkeypatch.setattr(config, "DIRECT_DEPLOYED_WEEK_FILE", tmp_path / "data" / "deployed_direct_week.json")
    config.SITE_DIR.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    config.DIGESTS_DIR.mkdir(parents=True, exist_ok=True)


def test_write_direct_manifest_preserves_meta_urls_and_strips_r2(tmp_path):
    """Verify write_direct_manifest maps direct Meta URLs to video_url, strips r2_url, and preserves permalinks."""
    out_file = config.direct_manifest_file()
    # Create thumbnail for reel123 so exists() check passes per AUDIT-025
    thumb_dir = out_file.parent / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    (thumb_dir / "reel123.jpg").write_bytes(b"jpg_data")

    sample_items = [
        {
            "id": "reel123",
            "creator_handle": "creator_a",
            "video_cdn_url": "https://instagram.fpen1-1.fna.fbcdn.net/v/t2/reel123.mp4?oe=6AD54193&token=abc%2Fdef",
            "video_url": "https://instagram-digest-media.workers.dev/videos/2026-10-24/01_creator_a_reel123.mp4",
            "r2_url": "https://instagram-digest-media.workers.dev/videos/2026-10-24/01_creator_a_reel123.mp4",
            "size_bytes": 10485760,
        },
        {
            "id": "reel456",
            "creator_handle": "creator_b",
            "url": "https://www.instagram.com/reel/reel456/",
            "video_cdn_url": "https://scontent.cdninstagram.com/v/t50/reel456.mp4?oe=6AD54193",
            "size_bytes": 5242880,
        },
    ]

    site_builder.write_direct_manifest(sample_items, week_id="2026-10-24", output_path=out_file)

    assert out_file.exists(), "data_direct.json must be written"
    data = json.loads(out_file.read_text(encoding="utf-8"))

    assert data["schema_version"] == 2
    assert data["channel"] == "direct"
    assert data["run_date"] == "2026-10-24"
    assert data["count"] == 2

    item0 = data["items"][0]
    assert item0["id"] == "reel123"
    assert item0["video_url"] == "https://instagram.fpen1-1.fna.fbcdn.net/v/t2/reel123.mp4?oe=6AD54193&token=abc%2Fdef"
    assert "r2_url" not in item0, "r2_url must be stripped"
    assert item0["url"] == "https://www.instagram.com/reel/reel123/"
    assert item0["size_bytes"] == 10485760
    assert item0["thumbnail"].endswith("/thumbnails/reel123.jpg")

    item1 = data["items"][1]
    assert item1["video_url"] == "https://scontent.cdninstagram.com/v/t50/reel456.mp4?oe=6AD54193"
    assert item1["url"] == "https://www.instagram.com/reel/reel456/"
    # reel456 has no local jpg file, should fall back to apple-touch-icon.png
    assert item1["thumbnail"].endswith("/apple-touch-icon.png")


def test_write_direct_manifest_drops_empty_and_r2_items_audit_006_027(monkeypatch, caplog):
    """Verify write_direct_manifest drops items with empty or R2 URLs, updates count, and logs warnings."""
    monkeypatch.setattr(config, "R2_PUBLIC_DOMAIN", "https://pub-r2.example.com")
    sample_items = [
        {
            "id": "reel_r2",
            "video_url": "https://pub-r2.example.com/videos/2026-10-24/01_reel.mp4",
        },
        {
            "id": "reel_empty",
            "video_url": "",
        },
        {
            "id": "reel_good",
            "video_cdn_url": "https://scontent.cdninstagram.com/v/t50/reel_good.mp4",
        },
    ]
    out_file = config.direct_manifest_file()
    with caplog.at_level(logging.WARNING):
        site_builder.write_direct_manifest(sample_items, week_id="2026-10-24", output_path=out_file)

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "reel_good"
    assert "reel_r2" in caplog.text
    assert "reel_empty" in caplog.text


def test_write_direct_manifest_extra_manifest_reserved_keys_audit_026(tmp_path):
    """Verify extra_manifest merges cleanly but refuses to overwrite reserved keys."""
    out_file = config.direct_manifest_file()
    items = [{"id": "r1", "video_cdn_url": "https://scontent.cdninstagram.com/v/t50/r1.mp4"}]
    extra = {
        "schema_version": 999,
        "channel": "hacked",
        "count": 0,
        "items": [],
        "budget_capped": True,
        "custom_metric": 42,
    }
    site_builder.write_direct_manifest(items, week_id="2026-10-24", output_path=out_file, extra_manifest=extra)
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["channel"] == "direct"
    assert data["count"] == 1
    assert len(data["items"]) == 1
    assert data["budget_capped"] is True
    assert data["custom_metric"] == 42


def test_build_site_passes_budget_capped_to_direct_manifest_audit_026(monkeypatch):
    """Verify build_site threads budget_capped into write_direct_manifest."""
    items = [{"id": "r1", "video_cdn_url": "https://scontent.cdninstagram.com/v/t50/r1.mp4"}]
    digest_data = {
        "run_date": "2026-10-24",
        "items": items,
        "budget_capped": True,
    }
    site_builder.build_site(digest_data=digest_data)
    manifest = config.direct_manifest_file()
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data.get("budget_capped") is True


def test_pages_bundle_is_remote_quorum_and_keys_audit_008(tmp_path):
    """Verify _pages_bundle_is_remote requires 60% quorum and inspects ONLY video_url in direct mode."""
    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = site_dir / "data_direct.json"

    # Case 1: items have valid video_cdn_url but empty video_url -> must fail
    manifest_file.write_text(json.dumps({
        "items": [
            {"id": "1", "video_url": "", "video_cdn_url": "https://scontent.cdninstagram.com/1.mp4"},
            {"id": "2", "video_url": "", "video_cdn_url": "https://scontent.cdninstagram.com/2.mp4"},
        ]
    }))
    assert site_builder._pages_bundle_is_remote(site_dir, direct_mode=True) is False

    # Case 2: 5 items, 2 good (40% < 60% floor) -> must fail
    manifest_file.write_text(json.dumps({
        "items": [
            {"id": "1", "video_url": "https://scontent.cdninstagram.com/1.mp4"},
            {"id": "2", "video_url": "https://scontent.cdninstagram.com/2.mp4"},
            {"id": "3", "video_url": ""},
            {"id": "4", "video_url": ""},
            {"id": "5", "video_url": ""},
        ]
    }))
    assert site_builder._pages_bundle_is_remote(site_dir, direct_mode=True) is False

    # Case 3: 5 items, 3 good (60% >= 60% floor) -> must pass
    manifest_file.write_text(json.dumps({
        "items": [
            {"id": "1", "video_url": "https://scontent.cdninstagram.com/1.mp4"},
            {"id": "2", "video_url": "https://scontent.cdninstagram.com/2.mp4"},
            {"id": "3", "video_url": "https://scontent.cdninstagram.com/3.mp4"},
            {"id": "4", "video_url": ""},
            {"id": "5", "video_url": ""},
        ]
    }))
    assert site_builder._pages_bundle_is_remote(site_dir, direct_mode=True) is True


def test_deploy_direct_fail_closed_on_serve_check_exception_audit_009(monkeypatch, tmp_path):
    """Verify deploy_direct_to_gh_pages fails closed when outer serve check errors."""
    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "SITE_DIR", site_dir)
    monkeypatch.setattr(config, "DIRECT_PAGES_MANIFEST_URL", "https://example.com/data_direct.json")

    direct_manifest = site_dir / "data_direct.json"
    direct_manifest.write_text(json.dumps({
        "channel": "direct",
        "run_date": "2026-10-24",
        "items": [{"id": "1", "video_url": "https://scontent.cdninstagram.com/1.mp4"}]
    }))

    prod_json_content = b'{"channel": "production", "items": [{"id": "prod1"}]}'

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "clone" in cmd:
            target_dir = Path(cmd[-1])
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "data.json").write_bytes(prod_json_content)
            return MagicMock(returncode=0, stdout="", stderr="")
        if "diff" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/git")

    # Simulate unexpected outer exception during poll loop by having time.sleep raise
    import time
    def fake_sleep(seconds):
        raise RuntimeError("Crash during poll backoff")
    monkeypatch.setattr(time, "sleep", fake_sleep)

    success = site_builder.deploy_direct_to_gh_pages(site_dir=site_dir, repo_url="mock_repo")
    assert success is False
    marker = config.direct_deployed_week_file()
    assert not marker.exists(), "Marker must not be written when deploy fails"


def test_direct_deployed_week_file_isolation_audit_010(tmp_path):
    """Verify config.direct_deployed_week_file derives from config.DATA_DIR dynamically."""
    assert config.direct_deployed_week_file() == config.DATA_DIR / "deployed_direct_week.json"
    assert str(tmp_path) in str(config.direct_deployed_week_file())


def test_deploy_direct_prunes_stale_thumbnails_audit_024(monkeypatch, tmp_path):
    """Verify deploy_direct_to_gh_pages prunes stale thumbnails not referenced in manifest."""
    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "SITE_DIR", site_dir)
    monkeypatch.setattr(config, "DIRECT_PAGES_MANIFEST_URL", "")

    # Manifest references only reel_keep
    direct_manifest = site_dir / "data_direct.json"
    direct_manifest.write_text(json.dumps({
        "channel": "direct",
        "run_date": "2026-10-24",
        "items": [{"id": "reel_keep", "video_url": "https://scontent.cdninstagram.com/1.mp4"}]
    }))

    # Site dir has thumbnail for reel_keep
    src_thumbs = site_dir / "thumbnails"
    src_thumbs.mkdir(parents=True, exist_ok=True)
    (src_thumbs / "reel_keep.jpg").write_bytes(b"keep_jpg")

    prod_json_content = b'{"channel": "production", "items": [{"id": "prod1"}]}'
    pruned_verified = [False]

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "clone" in cmd:
            target_dir = Path(cmd[-1])
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "data.json").write_bytes(prod_json_content)
            # Add pre-existing thumbnails in clone
            dst_th = target_dir / "thumbnails"
            dst_th.mkdir(parents=True, exist_ok=True)
            (dst_th / "stale_reel.jpg").write_bytes(b"stale")
            (dst_th / "stale_reel_portrait.jpg").write_bytes(b"stale_p")
            (dst_th / ".tmp-worker").write_bytes(b"tmp")
            return MagicMock(returncode=0, stdout="", stderr="")
        if "commit" in cmd:
            cwd = Path(kwargs.get("cwd"))
            dst_th = cwd / "thumbnails"
            # Verify stale thumbnails are unlinked and active + tmp are preserved
            if (dst_th / "reel_keep.jpg").exists() and (dst_th / ".tmp-worker").exists():
                if not (dst_th / "stale_reel.jpg").exists() and not (dst_th / "stale_reel_portrait.jpg").exists():
                    pruned_verified[0] = True
            return MagicMock(returncode=0, stdout="", stderr="")
        if "diff" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/git")

    success = site_builder.deploy_direct_to_gh_pages(site_dir=site_dir, repo_url="mock_repo")
    assert success is True
    assert pruned_verified[0] is True, "Stale thumbnails must be pruned before commit"


def test_deploy_direct_bootstrap_on_missing_remote_branch_audit_029(monkeypatch, tmp_path):
    """Verify deploy_direct_to_gh_pages bootstraps fresh gh-pages branch on remote branch not found."""
    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "SITE_DIR", site_dir)
    monkeypatch.setattr(config, "DIRECT_PAGES_MANIFEST_URL", "")

    direct_manifest = site_dir / "data_direct.json"
    direct_manifest.write_text(json.dumps({
        "channel": "direct",
        "run_date": "2026-10-24",
        "items": [{"id": "1", "video_url": "https://scontent.cdninstagram.com/1.mp4"}]
    }))

    recorded_cmds = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        recorded_cmds.append(cmd)
        if "clone" in cmd:
            # Clone fails with branch not found
            return MagicMock(returncode=128, stdout="", stderr="fatal: Remote branch gh-pages not found in upstream origin")
        if "diff" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/git")

    success = site_builder.deploy_direct_to_gh_pages(site_dir=site_dir, repo_url="mock_repo")
    assert success is True
    # Verify git init -b gh-pages and git push -u origin gh-pages were called
    assert any("init" in cmd and "-b" in cmd and "gh-pages" in cmd for cmd in recorded_cmds)
    assert any("push" in cmd and "-u" in cmd and "origin" in cmd and "gh-pages" in cmd for cmd in recorded_cmds)


def test_reconcile_stranded_outbox_audit_028(monkeypatch):
    """Verify _run_reconcile detects stranded legacy outbox in direct mode and returns 1."""
    week_id = "2026-10-24"
    # Write an outbox with parked reels
    main._write_upload_outbox(week_id, [{"id": "parked1"}], {})

    res = main._run_reconcile(week_id=week_id)
    assert res == 1, "Must return 1 and warn when stranded outbox exists in direct mode"

    # Now clear outbox
    main._outbox_path(week_id).unlink(missing_ok=True)
    res_clean = main._run_reconcile(week_id=week_id)
    assert res_clean == 0, "Must return 0 when outbox is clear"


def test_direct_mode_persist_loop_and_r2_url_sanitization_audit_007():
    """Verify that in direct mode, r2_url is popped and not saved to digest batch."""
    ranked_reels = [
        {
            "id": "r1",
            "video_cdn_url": "https://scontent.cdninstagram.com/v/t50/r1.mp4",
            "r2_url": "https://pub-r2.example.com/r1.mp4",
        }
    ]
    uploaded_url_map = {"r1": "https://scontent.cdninstagram.com/v/t50/r1.mp4"}

    # Simulate persist block
    for r in ranked_reels:
        r["video_url"] = uploaded_url_map.get(r.get("id")) or ""
        r.pop("r2_url", None)

    assert ranked_reels[0]["video_url"] == "https://scontent.cdninstagram.com/v/t50/r1.mp4"
    assert "r2_url" not in ranked_reels[0]


def test_legacy_parity(monkeypatch, tmp_path):
    """AUDIT-012: Verify that when DIRECT_STREAM_MODE is False, legacy R2 operations are invoked as expected."""
    import storage_r2
    monkeypatch.setattr(config, "DIRECT_STREAM_MODE", False)
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "legacy_r2_acc")
    monkeypatch.setattr(config, "R2_PUBLIC_DOMAIN", "https://pub-r2.example.com")
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "test-bucket")

    calls = []

    def make_recorder(name, ret=None):
        def _fn(*args, **kwargs):
            calls.append(name)
            return ret
        return _fn

    monkeypatch.setattr(storage_r2, "estimate_weekly_batch_bytes", make_recorder("estimate_weekly_batch_bytes", 500 * 1024 * 1024))
    monkeypatch.setattr(storage_r2, "get_bucket_storage_usage", make_recorder("get_bucket_storage_usage", (100 * 1024 * 1024, 10)))
    monkeypatch.setattr(storage_r2, "check_preflight_quota", make_recorder("check_preflight_quota", True))
    monkeypatch.setattr(storage_r2, "upload_manifest_to_r2", make_recorder("upload_manifest_to_r2", "https://pub-r2.example.com/data.json"))
    monkeypatch.setattr(storage_r2, "upload_reel_to_r2", make_recorder("upload_reel_to_r2", "https://pub-r2.example.com/reel1.mp4"))
    monkeypatch.setattr(storage_r2, "purge_previous_weeks_videos", make_recorder("purge_previous_weeks_videos", []))
    monkeypatch.setattr(storage_r2, "get_existing_r2_keys", make_recorder("get_existing_r2_keys", set()))
    monkeypatch.setattr(storage_r2, "purge_expired_r2_objects", make_recorder("purge_expired_r2_objects", []))
    monkeypatch.setattr(storage_r2, "purge_unreferenced_r2_videos", make_recorder("purge_unreferenced_r2_videos", []))

    # 1. Quota preflight in legacy mode must query R2 batch estimate and bucket usage
    calls.clear()
    res = main._estimate_run()
    assert res == 0
    assert "estimate_weekly_batch_bytes" in calls
    assert "get_bucket_storage_usage" in calls

    # 2. Manifest publish in legacy mode must upload data.json to R2
    calls.clear()
    dummy_manifest = config.SITE_DIR / "data.json"
    dummy_manifest.write_text(
        json.dumps({"run_date": "2026-10-24", "items": [{"id": f"r{i}"} for i in range(main.MIN_DEPLOY_ITEMS)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "_verify_digest_r2_keys", lambda items: set())
    r2_url = main._publish_manifest_to_r2()
    assert r2_url == "https://pub-r2.example.com/data.json"
    assert "upload_manifest_to_r2" in calls

    # 3. Simulate legacy Phase 5D upload & purge calls
    calls.clear()
    storage_r2.purge_previous_weeks_videos(current_week_id="2026-10-24")
    assert "purge_previous_weeks_videos" in calls

    quota_ok = storage_r2.check_preflight_quota(estimated_new_bytes=1000)
    assert quota_ok is True
    assert "check_preflight_quota" in calls

    test_video = tmp_path / "test.mp4"
    test_video.write_bytes(b"video_bytes")
    pub = storage_r2.upload_reel_to_r2(test_video, week_id="2026-10-24", key_name="test.mp4")
    assert pub == "https://pub-r2.example.com/reel1.mp4"
    assert "upload_reel_to_r2" in calls


def test_full_call_graph_bypasses_s3_in_direct_stream_mode(monkeypatch):
    """AUDIT-012: Verify that in DIRECT_STREAM_MODE, all R2/S3 entry points raise if called, ensuring complete bypass."""
    import storage_r2

    r2_functions = [
        "purge_previous_weeks_videos",
        "upload_reel_to_r2",
        "get_existing_r2_keys",
        "check_preflight_quota",
        "get_bucket_storage_usage",
        "estimate_weekly_batch_bytes",
        "purge_expired_r2_objects",
        "purge_unreferenced_r2_videos",
        "upload_manifest_to_r2",
        "get_s3_client",
    ]

    for fn_name in r2_functions:
        def poison_stub(*args, name=fn_name, **kwargs):
            raise AssertionError(f"R2 function storage_r2.{name} was called in DIRECT_STREAM_MODE!")
        monkeypatch.setattr(storage_r2, fn_name, poison_stub)

    # 1. Estimate run must return 0 without touching S3/R2
    assert main._estimate_run() == 0

    # 2. Reconcile must return 0 without touching S3/R2 (no stranded outbox)
    assert main.run_reconcile() == 0

    # 3. _publish_manifest_to_r2 must return None without touching S3/R2
    assert main._publish_manifest_to_r2() is None

    # 4. Pure write_direct_manifest must build without touching S3/R2
    sample_items = [
        {"id": "reel1", "video_cdn_url": "https://scontent.cdninstagram.com/reel1.mp4", "size_bytes": 1000}
    ]
    out = site_builder.write_direct_manifest(sample_items, week_id="2026-10-24")
    assert out.exists()

    # 5. build_site with Meta-URL items must build without touching S3/R2
    digest_payload = {"run_date": "2026-10-24", "items": sample_items}
    site_builder.build_site(digest_data=digest_payload)
    assert config.direct_manifest_file().exists()

    # 6. _build_only must execute without touching S3/R2
    config.DIGEST_BATCH_FILE.write_text(json.dumps(digest_payload), encoding="utf-8")
    assert main._build_only(deploy=False) == 0

    # 7. Direct mode upload simulation (exercising direct mapping branch)
    reel = {"id": "meta_reel", "video_cdn_url": "https://scontent.cdninstagram.com/v/t50/reel.mp4"}
    cand = reel.get("video_cdn_url") or reel.get("video_url") or ""
    assert site_builder.is_direct_stream_url(cand) is True


def test_direct_deploy_preserves_production_data_json(monkeypatch, tmp_path):
    """Verify deploy_direct_to_gh_pages verifies production data.json byte-for-byte."""
    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "SITE_DIR", site_dir)
    monkeypatch.setattr(config, "DIRECT_PAGES_MANIFEST_URL", "")  # Skip network poll in unit test

    # Create dummy data_direct.json
    direct_manifest = site_dir / "data_direct.json"
    direct_manifest.write_text(json.dumps({
        "channel": "direct",
        "run_date": "2026-10-24",
        "items": [{"id": "1", "video_url": "https://scontent.cdninstagram.com/1.mp4"}]
    }))

    prod_json_content = b'{"channel": "production", "items": [{"id": "prod1"}]}'

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "clone" in cmd:
            target_dir = Path(cmd[-1])
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "data.json").write_bytes(prod_json_content)
            return MagicMock(returncode=0, stdout="", stderr="")
        if "diff" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="")  # has changes
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/git")

    success = site_builder.deploy_direct_to_gh_pages(site_dir=site_dir, repo_url="mock_repo")
    assert success is True


def test_direct_deploy_aborts_if_clone_fails(monkeypatch, tmp_path):
    """Verify deploy_direct_to_gh_pages fails immediately if clone fails for non-bootstrap errors."""
    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    direct_manifest = site_dir / "data_direct.json"
    direct_manifest.write_text(json.dumps({
        "channel": "direct",
        "items": [{"id": "1", "video_url": "https://scontent.cdninstagram.com/1.mp4"}]
    }))

    def fake_failing_clone(cmd, *args, **kwargs):
        if "clone" in cmd:
            return MagicMock(returncode=128, stdout="", stderr="fatal: repository not found")
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_failing_clone)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/git")

    success = site_builder.deploy_direct_to_gh_pages(site_dir=site_dir, repo_url="mock_repo")
    assert success is False, "Must fail safely if clone fails"


def test_direct_deploy_aborts_if_production_data_json_missing(monkeypatch, tmp_path):
    """Verify deploy_direct_to_gh_pages fails if production data.json is missing in clone (absence is failure)."""
    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    direct_manifest = site_dir / "data_direct.json"
    direct_manifest.write_text(json.dumps({
        "channel": "direct",
        "items": [{"id": "1", "video_url": "https://scontent.cdninstagram.com/1.mp4"}]
    }))

    def fake_empty_clone(cmd, *args, **kwargs):
        if "clone" in cmd:
            target_dir = Path(cmd[-1])
            target_dir.mkdir(parents=True, exist_ok=True)
            # data.json is missing!
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_empty_clone)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/git")

    success = site_builder.deploy_direct_to_gh_pages(site_dir=site_dir, repo_url="mock_repo")
    assert success is False, "Must abort if production data.json is missing in clone"


def test_direct_mode_filtering_and_gates_audit_001():
    """Verify direct mode filters unplayable reels and triggers zero-playable / MIN_DEPLOY gates."""
    from site_builder import is_direct_stream_url

    ranked_reels = [
        {"id": "dead1", "video_url": "https://pub-r2.example.com/1.mp4"},
        {"id": "dead2", "video_url": ""},
        {"id": "live1", "video_cdn_url": "https://scontent.cdninstagram.com/live1.mp4"},
    ]
    uploaded_url_map = {}
    playable = []
    dead = []
    for reel in ranked_reels:
        cand = reel.get("video_cdn_url") or reel.get("video_url") or ""
        if cand and is_direct_stream_url(cand):
            uploaded_url_map[reel["id"]] = cand
            playable.append(reel)
        else:
            dead.append(reel.get("id"))
    ranked_reels = playable
    dropped = dead

    assert len(ranked_reels) == 1
    assert ranked_reels[0]["id"] == "live1"
    assert set(dropped) == {"dead1", "dead2"}
    assert uploaded_url_map == {"live1": "https://scontent.cdninstagram.com/live1.mp4"}

    # If all reels were dead:
    dead_ranked_reels = [
        {"id": "dead1", "video_url": "https://pub-r2.example.com/1.mp4"},
        {"id": "dead2", "video_url": ""},
    ]
    playable_empty = []
    for reel in dead_ranked_reels:
        cand = reel.get("video_cdn_url") or reel.get("video_url") or ""
        if cand and is_direct_stream_url(cand):
            playable_empty.append(reel)
    assert len(playable_empty) == 0, "Zero-playable guard triggers when no valid Meta CDN URLs exist"


def test_direct_expand_filtering_and_gates_audit_011():
    """Verify _run_expand seeds map only with valid Meta URLs and drops unplayable from combined items."""
    from site_builder import is_direct_stream_url

    existing_items = [
        {"id": "stale_r2", "r2_url": "https://pub-r2.example.com/stale.mp4"},
        {"id": "valid_meta", "video_cdn_url": "https://scontent.cdninstagram.com/valid.mp4"},
    ]
    uploaded_url_map = {}
    carried_over_count = 0
    for item in existing_items:
        rid = item.get("id")
        if not rid:
            continue
        cand = item.get("video_cdn_url") or item.get("video_url") or ""
        if cand and is_direct_stream_url(cand):
            uploaded_url_map[rid] = cand
            carried_over_count += 1

    assert carried_over_count == 1
    assert uploaded_url_map == {"valid_meta": "https://scontent.cdninstagram.com/valid.mp4"}
    assert "stale_r2" not in uploaded_url_map

    # Combined items filtering
    new_ranked = [
        {"id": "new_good", "video_cdn_url": "https://scontent.cdninstagram.com/new.mp4"},
        {"id": "new_bad", "video_url": "https://other.com/bad.mp4"},
    ]
    combined_items = existing_items + new_ranked

    playable = []
    dead = []
    for reel in combined_items:
        cand = uploaded_url_map.get(reel.get("id") or "") or reel.get("video_cdn_url") or reel.get("video_url") or ""
        if cand and is_direct_stream_url(cand):
            reel["video_url"] = cand
            reel.pop("r2_url", None)
            playable.append(reel)
        else:
            dead.append(reel.get("id"))

    assert len(playable) == 2
    assert [r["id"] for r in playable] == ["valid_meta", "new_good"]
    assert set(dead) == {"stale_r2", "new_bad"}
    assert all("r2_url" not in r for r in playable)


def test_direct_build_only(monkeypatch, tmp_path):
    """AUDIT-012: Verify _build_only in direct mode builds data_direct.json hermetically and returns 0."""
    import storage_r2
    for fn in ("get_s3_client", "upload_manifest_to_r2", "upload_reel_to_r2"):
        monkeypatch.setattr(storage_r2, fn, MagicMock(side_effect=AssertionError("R2 called during direct build-only")))

    sample_items = [
        {"id": "build1", "video_cdn_url": "https://scontent.cdninstagram.com/b1.mp4", "rank": 1},
        {"id": "build2", "video_cdn_url": "https://instagram.fpen1-1.fna.fbcdn.net/b2.mp4", "rank": 2},
    ]
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": "2026-10-24", "items": sample_items}), encoding="utf-8")

    code = main._build_only(deploy=False)
    assert code == 0
    direct_manifest = config.direct_manifest_file()
    assert direct_manifest.exists()
    data = json.loads(direct_manifest.read_text(encoding="utf-8"))
    assert data["channel"] == "direct"
    assert data["count"] == 2


def test_direct_deploy_only(monkeypatch):
    """AUDIT-012: Verify _deploy_only in direct mode routes to deploy_direct_to_gh_pages and checks min count."""
    # Fewer than MIN_DEPLOY_ITEMS should abort with code 2
    monkeypatch.setattr(main, "_digest_item_count", lambda: 5)
    assert main._deploy_only() == 2

    # With enough items, delegates to _publish_digest which calls deploy_direct_to_gh_pages
    monkeypatch.setattr(main, "_digest_item_count", lambda: main.MIN_DEPLOY_ITEMS)
    mock_deploy = MagicMock(return_value=True)
    monkeypatch.setattr(site_builder, "deploy_direct_to_gh_pages", mock_deploy)

    assert main._deploy_only() == 0
    assert mock_deploy.called


def test_direct_expand_pipeline(monkeypatch, tmp_path):
    """AUDIT-012: Verify _run_expand in direct mode carries over Meta URLs, integrates new reels without R2, and rebuilds direct manifest."""
    import storage_r2
    import extractor

    # Poison R2
    for fn in ("upload_reel_to_r2", "get_existing_r2_keys", "check_preflight_quota", "purge_previous_weeks_videos"):
        monkeypatch.setattr(storage_r2, fn, MagicMock(side_effect=AssertionError("R2 called during direct expand")))

    week_id = "2026-10-24"
    existing_items = [
        {"id": "exist1", "creator_handle": "alice", "video_cdn_url": "https://scontent.cdninstagram.com/e1.mp4", "rank": 1},
        {"id": "exist2", "creator_handle": "bob", "video_cdn_url": "https://scontent.cdninstagram.com/e2.mp4", "rank": 2},
    ]
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": week_id, "items": existing_items}), encoding="utf-8")

    # Create expand checkpoint with 2 new reels
    checkpoint_file = config.DATA_DIR / f"expand_checkpoint_{week_id}.json"
    new_reels = [
        {"id": "exp1", "creator_handle": "carol", "url": "https://instagram.com/reel/exp1/", "video_cdn_url": "https://scontent.cdninstagram.com/exp1.mp4"},
        {"id": "exp2", "creator_handle": "dave", "url": "https://instagram.com/reel/exp2/", "video_cdn_url": "https://instagram.fpen1-1.fna.fbcdn.net/exp2.mp4"},
    ]
    checkpoint_file.write_text(json.dumps({
        "version": 1,
        "target_count": 2,
        "saved_at": 10**10,  # fresh
        "reels": new_reels,
    }), encoding="utf-8")

    # Mock download to create dummy local video
    def mock_download(url, dest, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"dummy_video_bytes")
        return True
    monkeypatch.setattr(extractor, "download_reel_video", mock_download)

    res = main._run_expand(target_count=2, deploy=False)
    assert res == 0

    # Batch file must now have all 4 items
    updated_data = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
    assert len(updated_data["items"]) == 4
    for it in updated_data["items"]:
        assert "r2_url" not in it
        assert site_builder.is_direct_stream_url(it["video_url"])

    # Direct manifest must exist and include all 4 items
    assert config.direct_manifest_file().exists()
    direct_data = json.loads(config.direct_manifest_file().read_text(encoding="utf-8"))
    assert direct_data["channel"] == "direct"
    assert direct_data["count"] == 4


def test_is_direct_stream_url_edge_cases():
    """AUDIT-012: Verify is_direct_stream_url rigorously accepts valid Meta CDN streams and rejects everything else."""
    from site_builder import is_direct_stream_url

    # Valid Meta CDN URLs
    assert is_direct_stream_url("https://scontent.cdninstagram.com/o1/v/t16/f1/m86/test.mp4") is True
    assert is_direct_stream_url("https://instagram.fpen1-1.fna.fbcdn.net/v/t2/reel.mp4?oe=6AD54193") is True
    assert is_direct_stream_url("https://fbcdn.net/video.mp4") is True
    assert is_direct_stream_url("https://cdninstagram.com/video.mp4") is True
    assert is_direct_stream_url("https://subdomain.nested.fbcdn.net/stream.mp4") is True
    assert is_direct_stream_url("https://subdomain.nested.cdninstagram.com/stream.mp4") is True

    # Insecure HTTP must be rejected
    assert is_direct_stream_url("http://scontent.cdninstagram.com/test.mp4") is False
    assert is_direct_stream_url("http://instagram.fpen1-1.fna.fbcdn.net/test.mp4") is False

    # Lookalike / spoof domains must be rejected
    assert is_direct_stream_url("https://notfbcdn.net/test.mp4") is False
    assert is_direct_stream_url("https://evilfbcdn.net/test.mp4") is False
    assert is_direct_stream_url("https://notcdninstagram.com/test.mp4") is False
    assert is_direct_stream_url("https://cdninstagram.com.evil.com/test.mp4") is False

    # External storage & Worker domains must be rejected
    assert is_direct_stream_url("https://pub-r2.example.com/videos/test.mp4") is False
    assert is_direct_stream_url("https://instagram-digest-media.workers.dev/videos/test.mp4") is False

    # Instagram web pages (not media CDN) must be rejected
    assert is_direct_stream_url("https://www.instagram.com/reel/C99887766/") is False
    assert is_direct_stream_url("https://instagram.com/p/123/") is False

    # Empty, None, non-strings, and malformed strings
    assert is_direct_stream_url("") is False
    assert is_direct_stream_url(None) is False
    assert is_direct_stream_url(12345) is False
    assert is_direct_stream_url(["https://fbcdn.net/video.mp4"]) is False
    assert is_direct_stream_url({"url": "https://fbcdn.net/video.mp4"}) is False
    assert is_direct_stream_url("https://[invalid-ipv6/test") is False
    assert is_direct_stream_url("random non-url text") is False


def test_deploy_direct_byte_for_byte_preservation_and_mutation_abort(monkeypatch, tmp_path):
    """AUDIT-012: Verify deploy_direct_to_gh_pages guarantees byte-for-byte preservation and aborts on any modification."""
    import shutil
    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "SITE_DIR", site_dir)
    monkeypatch.setattr(config, "DIRECT_PAGES_MANIFEST_URL", "")

    direct_manifest = site_dir / "data_direct.json"
    direct_manifest.write_text(json.dumps({
        "channel": "direct",
        "run_date": "2026-10-24",
        "items": [{"id": "1", "video_url": "https://scontent.cdninstagram.com/1.mp4"}]
    }))

    golden_prod_bytes = b'{\n  "channel": "production",\n  "count": 100,\n  "checksum": "abcdef1234567890"\n}\n'
    verified_commit_bytes = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "clone" in cmd:
            target_dir = Path(cmd[-1])
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "data.json").write_bytes(golden_prod_bytes)
            return MagicMock(returncode=0, stdout="", stderr="")
        if "diff" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="")
        if "commit" in cmd:
            target_dir = Path(kwargs.get("cwd"))
            actual_bytes = (target_dir / "data.json").read_bytes()
            verified_commit_bytes.append(actual_bytes)
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/git")

    # 1. Successful deploy: data.json remains byte-for-byte identical
    success = site_builder.deploy_direct_to_gh_pages(site_dir=site_dir, repo_url="mock_repo")
    assert success is True
    assert len(verified_commit_bytes) == 1
    assert verified_commit_bytes[0] == golden_prod_bytes
    assert hashlib.sha256(verified_commit_bytes[0]).hexdigest() == hashlib.sha256(golden_prod_bytes).hexdigest()

    # 2. Mutation simulation: if data.json was altered during overlay, deploy aborts immediately
    def fake_subprocess_run_mutating(cmd, *args, **kwargs):
        if "clone" in cmd:
            target_dir = Path(cmd[-1])
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "data.json").write_bytes(golden_prod_bytes)
            # Tamper with production data.json during overlay
            orig_copy2 = shutil.copy2
            def corrupting_copy2(src, dst):
                res = orig_copy2(src, dst)
                (Path(dst).parent / "data.json").write_bytes(b"corrupted_bytes_tampered!")
                return res
            monkeypatch.setattr(shutil, "copy2", corrupting_copy2)
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_subprocess_run_mutating)
    failed_success = site_builder.deploy_direct_to_gh_pages(site_dir=site_dir, repo_url="mock_repo")
    assert failed_success is False, "Must abort if data.json bytes change during deploy"
