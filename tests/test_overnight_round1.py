"""
test_overnight_round1.py — Round-1 experiment features (F1..F6).

F1 quiet-channel bulk unselect UI · F2 storage gauge · F3 digest-email
recommended section · F4 media-URL sample check · F5 category progress API ·
F6 sources hygiene audit. Live-server tests use the venv (playwrightdep via
local_server -> extractor); pure tests run anywhere.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import config


class _LiveServer:
    def __init__(self, tmp_path, monkeypatch):
        import local_server
        site = tmp_path / "site"
        site.mkdir(exist_ok=True)
        (site / "local_index.html").write_text("<html></html>", encoding="utf-8")
        monkeypatch.setattr(config, "SITE_DIR", site)
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            monkeypatch.delenv(var, raising=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), local_server.LocalDigestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def api_get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=10) as res:
            return res.status, json.loads(res.read().decode("utf-8"))

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()


# --- F1: quiet bulk-unselect UI markers ---

def test_f1_quiet_bulk_ui_markers():
    html = (config.TEMPLATES_DIR / "channels.html").read_text(encoding="utf-8")
    for marker in ("quietBulkBtn", "unselectQuietChannels", "_quietHandles",
                   "Unselect quiet", "/api/channels/bulk-unselect"):
        assert marker in html, marker


# --- F2: storage gauge endpoint ---

def test_f2_storage_endpoint_reports_local_and_r2(tmp_path, monkeypatch):
    import local_server
    import storage_r2
    vids = tmp_path / "videos"
    vids.mkdir()
    (vids / "a.mp4").write_bytes(b"x" * 1500)
    (vids / "b.mp4").write_bytes(b"y" * 500)
    monkeypatch.setattr(config, "VIDEOS_DIR", vids)
    monkeypatch.setattr(storage_r2, "get_bucket_storage_usage", lambda: (4_000_000_000, 120))
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: object())
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/storage")
    finally:
        srv.close()
    assert status == 200 and data["success"] is True
    assert data["local"] == {"bytes": 2000, "files": 2}
    assert data["r2"] == {"bytes": 4_000_000_000, "objects": 120}


def test_f2_storage_fail_open_without_creds(tmp_path, monkeypatch):
    import local_server
    import storage_r2
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "nope")
    monkeypatch.setattr(storage_r2, "get_bucket_storage_usage", lambda: (0, 0))
    monkeypatch.setattr(storage_r2, "get_s3_client", lambda: None)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/storage")
    finally:
        srv.close()
    assert status == 200 and data["success"] is True
    assert data["local"] == {"bytes": 0, "files": 0}
    assert data["r2"] == {"bytes": None, "objects": None}


def test_f2_dashboard_widget_markers():
    html = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    for marker in ("storagePill", "storageMeta", "/api/storage", "loadStorageGauge"):
        assert marker in html, marker


# --- F3: digest-email recommended section ---

def test_f3_email_recommended_section():
    import notifier
    recs = [{"handle": "@Scout_Pick", "name": "Scout <Pick>", "reason": "Great <b>tech</b>"}]
    msg = notifier.build_email_message("2026-09-24", count=10, recommended=recs)
    payloads = {p.get_content_type(): p.get_payload(decode=True).decode("utf-8")
                for p in msg.get_payload()}
    assert "New Creators To Try" in payloads["text/plain"]
    assert "@scout_pick" in payloads["text/plain"].lower()
    assert "New Creators To Try" in payloads["text/html"]
    assert "<b>tech</b>" not in payloads["text/html"]  # escaped
    assert "&lt;b&gt;tech&lt;/b&gt;" in payloads["text/html"]


def test_f3_email_unchanged_without_recommended():
    import notifier
    msg = notifier.build_email_message("2026-09-24", count=10)
    payloads = {p.get_content_type(): p.get_payload(decode=True).decode("utf-8")
                for p in msg.get_payload()}
    assert "New Creators To Try" not in payloads["text/plain"]
    assert "New Creators To Try" not in payloads["text/html"]


# --- F4: media-URL sample check ---

def test_f4_digest_url_extraction_and_sampling(tmp_path):
    import sys
    sys.path.insert(0, str(config.ROOT_DIR / "scripts"))
    import check_media_urls
    digest = tmp_path / "d.json"
    digest.write_text(json.dumps({"items": [
        {"id": "1", "video_url": "https://cdn.example/1.mp4"},
        {"id": "2", "r2_url": "https://cdn.example/2.mp4"},
        {"id": "3"},  # no URL
        {"id": "4", "video_url": "not-a-url"},
    ]}), encoding="utf-8")
    urls = check_media_urls.load_video_urls(digest)
    assert urls == ["https://cdn.example/1.mp4", "https://cdn.example/2.mp4"]
    sample = check_media_urls.deterministic_sample(
        [f"https://cdn.example/{i}.mp4" for i in range(100)], 10)
    assert len(sample) == 10
    assert sample[0].endswith("/0.mp4") and sample[-1].endswith("/90.mp4")


def test_f4_main_exit_codes(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(config.ROOT_DIR / "scripts"))
    import check_media_urls
    digest = tmp_path / "d.json"
    digest.write_text(json.dumps({"items": [
        {"id": str(i), "video_url": f"https://cdn.example/{i}.mp4"} for i in range(4)]}),
        encoding="utf-8")
    monkeypatch.setattr(check_media_urls, "head_ok",
                        lambda url, timeout: url.endswith(("/0.mp4", "/1.mp4", "/2.mp4")))
    assert check_media_urls.main([str(digest), "--sample", "4", "--max-fail-ratio", "0.5"]) == 0
    assert check_media_urls.main([str(digest), "--sample", "4", "--max-fail-ratio", "0.0"]) == 2
    assert check_media_urls.main([str(tmp_path / "missing.json")]) == 1


# --- F5: category progress API ---

def _write_digest(digest_dir, week, items):
    digest_dir.mkdir(parents=True, exist_ok=True)
    (digest_dir / f"{week}.json").write_text(
        json.dumps({"run_date": week, "count": len(items), "items": items}),
        encoding="utf-8")


def test_f5_category_progress_with_watched_overlay(tmp_path, monkeypatch):
    dd = tmp_path / "digests"
    _write_digest(dd, "2026-09-20", [
        {"id": "r1", "creator_handle": "a", "category": "ai_tech"},
        {"id": "r2", "creator_handle": "b", "category": "ai_tech"},
        {"id": "r3", "creator_handle": "c", "category": "food"},
        {"id": "r4", "creator_handle": "d"},  # uncategorized
    ])
    (tmp_path / "watched.json").write_text(
        json.dumps({"2026-09-20": ["r1", "r3", "ghost"]}), encoding="utf-8")
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config, "DIGESTS_DIR", dd)
    monkeypatch.setattr(config, "WATCHED_FILE", tmp_path / "watched.json")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/category-progress?week_id=2026-09-20")
        assert status == 200 and data["success"] is True
        by_cat = {c["category"]: c for c in data["categories"]}
        assert by_cat["ai_tech"] == {"category": "ai_tech", "total": 2, "watched": 1}
        assert by_cat["food"] == {"category": "food", "total": 1, "watched": 1}
        assert by_cat["other"] == {"category": "other", "total": 1, "watched": 0}
        assert data["total"] == 4 and data["total_watched"] == 2
        status, latest = srv.api_get("/api/category-progress")
        assert latest["week_id"] == "2026-09-20"
        status, missing = srv.api_get("/api/category-progress?week_id=1999-01-01")
        assert missing["categories"] == [] and missing["total"] == 0
    finally:
        srv.close()


def test_f5_dashboard_widget_markers():
    html = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    for marker in ("watchProgPill", "watchProgMeta", "/api/category-progress",
                   "loadWatchProgress"):
        assert marker in html, marker


# --- F6: sources hygiene ---

def test_f6_hygiene_detects_and_fixes(tmp_path, monkeypatch):
    import audit_channels
    src = tmp_path / "sources.json"
    monkeypatch.setattr(config, "SOURCES_FILE", src)
    src.write_text(json.dumps([
        {"handle": "GoodOne", "category": "niche"},
        {"handle": "@goodone", "category": "niche"},
        {"handle": "  spaced  ", "category": "food"},
        {"handle": "!!!", "category": "food"},
        {"handle": "fine_one", "category": "food"},
    ]), encoding="utf-8")
    report = audit_channels.hygiene_report()
    assert {e["handle"] for e in report["non_normalized"]} >= {"GoodOne", "@goodone", "  spaced  "}
    assert any(e["normalized"] == "goodone" for e in report["duplicates"])
    assert any(e["handle"] == "!!!" for e in report["invalid"])
    changed = audit_channels.fix_hygiene(report)
    assert changed >= 3
    saved = json.loads(src.read_text(encoding="utf-8"))
    handles = [s["handle"] for s in saved]
    assert "goodone" in handles and "spaced" in handles
    assert handles.count("goodone") == 1  # dupe dropped, first kept
    assert "!!!" in handles  # invalid never auto-deleted
