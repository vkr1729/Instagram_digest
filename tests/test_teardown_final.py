"""
test_teardown_final.py — Regression tests for FINAL_TEARDOWN_REVIEW_AND_FIXES.md.

Covers the P1/P2 fixes functionally and pins the P3 string-level contracts.
Zero browser/network: all fixtures are tmp dirs, stubs, or source reads.
"""

import io
import json
import os
import stat
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

import config
import extractor
import local_server
import main as main_module
import notifier
import ranker
import site_builder
import storage_r2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import topup_digest


def _reel(rid, handle, rank=1):
    return {
        "id": rid,
        "url": f"https://www.instagram.com/reel/{rid}/",
        "creator_handle": handle,
        "caption": f"cap {rid}",
        "view_count": 50000,
        "like_count": 1000,
        "comment_count": 50,
        "duration": 30,
        "timestamp": int(time.time()),
        "thumbnail": "",
        "video_cdn_url": "",
        "metrics_estimated": False,
        "rank": rank,
        "rank_display": f"#{rank:02d}",
    }


# --------------------------------------------------------------------------
# P1-1: dry-run performs zero mutations (no site compile at all)
# --------------------------------------------------------------------------

class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def validate(self):
        return True


def test_p1_1_dry_run_skips_site_compile(tmp_path, monkeypatch):
    site_dir = tmp_path / "site"
    (site_dir / "share").mkdir(parents=True)
    sentinel = site_dir / "share" / "LIVE.html"
    sentinel.write_text("live")
    monkeypatch.setattr(config, "SITE_DIR", site_dir)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "data" / "top100_digest.json")
    monkeypatch.setattr(config, "BLACKLIST_FILE", tmp_path / "data" / "blacklist.json")

    sources = [
        {"handle": "alice", "category": "niche", "enabled": True},
        {"handle": "bob", "category": "health", "enabled": True},
    ]
    monkeypatch.setattr(extractor, "load_sources", lambda: sources)
    monkeypatch.setattr(extractor, "InstagramSession", lambda: _FakeSession())
    monkeypatch.setattr("recommendations.refresh_recommendations", lambda **kw: [])
    monkeypatch.setattr("recommendations.load_recommended_creators", lambda: [])
    monkeypatch.setattr(extractor, "human_pause", lambda **k: 0.0)

    def _fake_extract(*, handle, max_reels, days_back, fast_mode, session):
        return [_reel(f"{handle}_r{i}", handle) for i in range(3)]

    monkeypatch.setattr(extractor, "extract_creator_reels", _fake_extract)
    monkeypatch.setattr(
        extractor, "extract_single_reel_metadata",
        lambda r, session=None: {**r, "timestamp": int(time.time())},
    )
    built = []
    monkeypatch.setattr(
        site_builder, "build_site",
        lambda **kw: built.append(kw) or (site_dir / "index.html", site_dir / "local.html"),
    )

    ret = main_module._run_full_sync(
        dry_run=True, deploy=False, days_back=7,
        limit_per_creator=15, since_timestamp=None,
    )
    assert ret == 0
    assert built == [], "dry-run must never reach build_site"
    assert sentinel.read_text() == "live"
    assert "Dry-run: skipping site compile" in Path("main.py").read_text()


# --------------------------------------------------------------------------
# P1-3/P1-4/P2-7: top-up gates, pacing, safe glob
# --------------------------------------------------------------------------

