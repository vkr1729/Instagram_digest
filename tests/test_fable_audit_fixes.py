"""test_fable_audit_fixes.py — Regression tests for the Fable hardening audit.

Each test inverts one audit probe: where the probe passed by reproducing a
defect, the test below fails if the defect returns. Covers the two P0s
(expand week drift, cross-process pipeline lock), the P1s (ops-origin XSS,
GET /retrigger side effect, soft-block false positives, checkpoint resume
duplicates) and the P2s (checkpoint quarantine, rank collisions, video
route traversal + status line, durable sources writes, stealth coherence,
pruner reporting). No network, no browser.
"""

import http.client
import json
import socket
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import config
import extractor
import local_server
import main
import site_builder
import storage_r2

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
YESTERDAY = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DIGESTS_DIR", tmp_path / "data" / "digests")
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "data" / "top100_digest.json")
    monkeypatch.setattr(config, "LAST_RUN_FILE", tmp_path / "data" / "last_run.json")
    monkeypatch.setattr(config, "SITE_DIR", tmp_path / "site")
    monkeypatch.setattr(config, "R2_PUBLIC_DOMAIN", "https://r2.example")
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "")
    (tmp_path / "data" / "digests").mkdir(parents=True)
    monkeypatch.setattr(extractor, "InstagramSession", _Session)
    monkeypatch.setattr(storage_r2, "get_existing_r2_keys", lambda prefix="videos/": set())
    monkeypatch.setattr(storage_r2, "purge_expired_r2_objects", lambda **k: [])
    monkeypatch.setattr(storage_r2, "purge_unreferenced_r2_videos", lambda: [])
    monkeypatch.setattr(storage_r2, "purge_expired_local_videos", lambda **k: [])

    def _dl(url, out_path, video_cdn_url=None):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x" * 1024)
        return True

    monkeypatch.setattr(extractor, "download_reel_video", _dl)
    monkeypatch.setattr(
        storage_r2, "upload_reel_to_r2",
        lambda local_file, week_id, key_name=None, existing_keys=None:
            f"https://r2.example/videos/{week_id}/{key_name}",
    )


# P0: sync persists the real object URL on the digest -------------------------
def test_sync_persists_r2_url_on_digest(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    ranked = [{"id": "AAA", "creator_handle": "h1", "rank": 1, "rank_display": "#01",
               "url": "https://www.instagram.com/reel/AAA/"}]
    (config.DATA_DIR / f"sync_progress_{TODAY}.json").write_text(json.dumps({
        "version": 1, "week_id": TODAY, "days_back": 7, "limit_per_creator": 15,
        "since_timestamp": None, "stage": "ranked", "ranked": ranked,
    }))
    captured = {}
    monkeypatch.setattr(site_builder, "build_site",
                        lambda **kw: captured.update(kw) or (tmp_path, tmp_path))
    monkeypatch.setattr(main, "_alert_sync_abort", lambda *a, **k: None)
    monkeypatch.setattr(main, "MIN_DEPLOY_ITEMS", 1)
    assert main.run_full_sync(dry_run=False, deploy=False) == 0
    saved = json.loads(config.DIGEST_BATCH_FILE.read_text())["items"]
    assert saved[0]["r2_url"].startswith(f"https://r2.example/videos/{TODAY}/")
    assert saved[0]["video_url"] == saved[0]["r2_url"]


# P0: expand on a later day keeps the digest's week ---------------------------
def test_expand_on_later_day_keeps_digest_week(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    existing = [{"id": f"OLD{i}", "creator_handle": "h", "rank": i, "rank_display": f"#{i:02d}",
                 "url": f"https://www.instagram.com/reel/OLD{i}/"} for i in range(1, 4)]
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": YESTERDAY, "items": existing}))
    monkeypatch.setattr(extractor, "extract_external_reels_from_feed",
                        lambda **kw: [{"id": "NEW1", "creator_handle": "n",
                                       "url": "https://www.instagram.com/reel/NEW1/"}])
    captured = {}
    monkeypatch.setattr(site_builder, "build_site",
                        lambda **kw: captured.update(kw) or (tmp_path, tmp_path))
    assert main.run_expand(target_count=1, deploy=False) == 0
    urls = captured["r2_uploaded_urls"]
    for i in range(1, 4):
        assert f"/videos/{YESTERDAY}/" in urls[f"OLD{i}"]
    assert json.loads(config.DIGEST_BATCH_FILE.read_text())["run_date"] == YESTERDAY


# P1: checkpoint reels already in the digest are not re-appended --------------
def test_expand_resume_filters_ids_already_in_digest(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": TODAY, "items": [
        {"id": "X", "creator_handle": "h", "rank": 1, "rank_display": "#01",
         "url": "https://www.instagram.com/reel/X/"}]}))
    (config.DATA_DIR / f"expand_checkpoint_{TODAY}.json").write_text(json.dumps({
        "version": 1, "target_count": 1,
        "reels": [{"id": "X", "creator_handle": "h", "url": "https://www.instagram.com/reel/X/"}]}))
    monkeypatch.setattr(extractor, "extract_external_reels_from_feed",
                        lambda **kw: [{"id": "NEW1", "creator_handle": "n",
                                       "url": "https://www.instagram.com/reel/NEW1/"}])
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: (tmp_path, tmp_path))
    assert main.run_expand(target_count=1, deploy=False) == 0
    ids = [i["id"] for i in json.loads(config.DIGEST_BATCH_FILE.read_text())["items"]]
    assert ids == ["X", "NEW1"]


