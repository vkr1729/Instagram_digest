"""
test_recommendation_feedback.py — Recommendation feedback loop.

Covers the do-not-recommend store, exposure counting, 5-strike retirement
with slot backfill, AI steering context, serve-time filtering, and the
dashboard membership/DNR UI markers. No browser, network, or agy calls:
discovery and follow workers are stubbed/mocked.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import config
import recommendations


def _isolate_feedback(tmp_path, monkeypatch):
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", tmp_path / "recommended.json")
    monkeypatch.setattr(recommendations, "QUARANTINE_FILE", tmp_path / "quarantine.json")
    return tmp_path / "feedback.json"


def _rec(handle, category="ai_tech"):
    return {"handle": handle, "name": handle, "category": category, "reason": "r"}


# --- Feedback store ---

def test_feedback_defaults_when_missing(tmp_path, monkeypatch):
    _isolate_feedback(tmp_path, monkeypatch)
    fb = recommendations.load_feedback()
    assert fb["exposures"] == {}
    assert fb["do_not_recommend"] == []


def test_feedback_corrupt_file_quarantined_with_defaults(tmp_path, monkeypatch):
    fb_file = _isolate_feedback(tmp_path, monkeypatch)
    fb_file.write_text("{not json", encoding="utf-8")
    fb = recommendations.load_feedback()
    assert fb["exposures"] == {}
    assert fb["do_not_recommend"] == []
    assert list(tmp_path.glob("feedback.json.corrupt-*")), "corrupt file must be quarantined"


def test_feedback_coerces_bad_values(tmp_path, monkeypatch):
    fb_file = _isolate_feedback(tmp_path, monkeypatch)
    fb_file.write_text(json.dumps({
        "exposures": {"good": 3, "bad": "NaN", "neg": -2},
        "do_not_recommend": "not-a-list",
    }), encoding="utf-8")
    fb = recommendations.load_feedback()
    assert fb["exposures"] == {"good": 3, "neg": 0}
    assert fb["do_not_recommend"] == []


def test_dnr_add_clear_idempotent(tmp_path, monkeypatch):
    _isolate_feedback(tmp_path, monkeypatch)
    assert recommendations.add_do_not_recommend("@SomeCreator") is True
    assert recommendations.add_do_not_recommend("somecreator") is False  # dup
    assert recommendations.add_do_not_recommend("   ") is False
    fb = recommendations.load_feedback()
    assert fb["do_not_recommend"] == ["somecreator"]
    assert recommendations.clear_do_not_recommend("SomeCreator") is True
    assert recommendations.clear_do_not_recommend("SomeCreator") is False
    assert recommendations.load_feedback()["do_not_recommend"] == []


def test_record_exposures_accumulates(tmp_path, monkeypatch):
    _isolate_feedback(tmp_path, monkeypatch)
    recommendations.record_exposures(["a", "b"])
    recommendations.record_exposures(["a", "  "])
    exp = recommendations.load_feedback()["exposures"]
    assert exp == {"a": 2, "b": 1}


# --- Steering + sanitize ---

def test_steering_mentions_rejects_positives_and_history():
    ctx = recommendations.build_steering_context(
        do_not_recommend={"nope"},
        previously_suggested={"old1", "added1"},
        added_examples={"added1"},
    )
    assert "@nope" in ctx and "NEVER suggest" in ctx
    assert "@added1" in ctx and "prefer more" in ctx
    assert "@old1" in ctx and "do NOT repeat" in ctx


def test_steering_empty_when_no_history():
    assert recommendations.build_steering_context(set(), set(), set()) == ""


def test_sanitize_keeps_ten_by_default_allows_more_on_request():
    many = [
        {"handle": f"creator{i:02d}", "name": f"C{i}", "category": "ai_tech", "reason": "r"}
        for i in range(14)
    ]
    assert len(recommendations.sanitize_and_validate_recommendations(
        json.dumps(many), "ai_tech", set())) == 10
    assert len(recommendations.sanitize_and_validate_recommendations(
        json.dumps(many), "ai_tech", set(), limit=12)) == 12


# --- Refresh: exclusion + backfill + exposures ---

def _mock_discover_factory():
    def _mock(cat, samples, existing, timeout_secs=600, steering=""):
        # Returns excluded handles (must be dropped) plus fresh ones.
        assert isinstance(steering, str)
        base = [
            {"handle": "dnr_creator", "name": "D", "category": cat, "reason": "r"},
            {"handle": "retired_creator", "name": "R", "category": cat, "reason": "r"},
            {"handle": f"fresh_{cat}_1", "name": "F1", "category": cat, "reason": "r"},
            {"handle": f"fresh_{cat}_2", "name": "F2", "category": cat, "reason": "r"},
        ]
        return [r for r in base if r["handle"] not in existing]
    return _mock


def test_refresh_excludes_dnr_and_retired_and_records_exposures(tmp_path, monkeypatch):
    _isolate_feedback(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")
    recommendations.add_do_not_recommend("dnr_creator")
    recommendations.record_exposures(["retired_creator"] * 5)
    seen_prompts = []

    def _mock(cat, samples, existing, timeout_secs=600, steering=""):
        seen_prompts.append(steering)
        assert "dnr_creator" in existing and "retired_creator" in existing
        return [
            {"handle": "dnr_creator", "name": "D", "category": cat, "reason": "r"},
            {"handle": "retired_creator", "name": "R", "category": cat, "reason": "r"},
            {"handle": f"fresh_{cat}_1", "name": "F1", "category": cat, "reason": "r"},
            {"handle": f"fresh_{cat}_2", "name": "F2", "category": cat, "reason": "r"},
        ]

    with patch("recommendations.check_agy_auth", return_value=True), \
         patch("recommendations.discover_category_creators", side_effect=_mock):
        res = recommendations.refresh_recommendations(force=True, timeout_per_category=1)
    handles = {r["handle"] for r in res}
    assert "dnr_creator" not in handles and "retired_creator" not in handles
    assert any(h.startswith("fresh_") for h in handles)
    # Steering reached the scout, exposures recorded for served handles only.
    assert any("@dnr_creator" in p for p in seen_prompts)
    exp = recommendations.load_feedback()["exposures"]
    assert exp["fresh_ai_tech_1"] == 1
    assert exp["retired_creator"] == 5  # untouched: never served again
    assert len(res) == 12  # 6 categories x 2 fresh


def test_apply_exclusions_drops_seen_and_hard_excluded():
    recs = [_rec("fresh"), _rec("seen_one"), _rec("Stray_DNR"), {"nope": 1}, _rec("  ")]
    fresh = recommendations.apply_exclusions(recs, {"seen_one"}, {"stray_dnr"})
    assert [r["handle"] for r in fresh] == ["fresh"]


def test_refresh_backfills_slots_from_over_request(tmp_path, monkeypatch):
    """12 requested, 10 kept: exclusions consume headroom, set stays full."""
    _isolate_feedback(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    (tmp_path / "sources.json").write_text("[]", encoding="utf-8")

    def _mock(cat, samples, existing, timeout_secs=600, steering=""):
        # Production-faithful: the real scout returns sanitized, capped lists.
        raw = [{"handle": f"junk_{cat}_{i}", "name": "J", "category": cat, "reason": "r"}
               for i in range(4)]
        raw += [{"handle": f"good_{cat}_{i}", "name": "G", "category": cat, "reason": "r"}
                for i in range(12)]
        existing = set(existing) | {f"junk_{cat}_{i}" for i in range(4)}
        return recommendations.sanitize_and_validate_recommendations(
            json.dumps(raw), cat, existing)

    with patch("recommendations.check_agy_auth", return_value=True), \
         patch("recommendations.discover_category_creators", side_effect=_mock):
        res = recommendations.refresh_recommendations(force=True, timeout_per_category=1)
    by_cat: dict[str, list] = {}
    for r in res:
        by_cat.setdefault(r["category"], []).append(r)
    assert len(by_cat) == 6
    for cat, items in by_cat.items():
        assert len(items) == 10, (cat, len(items))
        assert all(i["handle"].startswith("good_") for i in items)


# --- prune helper ---

def test_prune_recommended_cache(tmp_path, monkeypatch):
    rec_file = tmp_path / "recommended.json"
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", rec_file)
    rec_file.write_text(json.dumps({
        "version": 1, "total_count": 3,
        "creators": [_rec("keep_me"), _rec("drop_me"), _rec("also_keep")],
    }), encoding="utf-8")
    assert recommendations.prune_recommended_cache({"DROP_me"}) == 1
    data = json.loads(rec_file.read_text(encoding="utf-8"))
    assert {c["handle"] for c in data["creators"]} == {"keep_me", "also_keep"}
    assert data["total_count"] == 2
    assert recommendations.prune_recommended_cache({"nobody"}) == 0


# --- Server endpoints (live handler, isolated files) ---

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

    def api_post(self, path, payload):
        req = urllib.request.Request(
            self._url(path), data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def api_get(self, path):
        try:
            with urllib.request.urlopen(self._url(path), timeout=10) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()


def _isolate_server(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps([{"handle": "chan_one", "category": "ai_tech"}]), encoding="utf-8")
    (tmp_path / "blacklist.json").write_text(json.dumps({"creators": []}), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources)
    monkeypatch.setattr(config, "BLACKLIST_FILE", tmp_path / "blacklist.json")
    monkeypatch.setattr(recommendations, "FEEDBACK_FILE", tmp_path / "feedback.json")
    rec_file = tmp_path / "recommended.json"
    monkeypatch.setattr(recommendations, "RECOMMENDED_FILE", rec_file)
    rec_file.write_text(json.dumps({
        "version": 1, "creators": [_rec("chan_one"), _rec("fresh_face"), _rec("grumpy")],
    }), encoding="utf-8")
    return sources


def test_recommended_creators_reports_channels_and_filters_dnr(tmp_path, monkeypatch):
    _isolate_server(tmp_path, monkeypatch)
    recommendations.add_do_not_recommend("grumpy")
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_get("/api/recommended-creators")
    finally:
        srv.close()
    assert status == 200
    assert data["success"] is True
    assert "chan_one" in data["channel_handles"]
    served = {c["handle"] for c in data["creators"]}
    assert "grumpy" not in served  # serve-time backstop
    assert {"chan_one", "fresh_face"} <= served  # members still served (as Added)


def test_do_not_recommend_endpoint_prunes_cache(tmp_path, monkeypatch):
    _isolate_server(tmp_path, monkeypatch)
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_post("/api/recommendations/do-not-recommend", {"handle": "@Grumpy"})
        assert status == 200
        assert data["success"] is True and data["handle"] == "grumpy"
        status, data = srv.api_post("/api/recommendations/do-not-recommend", {"handle": "!!!"})
        assert status == 400
        status, data = srv.api_post("/api/recommendations/do-not-recommend", {})
        assert status == 400
    finally:
        srv.close()
    assert recommendations.load_feedback()["do_not_recommend"] == ["grumpy"]
    remaining = json.loads((tmp_path / "recommended.json").read_text(encoding="utf-8"))["creators"]
    assert "grumpy" not in {c["handle"] for c in remaining}


def test_channels_add_clears_stale_dnr(tmp_path, monkeypatch):
    import extractor
    import local_server
    _isolate_server(tmp_path, monkeypatch)
    recommendations.add_do_not_recommend("prodigal")
    monkeypatch.setattr(
        extractor, "follow_creator", lambda handle, **kw: {"ok": True, "state": "followed"})
    monkeypatch.setattr(local_server, "_FOLLOW_RESULTS", {})
    srv = _LiveServer(tmp_path, monkeypatch)
    try:
        status, data = srv.api_post(
            "/api/channels/add", {"handle": "prodigal", "name": "P", "category": "food"})
        assert status == 200 and data["success"] is True
    finally:
        srv.close()
    assert recommendations.load_feedback()["do_not_recommend"] == []


# --- Dashboard template markers ---

def test_dashboard_recommendation_membership_and_dnr_ui():
    html = (config.TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    for marker in ("channel_handles", "addedNow", "Added ✓",
                   "data-dnr-handle", "doNotRecommend",
                   "/api/recommendations/do-not-recommend", "dnr-btn",
                   'data-rec-handle'):
        assert marker in html, marker
