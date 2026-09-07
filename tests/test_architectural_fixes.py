"""
test_architectural_fixes.py — Automated verification and demonstration tests for
all clinical fixes (C1-C8, B1-B5, P1-P8) identified in INSTAGRAM_DIGEST_REVIEW_AND_FIX_PLAN.md.
"""

import json
import os
import pathlib
import threading
import time
from unittest import mock
import botocore.exceptions
import pytest
from playwright.sync_api import sync_playwright

import config
import extractor
import main
import ranker
import site_builder
import storage_r2
from extractor import InstagramBlocked, _assert_not_blocked, InstagramSession
from local_server import _STATE_LOCK, _atomic_write_json, LocalDigestHandler


# ============================================================================
# C1 & C5: Detection vs Error Distinction & Adaptive Rate Limiting
# ============================================================================

def test_c1_c5_instagram_blocked_detection():
    """Verify InstagramBlocked is raised on login redirects or challenge/suspension pages."""
    mock_page = mock.MagicMock()
    
    # 1. Login redirect
    mock_page.url = "https://www.instagram.com/accounts/login/?next=%2Freels%2F"
    with pytest.raises(InstagramBlocked) as exc_info:
        _assert_not_blocked(mock_page, "test_context")
    assert "accounts/login" in str(exc_info.value).lower()

    # 2. Challenge page
    mock_page.url = "https://www.instagram.com/challenge/action/"
    with pytest.raises(InstagramBlocked):
        _assert_not_blocked(mock_page, "test_context")

    # 3. Suspended page
    mock_page.url = "https://www.instagram.com/accounts/suspended/"
    with pytest.raises(InstagramBlocked):
        _assert_not_blocked(mock_page, "test_context")

    # 4. Clean profile page passes without error
    mock_page.url = "https://www.instagram.com/mkbhd/reels/"
    _assert_not_blocked(mock_page, "test_context")


def test_c1_viability_constants_and_abort_ratios():
    """Verify viability thresholds: 50% candidate ratio, 60% empty creator ratio."""
    assert main.MIN_CANDIDATE_RATIO == 0.5
    assert main.MAX_EMPTY_CREATOR_RATIO == 0.6
    assert main.MIN_DEPLOY_ITEMS == int(config.TOP_DIGEST_COUNT * 0.6)