# P2: new ranks start after the highest existing rank, not len()+1 ------------
def test_expand_assigns_max_rank_plus_one(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": TODAY, "items": [
        {"id": f"O{r}", "creator_handle": "h", "rank": r, "rank_display": f"#{r:02d}",
         "url": f"https://www.instagram.com/reel/O{r}/"} for r in (1, 2, 4)]}))
    monkeypatch.setattr(extractor, "extract_external_reels_from_feed",
                        lambda **kw: [{"id": "N", "creator_handle": "n",
                                       "url": "https://www.instagram.com/reel/N/"}])
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: (tmp_path, tmp_path))
    assert main.run_expand(target_count=1, deploy=False) == 0
    ranks = sorted(i["rank"] for i in json.loads(config.DIGEST_BATCH_FILE.read_text())["items"])
    assert ranks == [1, 2, 4, 5]


# P2: corrupt expand checkpoint is quarantined, not silently deleted ----------
def test_corrupt_expand_checkpoint_is_quarantined(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": TODAY, "items": [
        {"id": "A", "creator_handle": "h", "rank": 1, "url": "u"}]}))
    ck = config.DATA_DIR / f"expand_checkpoint_{TODAY}.json"
    ck.write_text('{"version":1,"reels":[{"id":"BANKED"')
    monkeypatch.setattr(extractor, "extract_external_reels_from_feed",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("net")))
    assert main.run_expand(target_count=5, deploy=False) == 2
    assert not ck.exists()
    assert len(list(config.DATA_DIR.glob("*.corrupt-*"))) == 1


# P0: cross-process pipeline exclusion ----------------------------------------
def test_pipeline_file_lock_excludes_second_process(tmp_path):
    lock_dir = tmp_path / "data"
    lock_dir.mkdir()
    import main as main_module
    import config as config_module

    old = config_module.DATA_DIR
    config_module.DATA_DIR = lock_dir
    try:
        with main_module._pipeline_file_lock():
            # Child tries the non-blocking lock on the same directory.
            child = (
                "import sys\n"
                "sys.path.insert(0, %r)\n"
                "from pathlib import Path\n"
                "import config, main\n"
                "config.DATA_DIR = Path(%r)\n"
                "try:\n"
                "    main._pipeline_file_lock().__enter__()\n"
                "except RuntimeError:\n"
                "    sys.exit(3)\n"
                "sys.exit(0)\n"
            )
            r = subprocess.run(
                [sys.executable, "-c", child % (str(config_module.ROOT_DIR), str(lock_dir))],
                capture_output=True, text=True, timeout=60,
            )
            assert r.returncode == 3, r.stderr
    finally:
        config_module.DATA_DIR = old


# P1: ops-page sink escapes Instagram-sourced strings --------------------------
def test_channels_template_escapes_sink():
    tpl = (config.ROOT_DIR / "templates" / "channels.html").read_text()
    assert "function esc(s)" in tpl
    sink = tpl[tpl.index("listEl.innerHTML = filtered.map"):tpl.index("}).join('')")]
    assert "${c.name" not in sink
    assert "onclick=\"singleAction('${c.handle}'" not in sink
    assert "toggleRowSelect('${c.handle}'" not in sink


