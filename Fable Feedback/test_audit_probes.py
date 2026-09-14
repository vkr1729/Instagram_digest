"""Audit probes (not part of the repo): each test is a concrete failure trace.
A PASSING probe here means the BUG IS REPRODUCED."""
import http.client
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import config
import extractor
import local_server
import main
import ranker
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


# ---------------------------------------------------------------- claim 4 --
def test_probe_A_weekly_sync_persists_digest_without_r2_url(tmp_path, monkeypatch):
    """Premise for probe B: the digest run_full_sync writes has no r2_url."""
    _iso(tmp_path, monkeypatch)
    ranked = [{"id": "AAA", "creator_handle": "h1", "rank": 1, "rank_display": "#01",
               "url": "https://www.instagram.com/reel/AAA/"}]
    # Resume at 'ranked' stage so no scraping session is needed.
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
    assert "r2_url" not in saved[0] and "video_url" not in saved[0]   # <-- on disk
    assert captured["r2_uploaded_urls"]["AAA"].startswith("https://r2.example/videos/" + TODAY)


def test_probe_B_expand_on_later_day_repoints_every_existing_reel(tmp_path, monkeypatch):
    """Sync ran yesterday; +100 runs today -> all 'existing' cards get today's prefix."""
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
        assert f"/videos/{TODAY}/" in urls[f"OLD{i}"]          # wrong week
        assert f"/videos/{YESTERDAY}/" not in urls[f"OLD{i}"]  # where the object really is
    # and the on-disk digest now carries today's run_date for yesterday's objects
    assert json.loads(config.DIGEST_BATCH_FILE.read_text())["run_date"] == TODAY


def test_probe_C_expand_reintegrates_checkpoint_id_already_in_digest(tmp_path, monkeypatch):
    """Weekly sync integrated reel X after a +100 was interrupted with X banked;
    resume appends X again -> duplicate id in the manifest."""
    _iso(tmp_path, monkeypatch)
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": TODAY, "items": [
        {"id": "X", "creator_handle": "h", "rank": 1, "rank_display": "#01",
         "url": "https://www.instagram.com/reel/X/"}]}))
    (config.DATA_DIR / f"expand_checkpoint_{TODAY}.json").write_text(json.dumps({
        "version": 1, "target_count": 1,
        "reels": [{"id": "X", "creator_handle": "h", "url": "https://www.instagram.com/reel/X/"}]}))
    monkeypatch.setattr(extractor, "extract_external_reels_from_feed", lambda **kw: [])
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: (tmp_path, tmp_path))
    assert main.run_expand(target_count=1, deploy=False) == 0
    ids = [i["id"] for i in json.loads(config.DIGEST_BATCH_FILE.read_text())["items"]]
    assert ids == ["X", "X"]


def test_probe_D_rank_gap_from_sync_drop_yields_duplicate_rank_on_expand(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    # run_full_sync drops unplayables without renumbering -> ranks 1,2,4
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": TODAY, "items": [
        {"id": f"O{r}", "creator_handle": "h", "rank": r, "rank_display": f"#{r:02d}",
         "url": f"https://www.instagram.com/reel/O{r}/"} for r in (1, 2, 4)]}))
    monkeypatch.setattr(extractor, "extract_external_reels_from_feed",
                        lambda **kw: [{"id": "N", "creator_handle": "n",
                                       "url": "https://www.instagram.com/reel/N/"}])
    monkeypatch.setattr(site_builder, "build_site", lambda **kw: (tmp_path, tmp_path))
    assert main.run_expand(target_count=1, deploy=False) == 0
    ranks = sorted(i["rank"] for i in json.loads(config.DIGEST_BATCH_FILE.read_text())["items"])
    assert ranks == [1, 2, 4, 4]


# ---------------------------------------------------------------- claim 2 --
def test_probe_E_corrupt_expand_checkpoint_is_deleted_not_quarantined(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    config.DIGEST_BATCH_FILE.write_text(json.dumps({"run_date": TODAY, "items": [
        {"id": "A", "creator_handle": "h", "rank": 1, "url": "u"}]}))
    ck = config.DATA_DIR / f"expand_checkpoint_{TODAY}.json"
    ck.write_text('{"version":1,"reels":[{"id":"BANKED"')   # torn
    monkeypatch.setattr(extractor, "extract_external_reels_from_feed",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("net")))
    assert main.run_expand(target_count=5, deploy=False) == 2
    assert not ck.exists()
    assert list(config.DATA_DIR.glob("*.corrupt-*")) == []