def test_p1_3_topup_drops_unplayables_and_maps_build(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", data_dir / "top100_digest.json")
    monkeypatch.setattr(config, "DIGESTS_DIR", data_dir / "digests")
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "LAST_RUN_FILE", data_dir / "last_run.json")
    monkeypatch.setattr(config, "BLACKLIST_FILE", data_dir / "blacklist.json")

    old_week = "2026-09-11"
    kept = _reel("k2", "carol")
    (data_dir / "top100_digest.json").write_text(json.dumps({
        "run_date": old_week, "count": 2,
        "items": [_reel("k1", "carol"), kept],
    }))
    (data_dir / "candidates_cache.json").write_text(json.dumps({
        "candidates": [_reel("n1", "dave")],
    }))
    old_videos = tmp_path / "videos" / old_week
    old_videos.mkdir(parents=True)
    (old_videos / "05_carol_k2.mp4").write_bytes(b"x" * 60000)

    sources = [
        {"handle": "carol", "category": "niche", "enabled": True},
        {"handle": "dave", "category": "health", "enabled": True},
    ]
    monkeypatch.setattr(extractor, "load_sources", lambda: sources)
    monkeypatch.setattr(extractor, "InstagramSession", lambda: _FakeSession())
    monkeypatch.setattr("recommendations.refresh_recommendations", lambda **kw: [])
    monkeypatch.setattr("recommendations.load_recommended_creators", lambda: [])
    pauses = []
    monkeypatch.setattr(extractor, "human_pause", lambda **k: pauses.append(1) or 0.0)
    monkeypatch.setattr(
        extractor, "extract_single_reel_metadata",
        lambda r, session=None: {**r, "timestamp": int(time.time())},
    )

    def _fake_download(url, path, video_cdn_url=None, session=None, **k):
        if "n1" in str(path):
            return False
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"y" * 60000)
        return True

    monkeypatch.setattr(extractor, "download_reel_video", _fake_download)
    monkeypatch.setattr(
        storage_r2, "upload_reel_to_r2",
        lambda local_file, week_id, key_name, existing_keys=None: (
            "" if "n1" in key_name else f"https://cdn.test/videos/{week_id}/{key_name}"
        ),
    )
    monkeypatch.setattr(storage_r2, "get_existing_r2_keys", lambda prefix="videos/": set())
    built = []
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: built.append(kw))

    ret = topup_digest.topup_digest(watched_count=1, new_week_id="2026-09-14", deploy=False)
    assert ret == 0
    assert pauses, "P1-4: enrichment must be paced"
    saved = json.loads((data_dir / "top100_digest.json").read_text())
    assert [i["id"] for i in saved["items"]] == ["k2"]
    assert saved["items"][0]["r2_url"].startswith("https://cdn.test/")
    assert len(built) == 1 and set(built[0]["r2_uploaded_urls"]) == {"k2"}


def test_p2_7_topup_delete_glob_is_sanitized():
    src = (ROOT / "scripts" / "topup_digest.py").read_text()
    assert 'rid = main._safe_component(item.get("id"), "")' in src
    assert "with main._pipeline_file_lock():" in src


# --------------------------------------------------------------------------
# P2-10/P3-33: ranker cap + malformed skip
# --------------------------------------------------------------------------

def test_p2_10_ranker_never_exceeds_top_n():
    sources = [{"handle": f"c{i}", "category": "niche"} for i in range(10)]
    candidates = []
    for i in range(10):
        for j in range(3):
            candidates.append({
                "id": f"c{i}_r{j}", "creator_handle": f"c{i}",
                "view_count": 10000 + j * 100, "like_count": 100, "comment_count": 5,
            })
    ranked = ranker.rank_top_reels(candidates, sources, top_n=5, max_per_creator=4,
                                   shuffle=False)
    assert len(ranked) == 5
    assert len({r["creator_handle"] for r in ranked}) == 5


def test_p3_33_ranker_skips_malformed_candidates():
    sources = [{"handle": "ok", "category": "niche"}]
    candidates = [
        {"foo": 1},
        {"id": "no-handle"},
        {"creator_handle": "no-id"},
        {"id": "good", "creator_handle": "ok", "view_count": 999,
         "like_count": 10, "comment_count": 1},
    ]
    ranked = ranker.rank_top_reels(candidates, sources, top_n=5, shuffle=False)
    assert [r["id"] for r in ranked] == ["good"]


# --------------------------------------------------------------------------
# P2-8/P3-40: purge accounting + fail-closed quota
# --------------------------------------------------------------------------