# P1/P2: server routes ----------------------------------------------------------
def _serve(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "TEMPLATES_DIR", config.ROOT_DIR / "templates")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(config, "BLACKLIST_FILE", tmp_path / "data" / "blacklist.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), local_server.LocalDigestHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def test_retrigger_get_does_not_start_sync(tmp_path, monkeypatch):
    httpd = _serve(tmp_path, monkeypatch)
    try:
        calls = []
        monkeypatch.setattr(local_server, "trigger_adhoc_sync_task",
                            lambda deploy=False: calls.append(deploy) or {"status": "started"})
        c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        c.request("GET", "/retrigger", headers={"Origin": "http://evil.example",
                                                "Referer": "http://evil.example/x"})
        r = c.getresponse()
        body = r.read()
        assert r.status == 200 and calls == []
        assert b"/api/sync-adhoc" in body
    finally:
        httpd.shutdown()


def test_videos_route_blocks_traversal_and_sends_status_line(tmp_path, monkeypatch):
    httpd = _serve(tmp_path, monkeypatch)
    try:
        (tmp_path / "secret.env").write_text("R2_SECRET_ACCESS_KEY=hunter2\n")
        week_dir = config.VIDEOS_DIR / TODAY
        week_dir.mkdir(parents=True)
        (week_dir / "01_h_A.mp4").write_bytes(b"v" * 64)
        s = socket.create_connection(("127.0.0.1", httpd.server_address[1]), timeout=5)
        s.sendall(b"GET /videos/../secret.env HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        import time
        time.sleep(0.3)
        data = s.recv(4096)
        s.close()
        assert b"hunter2" not in data
        assert data.startswith(b"HTTP/")
        c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        c.request("GET", f"/videos/{TODAY}/01_h_A.mp4")
        r = c.getresponse()
        assert r.status == 200
        assert r.read() == b"v" * 64
    finally:
        httpd.shutdown()


# P1: soft-block detector ignores captions and bundle enums --------------------
def test_soft_block_detector_ignores_caption_and_bundle_enum():
    caption_html = ('<html><meta property="og:title" content="great pasta"/>'
                    '<time datetime="2026-09-10T10:00:00Z"></time>'
                    '<p>If the sauce splits, try again later with less heat.</p></html>')
    assert not extractor._page_html_indicates_block(caption_html)
    assert not extractor._page_html_indicates_block(
        "<script>E.LOGIN_REQUIRED='login_required'</script>")
    assert extractor._page_html_indicates_block("<div>challenge_required</div>")
    assert extractor._page_html_indicates_block("TRY AGAIN LATER")


def test_reel_with_caption_phrase_is_not_dropped():
    class El:
        def __init__(self, v): self.v = v
        def get_attribute(self, n): return self.v
        def inner_text(self): return self.v

    class Page:
        url = "https://www.instagram.com/reel/AAA/"
        class keyboard:
            @staticmethod
            def press(k): pass
        def goto(self, *a, **k): pass
        def content(self):
            return ('<html><meta property="og:title" content="great pasta"/>'
                    '<time datetime="2026-09-10T10:00:00Z"></time>'
                    '<p>If the sauce splits, try again later with less heat.</p></html>')
        def query_selector(self, sel):
            return {"meta[property=\"og:title\"]": El("great pasta"),
                    "time[datetime]": El("2026-09-10T10:00:00Z")}.get(sel)
        def query_selector_all(self, sel): return []

    class Sess:
        def get_page(self): return Page()

    meta = extractor.extract_single_reel_metadata(
        {"id": "AAA", "url": "https://www.instagram.com/reel/AAA/", "creator_handle": "chef"},
        session=Sess())
    assert meta is not None and meta["id"] == "AAA"


# P2: stealth coherence ----------------------------------------------------------
def test_stealth_script_matches_locale_and_pins_session():
    gb = extractor._stealth_script_for_locale("en-GB")
    assert "'en-GB', 'en'" in gb
    assert "get: () => undefined" not in gb
    assert "get: () => false" in gb
    assert "namedItem" in gb

    seen = []

    class FakePage:
        def close(self): pass

    class FakeContext:
        def __init__(self, **kw): self.kw = kw
        def add_init_script(self, script): seen.append((self.kw["locale"], script))
        def add_cookies(self, cookies): pass
        def new_page(self): return FakePage()
        def close(self): pass

    class FakeBrowser:
        def new_context(self, **kw): return FakeContext(**kw)

    sess = extractor.InstagramSession()
    sess._browser = FakeBrowser()
    sess._inject_cookies = lambda: None
    sess._open_context()
    first_locale = sess._locale
    sess._open_context()
    locales = [loc for loc, _ in seen]
    assert locales[0] == locales[1] == first_locale
    for loc, script in seen:
        assert f"'{loc}'" in script


# P2: pruner reports only confirmed deletes --------------------------------------
def test_pruner_reports_only_confirmed_deletes(tmp_path, monkeypatch):
    from unittest import mock
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / "2026-09-01.json").write_text(json.dumps({"run_date": "2026-09-01",
        "items": [{"id": "LIVE", "creator_handle": "h"}]}))
    monkeypatch.setattr(config, "DIGESTS_DIR", digests)
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "none.json")
    s3 = mock.MagicMock()
    pag = mock.MagicMock()
    pag.paginate.return_value = [{"Contents": [{"Key": "videos/2026-09-01/09_x_ORPHAN.mp4"}]}]
    s3.get_paginator.return_value = pag
    s3.delete_objects.return_value = {"Deleted": [], "Errors": [
        {"Key": "videos/2026-09-01/09_x_ORPHAN.mp4", "Message": "AccessDenied"}]}
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: s3)
    assert storage_r2.purge_unreferenced_r2_videos() == []


