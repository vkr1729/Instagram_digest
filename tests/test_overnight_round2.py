"""
test_overnight_round2.py — Round-2 experiment features (F7..F11).

F7 exposure transparency · F8 digest shortfall suggestion · F9 lock-holder
sidecar · F10 local media spot-check · F11 health-report rec stats.
Live-server tests need the venv (local_server -> extractor -> playwright).
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


def _rec(handle, category="ai_tech"):
    return {"handle": handle, "name": handle, "category": category, "reason": "r"}


# --- F7: exposure transparency ---

def test_f7_times_suggested_and_dnr_count(tmp_path, monkeypatch):
    import recommendations
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", tmp_path / "recommended.json")
    rec_file = tmp_path / "recommended.json"
    rec_file.write_text(json.dumps({"version": 1, "creators": [_rec("seen_twice"), _rec("fresh")]}),
                        encoding="utf-8")
    recommendations.record_exposures(["seen_twice", "seen_twice"])
    recommendations.add_do_not_recommend("muted_one")
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/recommended-creators")
    finally:
        srv.close()
    assert status == 200
    by_h = {c["handle"]: c for c in data["creators"]}
    assert by_h["seen_twice"]["times_suggested"] == 2
    assert by_h["fresh"]["times_suggested"] == 1
    assert data["do_not_recommend_count"] == 1


def test_f7_dashboard_markers():
    html = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    for marker in ("times_suggested", "suggested", "do_not_recommend_count", "retire after 5"):
        assert marker in html, marker


# --- F8: digest shortfall ---

def test_f8_digest_status_shortfall_and_complete(tmp_path, monkeypatch):
    dd = tmp_path / "digests"
    dd.mkdir()
    (dd / "2026-09-20.json").write_text(
        json.dumps({"run_date": "2026-09-20",
                    "items": [{"id": str(i)} for i in range(212)]}), encoding="utf-8")
    monkeypatch.setattr(config, "DIGESTS_DIR", dd)
    monkeypatch.setattr(config, "TOP_DIGEST_COUNT", 250)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/digest-status")
    finally:
        srv.close()
    assert status == 200 and data["success"] is True
    assert data == {"success": True, "week_id": "2026-09-20", "count": 212,
                    "target": 250, "shortfall": 38}


def test_f8_digest_status_empty(tmp_path, monkeypatch):
    dd = tmp_path / "digests"
    dd.mkdir()
    monkeypatch.setattr(config, "DIGESTS_DIR", dd)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/digest-status")
    finally:
        srv.close()
    assert status == 200 and data["count"] == 0 and data["shortfall"] == data["target"]


def test_f8_dashboard_markers():
    html = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    for marker in ("shortfallMeta", "loadDigestStatus", "/api/digest-status",
                   "shortfallExpand", "expandCount"):
        assert marker in html, marker


# --- F9: lock-holder sidecar ---

def test_f9_sidecar_lifecycle_and_busy_detail(tmp_path, monkeypatch):
    import main as pipeline
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert pipeline.lock_holder_info() is None
    with pipeline._pipeline_file_lock():
        holder = pipeline.lock_holder_info()
        assert holder and holder["pid"] == __import__("os").getpid()
        assert (tmp_path / ".pipeline.lock.info").exists()
    assert pipeline.lock_holder_info() is None
    assert not (tmp_path / ".pipeline.lock.info").exists()


def test_f9_busy_error_names_holder(tmp_path, monkeypatch):
    import main as pipeline
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    held = threading.Event()
    release = threading.Event()

    def _holder():
        with pipeline._pipeline_file_lock():
            held.set()
            assert release.wait(timeout=15)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    try:
        assert held.wait(timeout=10)
        try:
            with pipeline._pipeline_file_lock():
                raise AssertionError("second acquisition must fail")
        except pipeline.PipelineBusy as exc:
            assert "held by pid" in str(exc)
    finally:
        release.set()
        t.join(timeout=10)


def test_f9_stale_sidecar_swept(tmp_path, monkeypatch):
    import main as pipeline
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / ".pipeline.lock.info").write_text(
        json.dumps({"pid": 987654321, "started_at": "x", "cmd": "dead"}), encoding="utf-8")
    assert pipeline.lock_holder_info() is None
    assert not (tmp_path / ".pipeline.lock.info").exists()


# --- F10: local media spot-check ---

def test_f10_collect_orders_newest_week_first(tmp_path):
    import sys
    sys.path.insert(0, str(config.ROOT_DIR / "scripts"))
    import check_local_media
    root = tmp_path / "videos"
    (root / "2026-09-13").mkdir(parents=True)
    (root / "2026-09-20").mkdir(parents=True)
    (root / "2026-09-13" / "a.mp4").write_bytes(b"0")
    (root / "2026-09-20" / "b.mp4").write_bytes(b"0")
    files = check_local_media.collect_media_files(root)
    assert [p.name for p in files] == ["b.mp4", "a.mp4"]
    assert [p.name for p in check_local_media.deterministic_sample(files, 1)] == ["b.mp4"]


def test_f10_main_exit_codes(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(config.ROOT_DIR / "scripts"))
    import check_local_media
    import extractor
    root = tmp_path / "videos"
    root.mkdir()
    for i in range(4):
        (root / f"{i}.mp4").write_bytes(b"0")
    monkeypatch.setattr(extractor, "_downloaded_mp4_is_playable",
                        lambda p: p.name in ("0.mp4", "1.mp4", "2.mp4"))
    assert check_local_media.main(["--dir", str(root), "--sample", "4",
                                   "--max-fail-ratio", "0.5"]) == 0
    assert check_local_media.main(["--dir", str(root), "--sample", "4",
                                   "--max-fail-ratio", "0.0"]) == 2
    assert check_local_media.main(["--dir", str(tmp_path / "empty")]) == 1


# --- F11: health-report rec stats ---

def test_f11_health_report_rec_section(tmp_path, monkeypatch):
    import notifier
    import recommendations
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    rec_file = tmp_path / "recommended.json"
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", rec_file)
    rec_file.write_text(json.dumps({
        "version": 1, "recommendations_stale": False,
        "creators": [_rec("a"), _rec("b")]}), encoding="utf-8")
    recommendations.add_do_not_recommend("muted_x")
    recommendations.record_exposures(["old_y"] * 5)
    monkeypatch.setattr(config, "PAGES_BASE_URL", "http://127.0.0.1:9")
    report = notifier.collect_health_report(week_id="2026-09-20")
    assert report["recs_status"] == "2 served · 1 muted · 1 retired"
    assert report["recs_ok"] is True
    msg = notifier.build_health_report_message(report)
    payloads = {p.get_content_type(): p.get_payload(decode=True).decode("utf-8")
                for p in msg.get_payload()}
    assert "2 served" in payloads["text/html"] and "Recommendations" in payloads["text/html"]
    assert "- recs: 2 served" in payloads["text/plain"]