def test_probe_F_pipeline_lock_is_per_process():
    """cron run_weekly.sh + dashboard +100 are two processes: both acquire."""
    assert local_server._PIPELINE_LOCK.acquire(blocking=False)
    try:
        code = ("import local_server,sys;"
                "sys.exit(0 if local_server._PIPELINE_LOCK.acquire(blocking=False) else 1)")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(config.ROOT_DIR),
                           env=dict(os.environ), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    finally:
        local_server._PIPELINE_LOCK.release()


def test_probe_G_sources_json_write_is_not_durable():
    import inspect
    src = inspect.getsource(extractor.save_sources)
    assert "write_text" in src and "durable_write_json" not in src


# ---------------------------------------------------------------- server ---
@pytest.fixture
def server(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "TEMPLATES_DIR", config.ROOT_DIR / "templates")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(config, "BLACKLIST_FILE", tmp_path / "data" / "blacklist.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), local_server.LocalDigestHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def test_probe_H_get_retrigger_starts_sync_with_no_origin_check(server, monkeypatch):
    calls = []
    monkeypatch.setattr(local_server, "trigger_adhoc_sync_task",
                        lambda deploy=False: calls.append(deploy) or {"status": "started"})
    c = http.client.HTTPConnection("127.0.0.1", server, timeout=5)
    c.request("GET", "/retrigger", headers={"Origin": "http://evil.example",
                                             "Referer": "http://evil.example/x"})
    r = c.getresponse(); r.read()
    assert r.status == 200 and calls == [True]     # sync+deploy fired cross-origin
    c.request("POST", "/api/expand?count=1", headers={"Origin": "http://evil.example"})
    r = c.getresponse(); r.read()
    assert r.status == 403                          # ...while POST is guarded


def test_probe_I_videos_route_path_traversal_and_missing_end_headers(server, tmp_path):
    import socket, time
    config.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)  # config.py does this at import
    (tmp_path / "secret.env").write_text("R2_SECRET_ACCESS_KEY=hunter2\n")
    s = socket.create_connection(("127.0.0.1", server), timeout=5)
    s.sendall(b"GET /videos/../secret.env HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    time.sleep(0.3); data = s.recv(4096); s.close()
    assert b"hunter2" in data                 # traversal: file outside videos/ served
    assert not data.startswith(b"HTTP/")     # and no status line: end_headers() never called


def test_probe_J_api_channels_returns_instagram_full_name_raw(server, tmp_path):
    payload = "<img src=x onerror=fetch('/api/server/shutdown',{method:'POST'})>"
    (tmp_path / "sources.json").write_text(json.dumps([
        {"handle": "victimfollows", "name": payload, "category": "niche", "enabled": True}]))
    c = http.client.HTTPConnection("127.0.0.1", server, timeout=5)
    c.request("GET", "/api/channels"); r = c.getresponse()
    data = json.loads(r.read())
    assert data["channels"][0]["name"] == payload
    tpl = (config.ROOT_DIR / "templates" / "channels.html").read_text()
    sink = tpl[tpl.index("listEl.innerHTML = filtered.map"):tpl.index("}).join('')")]
    assert "${c.name" in sink and "esc(" not in sink and "textContent" not in sink


# ---------------------------------------------------------------- claim 7 --
def test_probe_K_soft_block_false_positive_drops_legit_reel():
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
    assert meta is None                     # dropped as a "soft block"
    # The detector also fires on a JS bundle enum, i.e. on every page if IG ships one.
    assert extractor._page_html_indicates_block("<script>E.LOGIN_REQUIRED='login_required'</script>")


def test_probe_L_fingerprint_self_contradiction():
    assert "en-GB" in extractor.LOCALE_POOL
    assert "['en-US', 'en']" in extractor.STEALTH_INIT_SCRIPT   # languages hard-coded
    assert "get: () => undefined" in extractor.STEALTH_INIT_SCRIPT  # real Chrome: false


# ---------------------------------------------------------------- claim 3 --
def test_probe_M_pruner_reports_failed_deletes_as_purged(tmp_path, monkeypatch):
    from unittest import mock
    digests = tmp_path / "digests"; digests.mkdir()
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
    assert storage_r2.purge_unreferenced_r2_videos() == ["videos/2026-09-01/09_x_ORPHAN.mp4"]