# P2: sources written durably ----------------------------------------------------
def test_save_sources_round_trips_atomically(tmp_path, monkeypatch):
    import atomic_io
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    payload = [{"handle": "chef", "name": "Chef", "category": "food", "enabled": True}]
    extractor.save_sources(payload)
    assert json.loads((tmp_path / "sources.json").read_text()) == payload
    import inspect
    assert "durable_write_json" in inspect.getsource(extractor.save_sources)


def test_discovery_selectors_are_disjoint():
    first, second = extractor._DISCOVERY_SELECTORS
    assert first != second
    assert second not in first and first not in second


# P0-2: resumed sync retires the older-day checkpoint ---------------------------
def test_resumed_sync_retires_older_day_checkpoint(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: (tmp_path, tmp_path))
    monkeypatch.setattr(main, "_alert_sync_abort", lambda *a, **k: None)
    monkeypatch.setattr(main, "MIN_DEPLOY_ITEMS", 1)
    stale = config.DATA_DIR / f"sync_progress_{YESTERDAY}.json"
    stale.write_text(json.dumps({
        "version": 1, "week_id": YESTERDAY, "days_back": 7, "limit_per_creator": 15,
        "since_timestamp": None, "stage": "publishing",
        "ranked": [{"id": "OLD", "creator_handle": "h", "rank": 1, "rank_display": "#01",
                    "url": "https://www.instagram.com/reel/OLD/"}]}))
    assert main.run_full_sync(dry_run=False, deploy=False) == 0
    assert not stale.exists()
    assert list(config.DATA_DIR.glob("sync_progress_*.json")) == []


def test_stale_banked_ranking_is_never_republished(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    old_week = (datetime.now(timezone.utc) - timedelta(days=9)).strftime("%Y-%m-%d")
    stale = config.DATA_DIR / f"sync_progress_{old_week}.json"
    stale.write_text(json.dumps({"version": 1, "week_id": old_week, "days_back": 7,
        "limit_per_creator": 15, "since_timestamp": None, "stage": "ranked",
        "ranked": [{"id": "OLD", "creator_handle": "h", "rank": 1, "url": "u"}]}))
    opened = []
    class Sess:
        def __enter__(self): opened.append(1); raise RuntimeError("stop before scraping")
        def __exit__(self, *a): return False
    monkeypatch.setattr(extractor, "InstagramSession", Sess)
    import pytest
    with pytest.raises(RuntimeError):
        main.run_full_sync(dry_run=False, deploy=False)
    assert opened == [1]                      # extraction was attempted, not skipped
    assert not stale.exists()                 # retired, not re-resumable
    assert list(config.DATA_DIR.glob("sync_progress_*.json.retired-*"))


# P1-4: hostile handle cannot escape the week dir -------------------------------
def test_hostile_handle_cannot_escape_week_dir(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    paths, keys = [], []
    def _dl(url, out_path, video_cdn_url=None):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x"); paths.append(Path(out_path).resolve()); return True
    monkeypatch.setattr(extractor, "download_reel_video", _dl)
    monkeypatch.setattr(storage_r2, "upload_reel_to_r2",
        lambda local_file, week_id, key_name=None, existing_keys=None:
            keys.append(key_name) or f"https://r2.example/videos/{week_id}/{key_name}")
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: (tmp_path, tmp_path))
    monkeypatch.setattr(main, "_alert_sync_abort", lambda *a, **k: None)
    monkeypatch.setattr(main, "MIN_DEPLOY_ITEMS", 1)
    (config.DATA_DIR / f"sync_progress_{TODAY}.json").write_text(json.dumps({
        "version": 1, "week_id": TODAY, "days_back": 7, "limit_per_creator": 15,
        "since_timestamp": None, "stage": "ranked",
        "ranked": [{"id": "EVIL1", "creator_handle": "../../../../escaped", "rank": 1,
                    "rank_display": "#01", "url": "u", "is_external": True}]}))
    assert main.run_full_sync(dry_run=False, deploy=False) == 0
    week_dir = (config.VIDEOS_DIR / TODAY).resolve()
    assert all(week_dir in p.parents for p in paths)
    assert keys == ["01_escaped_EVIL1.mp4"]
