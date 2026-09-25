"""
test_review_fixes.py — Regression tests for the pre-merge adversarial review.

Covers: lock sidecar ownership, finalize_recommendations on every load path,
email prod wiring, endpoint payload guards, lock-status canonical behavior,
week_id containment, script hardening, hygiene preservation, quiet bulk action,
health recs verdicts, and doc/template markers.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

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

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def api_post(self, path, payload=None, raw=None):
        data = raw if raw is not None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url(path), data=data, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def api_get(self, path):
        with urllib.request.urlopen(self._url(path), timeout=10) as res:
            return res.status, json.loads(res.read().decode("utf-8"))

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()


def _rec(handle, category="ai_tech"):
    return {"handle": handle, "name": handle, "category": category, "reason": "r"}


# --- P0-1: failed contender must not delete the holder sidecar ---

def test_lock_contender_preserves_holder_sidecar(tmp_path, monkeypatch):
    import main as pipeline
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    held, release = threading.Event(), threading.Event()

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
        holder = pipeline.lock_holder_info()
        assert holder and (tmp_path / ".pipeline.lock.info").exists()
    finally:
        release.set()
        t.join(timeout=10)
    assert pipeline.lock_holder_info() is None


# --- finalize_recommendations on every load path ---

def _write_sources(tmp_path, monkeypatch, handles):
    src = tmp_path / "sources.json"
    src.write_text(json.dumps([{"handle": h} for h in handles]), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", src)


def test_finalize_drops_dnr_retired_keeps_channels(tmp_path, monkeypatch):
    import recommendations
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    recommendations.add_do_not_recommend("muted_one")
    recommendations.record_exposures(["retired_one"] * 5)
    recs = [_rec("muted_one"), _rec("retired_one"), _rec("fresh"),
            _rec("added_but_retired"), "not-a-dict"]
    out = recommendations.finalize_recommendations(recs, {"added_but_retired"})
    assert {r["handle"] for r in out} == {"fresh", "added_but_retired"}
    # Idempotent: second pass changes nothing.
    assert recommendations.finalize_recommendations(out, {"added_but_retired"}) == out
    # Tolerant of corrupt exposure values.
    fb = recommendations.load_feedback()
    fb["exposures"] = {"weird": "NaN"}
    recommendations.save_feedback(fb)
    out = recommendations.finalize_recommendations([_rec("weird")], set())
    assert [r["handle"] for r in out] == ["weird"]


def test_refresh_cache_hit_applies_finalize(tmp_path, monkeypatch):
    import recommendations
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    rec_file = tmp_path / "recommended.json"
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", rec_file)
    _write_sources(tmp_path, monkeypatch, ["chan_one"])
    from datetime import datetime, timezone
    rec_file.write_text(json.dumps({
        "version": 1, "updated_at": datetime.now(timezone.utc).isoformat(),
        "creators": [_rec("chan_one"), _rec("fresh_face"), _rec("grumpy")] * 4,
    }), encoding="utf-8")
    recommendations.add_do_not_recommend("grumpy")
    res = recommendations.refresh_recommendations(force=False)
    assert {r["handle"] for r in res} == {"chan_one", "fresh_face"}


def test_refresh_trims_to_ten_after_exclusions(tmp_path, monkeypatch):
    import recommendations
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", tmp_path / "recommended.json")
    monkeypatch.setattr(recommendations, "QUARANTINE_FILE", tmp_path / "quarantine.json")
    _write_sources(tmp_path, monkeypatch, [])

    def _mock(cat, samples, existing, timeout_secs=600, steering=""):
        # 12 valid, no sanitize cap in the mock: refresh must trim to 10.
        return [{"handle": f"overshoot_{cat}_{i}", "name": "O",
                 "category": cat, "reason": "r"} for i in range(12)]

    with patch("recommendations.check_agy_auth", return_value=True), \
         patch("recommendations.discover_category_creators", side_effect=_mock):
        res = recommendations.refresh_recommendations(force=True, timeout_per_category=1)
    by_cat: dict[str, list] = {}
    for r in res:
        by_cat.setdefault(r["category"], []).append(r)
    assert len(by_cat) == 6
    assert all(len(v) == 10 for v in by_cat.values())
    exp = recommendations.load_feedback()["exposures"]
    assert len(exp) == 60 and all(c == 1 for c in exp.values())


# --- Email prod wiring (real send path lives in test_notifier.py: conftest
# stubs notifier.send_* helpers in every other module) ---

def test_dashboard_recommendation_markers_present():
    html = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert "/api/recommendations/do-not-recommend" in html


# --- Endpoint payload guards ---

def _isolate_server(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    sources.write_text("[]", encoding="utf-8")
    (tmp_path / "blacklist.json").write_text(json.dumps({"creators": []}), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources)
    monkeypatch.setattr(config, "BLACKLIST_FILE", tmp_path / "blacklist.json")
    import recommendations
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", tmp_path / "recommended.json")


def test_non_dict_json_body_answers_400_not_dropped(tmp_path, monkeypatch):
    import extractor
    _isolate_server(tmp_path, monkeypatch)
    monkeypatch.setattr(
        extractor, "follow_creator", lambda handle, **kw: {"ok": True, "state": "followed"})
    import local_server
    monkeypatch.setattr(local_server, "_FOLLOW_RESULTS", {})
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        for path in ("/api/recommendations/do-not-recommend", "/api/channels/add"):
            status, _ = srv.api_post(path, raw=b"[1,2]")
            assert status == 400, path
            status, _ = srv.api_post(path, raw=b'"justastring"')
            assert status == 400, path
    finally:
        srv.close()


# --- lock-status canonical behavior over HTTP ---

def test_lock_status_stale_sidecar_reads_free(tmp_path, monkeypatch):
    import main as pipeline
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / ".pipeline.lock.info").write_text(
        json.dumps({"pid": 987654321, "started_at": "x", "cmd": "dead"}), encoding="utf-8")
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/lock-status")
    finally:
        srv.close()
    assert status == 200
    assert data == {"success": True, "locked": False, "holder": None}
    assert pipeline.lock_holder_info() is None  # stale metadata swept


# --- week_id containment ---

def test_category_progress_rejects_traversal(tmp_path, monkeypatch):
    dd = tmp_path / "digests"
    dd.mkdir()
    (dd / "2026-09-20.json").write_text(
        json.dumps({"run_date": "2026-09-20",
                    "items": [{"id": "r1", "category": "ai_tech"}]}), encoding="utf-8")
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")
    (tmp_path / "watched.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "DIGESTS_DIR", dd)
    monkeypatch.setattr(config, "WATCHED_FILE", tmp_path / "watched.json")
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/category-progress?week_id=../sources")
        assert status == 200 and data["week_id"] == "2026-09-20"  # fell back to latest
        status, data = srv.api_get("/api/category-progress?week_id=2026-09-20")
        assert data["total"] == 1
    finally:
        srv.close()


# --- prune list shape ---

def test_prune_list_shaped_cache_persists(tmp_path, monkeypatch):
    import recommendations
    rec_file = tmp_path / "recommended.json"
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", rec_file)
    rec_file.write_text(json.dumps([{"handle": "drop_me"}, {"handle": "keep_me"}]),
                        encoding="utf-8")
    assert recommendations.prune_recommended_cache({"drop_me"}) == 1
    data = json.loads(rec_file.read_text(encoding="utf-8"))
    assert [c["handle"] for c in data] == ["keep_me"]


# --- hygiene preservation + CLI guard ---

def test_fix_hygiene_preserves_empties_and_invalids(tmp_path, monkeypatch):
    import audit_channels
    src = tmp_path / "sources.json"
    monkeypatch.setattr(config, "SOURCES_FILE", src)
    src.write_text(json.dumps([
        {"handle": "  ", "category": "food"},
        {"handle": "!!!", "category": "food"},
        {"handle": "Dup", "category": "niche"},
        {"handle": "dup", "category": "niche"},
    ]), encoding="utf-8")
    changed = audit_channels.fix_hygiene()
    saved = [s["handle"] for s in json.loads(src.read_text(encoding="utf-8"))]
    assert "  " in saved and "!!!" in saved  # preserved, never deleted
    assert saved.count("dup") == 1 and changed == 2


def test_hygiene_fix_flag_requires_hygiene(monkeypatch):
    import audit_channels
    import sys
    monkeypatch.setattr(sys, "argv", ["audit_channels.py", "--fix"])
    import pytest
    with pytest.raises(SystemExit):
        audit_channels.main()


# --- quiet bulk-unselect endpoint effect ---

def test_quiet_bulk_unselect_blacklists_and_removes(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps([
        {"handle": "quiet_one", "category": "niche", "enabled": True},
        {"handle": "loud_one", "category": "niche", "enabled": True},
    ]), encoding="utf-8")
    (tmp_path / "blacklist.json").write_text(json.dumps({"creators": []}), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources)
    monkeypatch.setattr(config, "BLACKLIST_FILE", tmp_path / "blacklist.json")
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_post(
            "/api/channels/bulk-unselect",
            {"creator_handles": ["quiet_one"], "action": "add"})
    finally:
        srv.close()
    assert status == 200 and data["success"] is True
    remaining = [s["handle"] for s in json.loads(sources.read_text(encoding="utf-8"))]
    assert "quiet_one" not in remaining and "loud_one" in remaining
    bl = json.loads((tmp_path / "blacklist.json").read_text(encoding="utf-8"))
    assert "quiet_one" in bl["creators"]


# --- health recs verdicts ---

def test_health_recs_stale_is_unhealthy_list_shape_counts(tmp_path, monkeypatch):
    import notifier
    import recommendations
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    rec_file = tmp_path / "recommended.json"
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", rec_file)
    rec_file.write_text(json.dumps({"version": 1, "recommendations_stale": True,
                                    "creators": [{"handle": "a"}]}), encoding="utf-8")
    monkeypatch.setattr(config, "PAGES_BASE_URL", "http://127.0.0.1:9")
    report = notifier.collect_health_report(week_id="2026-09-20")
    assert report["recs_ok"] is False and "STALE" in report["recs_status"]
    assert report["healthy"] is False
    rec_file.write_text(json.dumps([{"handle": "a"}, {"handle": "b"}]), encoding="utf-8")
    report = notifier.collect_health_report(week_id="2026-09-20")
    assert report["recs_status"].startswith("2 served") and report["recs_ok"] is True


# --- check_media_urls hardening ---

def test_media_urls_prefer_r2_and_repo_root(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(config.ROOT_DIR / "scripts"))
    import check_media_urls
    digest = tmp_path / "d.json"
    digest.write_text(json.dumps({"items": [
        {"id": "1", "url": "https://instagram.com/reel/1",
         "video_url": "https://cdn.example/1.mp4"},
        {"id": "2", "url": "https://instagram.com/reel/2"},
    ]}), encoding="utf-8")
    urls = check_media_urls.load_video_urls(digest)
    assert urls == ["https://cdn.example/1.mp4"]  # permalink-only item skipped
    # Default alias resolves against the repo tree (never CWD or network).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_media_urls, "head_ok", lambda url, timeout: True)
    assert check_media_urls.main([]) == 0


# --- template markers for review fixes ---

def test_review_template_markers():
    dash = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    for marker in ("filter_degraded", "Filters degraded"):
        assert marker in dash, marker
    assert dash.count("setInterval(loadDigestStatus, 60000);") == 1
    ch = (config.TEMPLATES_DIR / "channels.html").read_text(encoding="utf-8")
    assert "quietBtn.disabled = false" in ch