def test_p2_8_purge_reports_only_confirmed_deletes(monkeypatch):
    from datetime import datetime, timedelta, timezone

    class _Paginator:
        def paginate(self, Bucket, Prefix=None, **k):
            return [{"Contents": [
                {"Key": "videos/2020-01-01/01_a.mp4",
                 "LastModified": datetime.now(timezone.utc) - timedelta(days=30)},
                {"Key": "videos/2020-01-01/02_b.mp4",
                 "LastModified": datetime.now(timezone.utc) - timedelta(days=30)},
            ]}]

    class _S3:
        def get_paginator(self, name):
            return _Paginator()

        def delete_objects(self, Bucket, Delete):
            assert Delete["Quiet"] is False
            return {"Deleted": [{"Key": "videos/2020-01-01/01_a.mp4"}],
                    "Errors": [{"Key": "videos/2020-01-01/02_b.mp4", "Message": "boom"}]}

    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: _S3())
    assert storage_r2.purge_expired_r2_objects(max_age_days=8) == [
        "videos/2020-01-01/01_a.mp4"]


def test_p3_40_quota_refuses_on_r2_outage(monkeypatch):
    from botocore.exceptions import ClientError

    class _BadPaginator:
        def paginate(self, **k):
            raise ClientError({"Error": {"Code": " boom"}}, "ListObjects")

    class _BadS3:
        def get_paginator(self, name):
            return _BadPaginator()

    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: _BadS3())
    assert storage_r2.get_bucket_storage_usage() == (-1, -1)
    monkeypatch.setattr(storage_r2, "get_bucket_storage_usage", lambda: (-1, -1))
    assert storage_r2.check_preflight_quota(estimated_new_bytes=1024) is False


# --------------------------------------------------------------------------
# P2-9/P3-26: secure cookie writes + pad validation
# --------------------------------------------------------------------------

def test_p2_9_secure_write_is_0600_from_first_byte(tmp_path):
    from cookie_exporter import _secure_write_text
    p = tmp_path / "cookies.txt"
    p.write_text("old")
    os.chmod(p, 0o644)
    _secure_write_text(p, "sessionid=abc")
    assert p.read_text() == "sessionid=abc"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    fresh = tmp_path / "new.json"
    _secure_write_text(fresh, "{}")
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600


def test_p3_26_cookie_decrypt_validates_padding(monkeypatch):
    """Pad validation works without the real cryptography lib (stubbed Cipher)."""
    import types
    from cookie_exporter import decrypt_chrome_cookie

    class _FakeDecryptor:
        def __init__(self, out):
            self._out = out

        def update(self, data):
            return self._out

        def finalize(self):
            return b""

    class _FakeCipher:
        def __init__(self, out):
            self._out = out

        def decryptor(self):
            return _FakeDecryptor(self._out)

    def _fake_cipher_factory(out):
        return lambda *a, **k: _FakeCipher(out)

    backends = types.ModuleType("cryptography.hazmat.backends")
    backends.default_backend = lambda: None
    primitives = types.ModuleType("cryptography.hazmat.primitives.ciphers")
    primitives.Cipher = None
    primitives.algorithms = types.SimpleNamespace(AES=lambda k: None)
    primitives.modes = types.SimpleNamespace(CBC=lambda iv: None)
    pkg1 = types.ModuleType("cryptography")
    pkg2 = types.ModuleType("cryptography.hazmat")
    pkg3 = types.ModuleType("cryptography.hazmat.primitives")
    monkeypatch.setitem(sys.modules, "cryptography", pkg1)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat", pkg2)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.backends", backends)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives", pkg3)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.ciphers", primitives)

    good = b"\x00" * 32 + b"sessionid!" + bytes([6]) * 6
    primitives.Cipher = _fake_cipher_factory(good)
    assert decrypt_chrome_cookie(b"v10" + b"\x00" * 64, b"k" * 16, b" " * 16) == "sessionid!"

    bad_len = b"\x00" * 40 + bytes([99])
    primitives.Cipher = _fake_cipher_factory(bad_len)
    assert decrypt_chrome_cookie(b"v10" + b"\x00" * 64, b"k" * 16, b" " * 16) == ""

    bad_bytes = b"\x00" * 32 + b"sessionid!" + b"\x06\x06\x06\x06\x06\x07"
    primitives.Cipher = _fake_cipher_factory(bad_bytes)
    assert decrypt_chrome_cookie(b"v10" + b"\x00" * 64, b"k" * 16, b" " * 16) == ""


