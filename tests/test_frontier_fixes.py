"""Regression tests for frontier-review fixes B1, B4, B5."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import cookie_exporter
import main


class _ProbeFake:
    """Mimics InstagramSession's lifecycle for the step-0 probe gate."""

    def __init__(self, validations):
        self._validations = list(validations)
        self.validations = 0
        self.closed = 0
        self.started = 0

    def validate(self):
        self.validations += 1
        return self._validations.pop(0)

    def close(self):
        self.closed += 1

    def start(self):
        self.started += 1


def test_probe_session_closed_on_success(monkeypatch):
    probe = _ProbeFake([True])
    monkeypatch.setattr(main.extractor, "InstagramSession", lambda: probe)
    assert main._probe_session_once() is True
    assert probe.closed == 1


def test_probe_session_closed_on_failure(monkeypatch):
    probe = _ProbeFake([False])
    monkeypatch.setattr(main.extractor, "InstagramSession", lambda: probe)
    monkeypatch.setattr(main, "_ensure_valid_session", lambda session: False)
    assert main._probe_session_once() is False
    assert probe.closed == 1


def _ckpt(**over):
    base = {"version": 1, "limit_per_creator": 15, "since_timestamp": 1000,
            "stage": "extracting", "week_id": "2026-09-25"}
    base.update(over)
    return base


def test_b5_resume_continues_despite_anchor_drift():
    banked = _ckpt(since_timestamp=1000)
    assert main._sync_progress_usable(banked, 15, 9999, True, 1) is True


def test_b5_non_resume_still_requires_matching_anchor():
    banked = _ckpt(since_timestamp=1000)
    assert main._sync_progress_usable(banked, 15, 9999, False, 1) is False
    assert main._sync_progress_usable(banked, 15, 1000, False, 1) is True


def test_b5_stale_or_wrong_stage_still_rejected_on_resume():
    assert main._sync_progress_usable(_ckpt(), 15, 1, True, 99) is False
    assert main._sync_progress_usable(_ckpt(stage="zzz"), 15, 1, True, 1) is False
    assert main._sync_progress_usable(_ckpt(limit_per_creator=5), 15, 1, True, 1) is False


def test_b1_same_day_rerun_keeps_live_ids(tmp_path, monkeypatch):
    digest = tmp_path / "top100_digest.json"
    digest.write_text(json.dumps({
        "run_date": "2026-09-25",
        "items": [{"id": "live1"}, {"id": "shared"}],
    }))
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", digest)
    keep = main._current_week_stray_keep_ids(
        "2026-09-25", [{"id": "shared"}, {"id": "new1"}])
    assert keep == {"live1", "shared", "new1"}


def test_b1_other_weeks_keep_only_ranked(tmp_path, monkeypatch):
    digest = tmp_path / "top100_digest.json"
    digest.write_text(json.dumps({
        "run_date": "2026-09-19",
        "items": [{"id": "old1"}],
    }))
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", digest)
    keep = main._current_week_stray_keep_ids("2026-09-25", [{"id": "new1"}])
    assert keep == {"new1"}


def test_b1_unreadable_live_digest_skips_purge(tmp_path, monkeypatch):
    digest = tmp_path / "top100_digest.json"
    digest.write_text("{broken")
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", digest)
    # live week == run week forces a live read; corrupt -> None (skip purge).
    import datetime
    monkeypatch.setattr(
        main, "_persisted_digest_week", lambda: "2026-09-25")
    assert main._current_week_stray_keep_ids("2026-09-25", [{"id": "x"}]) is None


def test_b4_expiry_converts_chrome_epoch():
    # 2026-01-01T00:00:00Z in Chrome µs -> same instant in Unix seconds.
    assert cookie_exporter.netscape_cookie_expiry(13411699200000000) == 1767225600


def test_b4_session_cookies_never_expire():
    assert cookie_exporter.netscape_cookie_expiry(0) == 2147483647
    assert cookie_exporter.netscape_cookie_expiry(None) == 2147483647


def test_b4_expiry_is_not_year_2401():
    import time
    now_chrome = int((time.time() + 11644473600) * 1_000_000)
    exp = cookie_exporter.netscape_cookie_expiry(now_chrome)
    assert abs(exp - time.time()) < 5