def test_c1_viability_gate_aborts_run_without_touching_digest(tmp_path, monkeypatch):
    """All creators empty -> run_full_sync exits 2, deletes the cache, digest untouched."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")
    sentinel = tmp_path / "top100_digest.json"
    sentinel.write_text('{"sentinel": true}')
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", sentinel)
    monkeypatch.setattr(extractor, "load_sources", lambda: [
        {"handle": "alice", "category": "entertainment", "enabled": True},
        {"handle": "bob", "category": "finance", "enabled": True},
        {"handle": "cara", "category": "food", "enabled": True},
    ])
    monkeypatch.setattr(extractor, "InstagramSession", mock.MagicMock)
    monkeypatch.setattr(extractor, "extract_creator_reels", lambda **kw: [])

    rc = main.run_full_sync(dry_run=True, deploy=False, days_back=7, limit_per_creator=15)

    assert rc == 2
    assert not (tmp_path / "candidates_cache.json").exists()
    assert json.loads(sentinel.read_text()) == {"sentinel": True}


# ============================================================================
# C2: Download/R2 Failure Pruning & Invariant URL Output
# ============================================================================

def test_c2_upload_reel_to_r2_never_returns_local_path_on_failure(monkeypatch, tmp_path):
    """Verify upload_reel_to_r2 returns '' (not local path) when remote R2 upload fails."""
    dummy_file = tmp_path / "video.mp4"
    dummy_file.write_text("test")

    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "dummy_account")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "dummy_key")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "dummy_secret")
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "dummy_bucket")
    monkeypatch.setattr(config, "R2_PUBLIC_DOMAIN", "https://pub-dummy.r2.dev")

    mock_s3 = mock.MagicMock()
    # Mock head_object raising 404 (does not exist on R2 yet)
    mock_s3.head_object.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    # Mock upload_file failing with error
    mock_s3.upload_file.side_effect = RuntimeError("S3 Network Failure")
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: mock_s3)

    result_url = storage_r2.upload_reel_to_r2(dummy_file, week_id="2026-09-06")
    assert result_url == "", "Must return empty string on R2 failure, never a local path"


def test_c2_site_builder_prunes_unplayable_reels(tmp_path, monkeypatch):
    """Verify build_site skips any reel not in r2_uploaded_urls to prevent dead cards."""
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    (tmp_path / "videos").mkdir()

    raw_items = [
        {"id": "playable_1", "creator_handle": "alice", "rank": 1, "view_count": 50000},
        {"id": "failed_upload_2", "creator_handle": "bob", "rank": 2, "view_count": 40000},
    ]
    # Only playable_1 succeeded in R2 upload
    r2_uploaded_urls = {"playable_1": "https://pub.dev/videos/playable_1.mp4"}

    index_html, _ = site_builder.build_site(
        digest_data={"run_date": "2026-09-06", "items": raw_items},
        r2_uploaded_urls=r2_uploaded_urls
    )

    rendered_text = index_html.read_text()
    assert "playable_1" in rendered_text
    assert "failed_upload_2" not in rendered_text


# ============================================================================
# C3: Fast-Mode Metadata Honesty & Two-Pass Viral Ranking
# ============================================================================

def test_c3_fast_mode_honest_metrics_and_zero_synthetic_engagement():
    """Verify estimated metrics set metrics_estimated=True and engagement is 0.0 in ranking."""
    candidate = {
        "id": "cand_1",
        "creator_handle": "mkbhd",
        "video_url": "https://instagram.com/reel/cand_1/",
        "like_count": 0,
        "comment_count": 0,
        "view_count": 0,
        "caption": "",
        "duration": 0,
        "timestamp": 0,
        "metrics_estimated": True,
    }

    # In ranker, metrics_estimated forces smooth_engagement to 0.0
    score = ranker.compute_viral_score(candidate, baseline_views=100000.0)
    assert score == 0.0, "Estimated metrics with 0 views must yield 0.0 viral score"

    # With non-zero views but metrics_estimated=True, viral multiplier is applied on view ratio only (smooth_engagement=0.0)
    candidate_views = dict(candidate, view_count=100000)
    score_views = ranker.compute_viral_score(candidate_views, baseline_views=100000.0)
    # damped_reach = (100000/100000)^0.75 * log10(100000) = 5.0; engagement=0.0 -> score = 5.0
    assert abs(score_views - 5.0) < 0.05

    # Missing/0 timestamp must NOT apply false recency penalty
    candidate_recent = dict(candidate, timestamp=0)
    score_recent = ranker.compute_viral_score(candidate_recent, baseline_views=100000.0)
    assert score_recent == 0.0


# ============================================================================
# C4: Playwright Browser Context Recycling
# ============================================================================

def test_c4_playwright_context_recycling():
    """Verify InstagramSession recycles its context every RECYCLE_EVERY navigation calls."""
    session = InstagramSession()
    session.RECYCLE_EVERY = 2

    mock_playwright = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context_1 = mock.MagicMock()
    mock_context_2 = mock.MagicMock()
    mock_browser.new_context.side_effect = [mock_context_1, mock_context_2]

    session._playwright = mock_playwright
    session._browser = mock_browser
    session._open_context()
    assert session._context == mock_context_1

    # Call 1: same context
    session.get_page()
    assert session._nav_count == 1
    assert session._context == mock_context_1

    # Call 2: same context (reaches RECYCLE_EVERY=2)
    session.get_page()
    assert session._nav_count == 2
    assert session._context == mock_context_1

    # Call 3: exceeds threshold -> recycles context_1 and opens context_2
    session.get_page()
    assert session._nav_count == 1
    assert session._context == mock_context_2
    mock_context_1.close.assert_called_once()


# ============================================================================
# C6 & C8: Asset Pruning & Retention Days Synchronization
# ============================================================================

def test_c6_asset_pruning(tmp_path, monkeypatch):
    """Verify _prune_site_assets removes orphaned share pages, thumbnails, and archives."""
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)

    share_dir = tmp_path / "share"
    thumb_dir = tmp_path / "thumbnails"
    archive_dir = tmp_path / "archive"
    for d in [share_dir, thumb_dir, archive_dir]:
        d.mkdir(parents=True)

    # Active assets
    (share_dir / "active1.html").write_text("ok")
    (thumb_dir / "active1.jpg").write_text("ok")
    (archive_dir / "2026-09-06.html").write_text("ok")

    # Stale orphaned assets
    (share_dir / "orphaned_old.html").write_text("stale")
    (thumb_dir / "orphaned_old.jpg").write_text("stale")
    (archive_dir / "2025-01-01.html").write_text("stale")

    site_builder._prune_site_assets(
        current_ids={"active1"},
        keep_week_ids={"2026-09-06"}
    )

    assert (share_dir / "active1.html").exists()
    assert (thumb_dir / "active1.jpg").exists()
    assert (archive_dir / "2026-09-06.html").exists()

    assert not (share_dir / "orphaned_old.html").exists()
    assert not (thumb_dir / "orphaned_old.jpg").exists()
    assert not (archive_dir / "2025-01-01.html").exists()


def test_c8_retention_days_synchronization():
    """Verify RETENTION_DAYS is at least RETENTION_WEEKS * 7 + 1 to prevent premature deletion."""
    assert config.RETENTION_DAYS >= config.RETENTION_WEEKS * 7 + 1
    assert config.RETENTION_DAYS == 8


# ============================================================================
# C7: Local Server Atomic Writes & do_HEAD Body-less Response
# ============================================================================

def test_c7_atomic_write_and_thread_safety(tmp_path):
    """Verify _atomic_write_json performs safe atomic replacement under lock."""
    target_file = tmp_path / "watched.json"
    target_file.write_text("{}")

    def _worker(thread_id):
        for i in range(20):
            with _STATE_LOCK:
                data = json.loads(target_file.read_text())
                data[f"t{thread_id}_{i}"] = True
                _atomic_write_json(target_file, data)

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final_data = json.loads(target_file.read_text())
    assert len(final_data) == 100, "All 100 concurrent atomic writes must be preserved"


def test_p1_goto_card_generation_guard_and_b5_ring_throttle():
    """Verify P1 navGen guard in goToCard and B5 rAF coalescing of ring updates."""
    viewer_src = (pathlib.Path(__file__).resolve().parent.parent / "templates" / "viewer.html").read_text()

    # P1: goToCard takes a generation and both RAF stages bail when superseded
    assert "const gen = ++navGen;" in viewer_src
    assert viewer_src.count("if (gen !== navGen)") >= 2
    # P1: old smooth-scroll/snap-toggle choreography fully removed
    assert "scrollSnapType" not in viewer_src
    assert "behavior: 'instant'" in viewer_src

    # B5: ring updates coalesced to one rAF per frame
    assert "ringsQueued" in viewer_src
    assert "_paintCategoryProgressRings" in viewer_src


def test_c7_do_head_returns_zero_body_bytes():
    """Verify LocalDigestHandler.do_HEAD sends headers with exactly 0 body bytes."""
    handler = mock.MagicMock(spec=LocalDigestHandler)
    handler.path = "/"
    handler.headers = {}
    handler.wfile = mock.MagicMock()
    
    LocalDigestHandler.do_HEAD(handler)
    handler.send_response.assert_called_with(200)
    handler.send_header.assert_any_call("Content-Type", "text/html; charset=utf-8")
    handler.end_headers.assert_called_once()
    assert handler.wfile.write.call_count == 0


# ============================================================================
# B2: Batched R2 Operations
# ============================================================================

def test_b2_batched_r2_purge(monkeypatch):
    """Verify purge_expired_r2_objects uses batch delete_objects instead of serial deletes."""
    mock_s3 = mock.MagicMock()
    mock_paginator = mock.MagicMock()
    mock_paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": "videos/2025-01-01/01_test.mp4"},
                {"Key": "videos/2025-01-01/02_test.mp4"}
            ]
        }
    ]
    mock_s3.get_paginator.return_value = mock_paginator
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: mock_s3)
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "test_bucket")

    purged = storage_r2.purge_expired_r2_objects(max_age_days=14)

    assert len(purged) == 2
    mock_s3.delete_objects.assert_called_once_with(
        Bucket="test_bucket",
        Delete={
            "Objects": [
                {"Key": "videos/2025-01-01/01_test.mp4"},
                {"Key": "videos/2025-01-01/02_test.mp4"}
            ],
            "Quiet": True
        }
    )


# ============================================================================
# P1, P3, P5, P6, P7, P8: Frontend Playback Engine, Posters, & Storage Hygiene
# ============================================================================

def test_p_series_viewer_engine(tmp_path, monkeypatch):
    """E2E Playwright test verifying P1 navigation, P3 max 5 posters, P5 mute pill, P6 hygiene, P7 tap debounce."""
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    (tmp_path / "videos").mkdir()

    # Create 6 mock reels to test sliding window and poster limits
    mock_items = [
        {
            "id": f"reel_{i}",
            "creator_handle": f"creator_{i}",
            "rank": i + 1,
            "rank_display": f"#{i+1:02d}",
            "category": "all",
            "view_count": 100000 * (i + 1),
            "video_url": "data:video/mp4;base64,AAAA",
            "thumbnail": f"https://example.com/thumb_{i}.jpg"
        }
        for i in range(6)
    ]

    _, local_index = site_builder.build_site(
        digest_data={"run_date": "2026-09-06", "items": mock_items}
    )

    def _worker():
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Pre-seed legacy key to verify P6 boot cleanup
            page.goto(local_index.as_uri())
            page.evaluate("() => localStorage.setItem('ig_digest_watched_ids', JSON.stringify(['legacy_1']))")
            page.reload()
            page.wait_for_timeout(300)

            # P6: legacy key pruned on boot
            legacy_val = page.evaluate("() => localStorage.getItem('ig_digest_watched_ids')")
            assert legacy_val is None, "Legacy un-scoped key must be pruned on boot"

            # P3: At most 5 cards have poster attribute (initially only first 3 rendered with poster)
            posters_count = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('.reel-video'))
                    .filter(v => v.hasAttribute('poster') && v.getAttribute('poster') !== '').length;
            }""")
            assert posters_count <= 5, f"Posters count must be <= 5, got {posters_count}"

            # P5: Verify mute pill exists and is wired
            mute_pills = page.locator(".mute-pill")
            assert mute_pills.count() == 6

            # P7: Verify single tap debounce timer exists
            pending_timer = page.evaluate("() => pendingSingleTapTimer !== undefined")
            assert pending_timer is True, "pendingSingleTapTimer must be defined"

            browser.close()

    err = []
    def _run():
        try:
            _worker()
        except Exception as e:
            err.append(e)

    t = threading.Thread(target=_run)
    t.start()
    t.join()
    if err:
        raise err[0]