# --------------------------------------------------------------------------
# P3-22/P3-19: config parsing + CLI validation
# --------------------------------------------------------------------------

def test_p3_22_env_int_clamps_and_defaults(monkeypatch):
    monkeypatch.setenv("TD_INT", "notanint")
    assert config._env_int("TD_INT", 7, 1, 10) == 7
    monkeypatch.setenv("TD_INT", "999")
    assert config._env_int("TD_INT", 7, 1, 10) == 10
    monkeypatch.setenv("TD_INT", "-3")
    assert config._env_int("TD_INT", 7, 1, 10) == 1
    monkeypatch.setenv("TD_FLOAT", "junk")
    assert config._env_float("TD_FLOAT", 1.5) == 1.5


def test_p3_19_negative_expand_errors(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--expand", "-5"])
    with pytest.raises(SystemExit) as e:
        main_module.main()
    assert e.value.code == 2


# --------------------------------------------------------------------------
# P2-2/P2-3/P3-30: builder URL preference, archive assets, no caller mutation
# --------------------------------------------------------------------------

def _odd_digest():
    return {
        "run_date": "2026-09-14",
        "items": [{
            "id": "x1", "creator_handle": "Foo Bar", "creator_name": "Foo",
            "category": "niche", "rank": 1, "rank_display": "#01",
            "caption": "hi", "thumbnail": "", "poster": "",
            "r2_url": "https://cdn.test/videos/2026-09-14/01_FooBar_x1.mp4",
            "video_url": "https://cdn.test/videos/2026-09-14/01_FooBar_x1.mp4",
        }],
    }


def test_p2_2_builder_prefers_persisted_r2_url(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")
    digest = _odd_digest()
    site_builder.build_site(digest_data=digest, r2_uploaded_urls=None)
    html = (tmp_path / "index.html").read_text()
    assert "https://cdn.test/videos/2026-09-14/01_FooBar_x1.mp4" in html
    assert "Foo Bar_" not in html and "Foo%20Bar" not in html


def test_p2_3_archive_pages_get_parent_relative_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")
    site_builder.build_site(digest_data=_odd_digest(), r2_uploaded_urls=None)
    assert (tmp_path / "archive" / "sw.js").exists()
    for name in ("2026-09-14.html", "local_2026-09-14.html"):
        html = (tmp_path / "archive" / name).read_text()
        assert '"../sw.js' in html
        assert '"../manifest.webmanifest"' in html
    assert '"./sw.js' in (tmp_path / "index.html").read_text()


def test_p3_30_builder_does_not_mutate_caller_digest(tmp_path, monkeypatch):
    import copy
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")
    digest = _odd_digest()
    before = copy.deepcopy(digest)
    site_builder.build_site(
        digest_data=digest,
        r2_uploaded_urls={"x1": "https://cdn.test/videos/2026-09-14/01_FooBar_x1.mp4"},
    )
    assert digest == before
    payload = json.loads((tmp_path / "data.json").read_text())
    assert payload["items"][0]["r2_url"].startswith("https://cdn.test/")


# --------------------------------------------------------------------------
# P3-13/P2-4: bounded bodies + following guard
# --------------------------------------------------------------------------

def test_p3_13_read_json_body_guards_and_caps():
    class _H:
        def __init__(self, headers, body):
            self.headers = headers
            self.rfile = io.BytesIO(body)

    assert local_server._read_json_body(_H({"Content-Length": "junk"}, b"{}")) == {}
    assert local_server._read_json_body(_H({}, b"{}")) == {}
    assert local_server._read_json_body(
        _H({"Content-Length": "7"}, b'{"a":1}')) == {"a": 1}
    with pytest.raises(ValueError):
        local_server._read_json_body(
            _H({"Content-Length": str(5 * 1024 * 1024)}, b"x"))


def test_p2_4_following_sync_is_single_flight(monkeypatch):
    import http.client
    calls = []
    monkeypatch.setattr(
        extractor, "sync_following_accounts",
        lambda force=False: (calls.append(force), time.sleep(0.3))[0],
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), local_server.LocalDigestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_port

        def _post():
            # http.client: no proxy env interference (urllib honors proxies).
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/api/sync-following", body=b"{}",
                         headers={"Content-Length": "2"})
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()
            return data

        with local_server._FOLLOWING_LOCK:
            local_server._FOLLOWING_RUNNING = True
        try:
            assert _post()["status"] == "already_running"
        finally:
            with local_server._FOLLOWING_LOCK:
                local_server._FOLLOWING_RUNNING = False

        assert _post()["success"] is True
        for _ in range(100):
            with local_server._FOLLOWING_LOCK:
                if not local_server._FOLLOWING_RUNNING:
                    break
            time.sleep(0.05)
        assert calls == [True]
    finally:
        srv.shutdown()
        srv.server_close()


# --------------------------------------------------------------------------
# P3-34: plaintext mail must not contain HTML entities
# --------------------------------------------------------------------------

def test_p3_34_failure_plaintext_is_not_html_escaped():
    msg = notifier.build_failure_alert_message("a&b <c>", 1)
    plain = next(p for p in msg.get_payload()
                 if p.get_content_type() == "text/plain").get_payload(decode=True).decode()
    html_part = next(p for p in msg.get_payload()
                     if p.get_content_type() == "text/html").get_payload(decode=True).decode()
    assert "a&b <c>" in plain and "&amp;" not in plain
    assert "a&amp;b" in html_part


# --------------------------------------------------------------------------
# String pins: client, worker, SW, templates, docs
# --------------------------------------------------------------------------

def _src(*parts):
    return (ROOT.joinpath(*parts)).read_text()


def test_p1_2_play_failure_affordance_selector_exists():
    js = _src("templates", "partials", "player.js")
    feed = _src("templates", "partials", "feed.html")
    assert ".play-indicator'" not in js.replace(".play-pause-indicator", "")
    assert "play-pause-indicator" in feed


def test_p2_1_outbox_network_failure_backs_off():
    js = _src("templates", "partials", "player.js")
    assert "op.attempts = (op.attempts || 0) + 1;" in js
    assert "if (navigator.onLine === false) return;" in js


def test_p2_11_client_surfaces_pending_archive():
    assert "Saving…" in _src("templates", "partials", "player.js")


def test_p2_11_worker_retries_telegram_once():
    worker = _src("cloudflare", "worker.js")
    assert "setTimeout(r, 5000)" in worker
    assert "telegram_message_id IS NULL" in worker


def test_p2_5_no_tls_bypass_anywhere():
    for f in ["extractor.py", "main.py", "local_server.py", "site_builder.py"]:
        assert "no-check-certificates" not in _src(f)


def test_p2_6_feed_fallback_reuses_session():
    assert '"creator_handle": h}, session=session)' in _src("extractor.py")


def test_p3_1_outbox_coalesces_post_delete():
    assert "firstById" in _src("templates", "partials", "player.js")


def test_p3_2_share_selector_escaped():
    assert "CSS.escape(reelId)" in _src("templates", "partials", "player.js")


def test_p3_3_switch_week_validated():
    js = _src("templates", "partials", "player.js")
    assert "javascript:" in js and "u.origin !== location.origin" in js


def test_p3_4_removed_cards_unobserved():
    assert "observer.unobserve(c)" in _src("templates", "partials", "player.js")


def test_p3_5_advance_matches_toast():
    js = _src("templates", "partials", "player.js")
    assert "}, 500);" in js
    assert "Next reel in 0.5s" in _src("templates", "partials", "feed.html")


def test_p3_6_download_prescan_batched():
    assert "existing.has(clean)" in _src("templates", "partials", "player.js")


def test_p3_7_worker_bounds_actual_body():
    assert "request.arrayBuffer()" in _src("cloudflare", "worker.js")


def test_p3_8_worker_binds_id_to_source():
    assert "ID_SOURCE_MISMATCH" in _src("cloudflare", "worker.js")


def test_p3_10_worker_cron_parallel_heads():
    assert "i += 20" in _src("cloudflare", "worker.js")


def test_p3_11_sw_image_cache_capped():
    assert "keys.length > 400" in _src("templates", "sw.js")


def test_p3_12_sw_precache_timeout():
    assert "setTimeout(() => ctl.abort(), 30000)" in _src("templates", "sw.js")


def test_p3_14_no_wildcard_cors_on_mutating_posts():
    # Only the video/static GET paths may send ACAO:* (3 sites).
    assert _src("local_server.py").count('Access-Control-Allow-Origin", "*"') == 3


def test_p3_15_all_416_carry_content_range():
    assert _src("local_server.py").count('Content-Range", f"bytes */{file_size}') >= 4


def test_p3_16_video_stat_guarded():
    src = _src("local_server.py")
    assert "try:\n            file_size = video_path.stat().st_size" in src


def test_p3_17_dead_block_removed():
    # The old dead "zero playable reels" log line was repurposed: the
    # empty-save guard now aborts (rc 2) instead of clobbering the live
    # digest, and parks everything in the upload outbox for --reconcile.
    src = _src("main.py")
    assert "Zero playable reels after upload phase" in src
    assert "outbox holds" in src
    assert "shortfall_paused" in src


def test_p3_20_limit_help_is_honest():
    assert "5/creator" in _src("main.py")


def test_p3_21_feed_retry_heartbeat():
    assert '"__feed__"' in _src("main.py")


def test_p3_23_cdn_download_capped():
    src = _src("extractor.py")
    assert "250MB cap" in src and "text/html" in src


def test_p3_24_estimated_fields_flagged():
    src = _src("extractor.py")
    assert '"timestamp_estimated": True' in src
    assert '"view_count_estimated": True' in src


def test_p3_25_cookie_python_fallback():
    assert "_cookie_python" in _src("extractor.py")
    assert "_cookie_python" in _src("local_server.py")


def test_p3_27_share_manifest_writes_atomic():
    src = _src("site_builder.py")
    assert "durable_write_text(share_dir" in src
    assert 'durable_write_json(config.SITE_DIR / "manifest.webmanifest"' in src


def test_p3_28_thumb_tmp_sanitized():
    assert "_safe_component(reel_id" in _src("site_builder.py")


def test_p3_29_ffmpeg_failures_logged():
    assert "Thumbnail generation failed" in _src("site_builder.py")


def test_p3_31_deploy_timeouts():
    assert _src("site_builder.py").count("timeout=300") >= 6


def test_p3_32_local_base_url_configurable():
    assert "LOCAL_BASE_URL" in _src("site_builder.py")


def test_p2_2_builder_sanitizer_present():
    assert "_safe_component(item.get('creator_handle')" in _src("site_builder.py")


def test_p3_35_channel_class_sanitized():
    assert "catCls" in _src("templates", "channels.html")


def test_p3_36_dashboard_esc_covers_quote():
    assert "\"'\": \"&#39;\"" in _src("templates", "dashboard.html")


def test_p3_37_modals_divs_balanced():
    import re
    html = _src("templates", "partials", "modals.html")
    assert len(re.findall(r"<div\b", html)) == html.count("</div>")


def test_p3_38_pin_docs_honest():
    readme = _src("README.md")
    assert "PBKDF2" in readme and "not access control" in readme


def test_p3_39_tracking_claim_scoped():
    assert "only third-party request" in _src("README.md")


# --------------------------------------------------------------------------
# FINAL AUDIT (2026-09-18): P0/P1/P2 regression tests
# --------------------------------------------------------------------------

def test_audit_p0_jit_purge_keeps_live_week(monkeypatch):
    """PY-P0-1: JIT purge must keep the week the persisted digest points at."""
    from datetime import datetime, timezone

    live_week = "2026-09-10"
    new_week = "2026-09-17"

    def _obj(key):
        return {"Key": key, "Size": 1024, "LastModified": datetime.now(timezone.utc)}

    class _FakePaginator:
        def paginate(self, Bucket, Prefix=None, **kw):
            return [{"Contents": [
                _obj(f"videos/{live_week}/01_a_x.mp4"),
                _obj(f"videos/{new_week}/01_b_y.mp4"),
                _obj("videos/2026-09-03/01_old_z.mp4"),
            ]}]

    class _FakeS3:
        def get_paginator(self, name):
            return _FakePaginator()

        def delete_objects(self, Bucket, Delete, **kw):
            keys = [o["Key"] for o in Delete["Objects"]]
            return {"Deleted": [{"Key": k} for k in keys], "Errors": []}

    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: _FakeS3())
    purged = storage_r2.purge_previous_weeks_videos(
        current_week_id=new_week, keep_week_ids={live_week})
    assert f"videos/{live_week}/01_a_x.mp4" not in purged
    assert f"videos/{new_week}/01_b_y.mp4" not in purged
    assert "videos/2026-09-03/01_old_z.mp4" in purged


def test_audit_p1_corrupt_sources_quarantined(tmp_path, monkeypatch):
    """PY-P1-3: torn sources.json is quarantined, not silently replaced."""
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "sources.json").write_text('{"handles": [broken', encoding="utf-8")
    assert extractor.load_sources() == []
    assert len(list(tmp_path.glob("sources.json.corrupt-*"))) == 1


def test_audit_p1_nonlist_sources_quarantined(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "sources.json").write_text('{"handles": ["a"]}', encoding="utf-8")
    assert extractor.load_sources() == []
    assert len(list(tmp_path.glob("sources.json.corrupt-*"))) == 1


def test_audit_p1_handleless_sources_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "sources.json").write_text(json.dumps([
        {"handle": "alice", "enabled": True},
        {"name": "no-handle"},
        "junk",
    ]), encoding="utf-8")
    loaded = extractor.load_sources()
    assert [s["handle"] for s in loaded] == ["alice"]


def test_audit_p1_topup_quarantines_corrupt_digest(tmp_path, monkeypatch):
    """PY-P1-4: corrupt active digest is quarantined; topup exits 1."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "data" / "top100_digest.json")
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / "top100_digest.json").write_text('{"items": [broken', encoding="utf-8")
    ret = topup_digest.topup_digest(watched_count=1, new_week_id="2026-09-14", deploy=False)
    assert ret == 1
    assert len(list((tmp_path / "data").glob("top100_digest.json.corrupt-*"))) == 1


def test_audit_p1_topup_drops_stale_backfill(tmp_path, monkeypatch):
    """PY-P1-8: undated/stale backfill reels never reach the new digest."""
    import time as _time
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", data_dir / "top100_digest.json")
    monkeypatch.setattr(config, "DIGESTS_DIR", data_dir / "digests")
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "LAST_RUN_FILE", data_dir / "last_run.json")
    monkeypatch.setattr(config, "BLACKLIST_FILE", data_dir / "blacklist.json")

    old_week = "2026-09-11"
    kept = _reel("k2", "carol")
    (data_dir / "top100_digest.json").write_text(json.dumps({
        "run_date": old_week, "count": 2,
        "items": [_reel("k1", "carol"), kept],
    }))
    now = int(_time.time())
    fresh = dict(_reel("n1", "dave"), timestamp=now - 3600)
    stale = dict(_reel("n2", "erin"), timestamp=now - 30 * 86400)
    undated = dict(_reel("n3", "fred"))
    undated["timestamp"] = 0
    (data_dir / "candidates_cache.json").write_text(json.dumps({
        "candidates": [fresh, stale, undated],
    }))
    old_videos = tmp_path / "videos" / old_week
    old_videos.mkdir(parents=True)
    (old_videos / "05_carol_k2.mp4").write_bytes(b"x" * 60000)

    sources = [
        {"handle": "carol", "category": "niche", "enabled": True},
        {"handle": "dave", "category": "health", "enabled": True},
        {"handle": "erin", "category": "health", "enabled": True},
        {"handle": "fred", "category": "health", "enabled": True},
    ]
    monkeypatch.setattr(extractor, "load_sources", lambda: sources)
    monkeypatch.setattr(extractor, "InstagramSession", lambda: _FakeSession())
    monkeypatch.setattr("recommendations.refresh_recommendations", lambda **kw: [])
    monkeypatch.setattr("recommendations.load_recommended_creators", lambda: [])
    monkeypatch.setattr(extractor, "human_pause", lambda **k: 0.0)
    monkeypatch.setattr(
        extractor, "extract_single_reel_metadata",
        lambda r, session=None: {**r, "timestamp": r.get("timestamp") or 0},
    )
    monkeypatch.setattr(
        extractor, "download_reel_video",
        lambda url, path, video_cdn_url=None, session=None, **k: (
            Path(path).parent.mkdir(parents=True, exist_ok=True),
            Path(path).write_bytes(b"y" * 60000), True)[2])
    monkeypatch.setattr(
        storage_r2, "upload_reel_to_r2",
        lambda local_file, week_id, key_name, existing_keys=None:
            f"https://cdn.test/videos/{week_id}/{key_name}")
    monkeypatch.setattr(storage_r2, "get_existing_r2_keys", lambda prefix="videos/": set())
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: None)

    ret = topup_digest.topup_digest(watched_count=1, new_week_id="2026-09-14", deploy=False)
    assert ret == 0
    saved = json.loads((data_dir / "top100_digest.json").read_text())
    assert [i["id"] for i in saved["items"]] == ["k2", "n1"]


def test_audit_p1_deploy_refuses_local_bundle(tmp_path, monkeypatch):
    """PY-P1-9: deploy_to_gh_pages refuses an all-local /videos/ bundle."""
    (tmp_path / "data.json").write_text(json.dumps({
        "run_date": "2026-09-06", "count": 1,
        "items": [{"id": "x", "video_url": "/videos/2026-09-06/01_a_x.mp4"}],
    }), encoding="utf-8")
    assert site_builder._pages_bundle_is_remote(tmp_path) is False
    assert site_builder.deploy_to_gh_pages(site_dir=tmp_path) is False


def test_audit_p2_generated_at_emitted(tmp_path, monkeypatch):
    """PY-P2-6: digest batch carries generated_at for the iOS decoder."""
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "top100_digest.json")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")
    ranker.save_digest_batch([_reel("r1", "alice")], run_date="2026-09-06")
    payload = json.loads((tmp_path / "top100_digest.json").read_text())
    assert payload["generated_at"] == payload["created_at"]


def test_audit_p2_corrupt_candidates_cache_quarantined(tmp_path, monkeypatch):
    """PY-P2-2: torn candidates_cache.json is quarantined, not retried forever."""
    import main as main_module
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "digests")
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    sentinel = tmp_path / "top100_digest.json"
    sentinel.write_text('{"sentinel": true}')
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", sentinel)
    (tmp_path / "candidates_cache.json").write_text('{"candidates": [broken', encoding="utf-8")
    monkeypatch.setattr(extractor, "load_sources", lambda: [
        {"handle": "alice", "category": "entertainment", "enabled": True},
    ])
    monkeypatch.setattr(extractor, "InstagramSession", lambda: _FakeSession())
    monkeypatch.setattr("recommendations.refresh_recommendations", lambda **kw: [])
    monkeypatch.setattr("recommendations.load_recommended_creators", lambda: [])
    monkeypatch.setattr(extractor, "extract_creator_reels", lambda **kw: [])
    monkeypatch.setattr(extractor, "extract_single_reel_metadata", lambda r, session=None: r)

    rc = main_module.run_full_sync(dry_run=False, deploy=False, days_back=7, limit_per_creator=15)
    assert rc == 2
    assert not (tmp_path / "candidates_cache.json").exists()
    assert len(list(tmp_path.glob("candidates_cache.json.corrupt-*"))) == 1
    assert json.loads(sentinel.read_text()) == {"sentinel": True}
