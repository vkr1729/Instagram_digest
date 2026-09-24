"""Tests for the enhancement batch: trust guardrails, Tier 3 caps, dedup ledger,
dry-run estimator, cooldown, warming, and audit inactivity."""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import main


@pytest.fixture
def _seen_file(tmp_path, monkeypatch):
    p = tmp_path / "seen_reel_ids.json"
    monkeypatch.setattr(main, "SEEN_IDS_FILE", p)
    return p


def test_seen_ledger_record_and_filter(_seen_file):
    reels = [{"id": f"r{i}"} for i in range(5)]
    assert main._record_seen_reel_ids(reels) == 5
    assert main._record_seen_reel_ids(reels[:2]) == 0
    fresh = main._filter_seen_reel_ids([{"id": "r0"}, {"id": "new1"}])
    assert [c["id"] for c in fresh] == ["new1"]


def test_seen_ledger_prunes_stale(_seen_file):
    old = time.time() - 40 * 86400
    _seen_file.write_text(json.dumps({"old_reel": old, "new_reel": time.time()}))
    seen = main._load_seen_reel_ids()
    assert "old_reel" not in seen and "new_reel" in seen


def test_seen_filter_never_empties_pool(_seen_file):
    main._record_seen_reel_ids([{"id": "only"}])
    pool = [{"id": "only"}]
    assert main._filter_seen_reel_ids(pool) == pool


def test_seen_ledger_tolerates_corrupt(_seen_file):
    _seen_file.write_text("not json{{{")
    assert main._load_seen_reel_ids() == {}
    assert main._filter_seen_reel_ids([{"id": "a"}]) == [{"id": "a"}]


def test_follow_cooldown_blocks_burst(tmp_path, monkeypatch):
    prog = tmp_path / "follow_progress.json"
    prog.write_text(json.dumps({
        "done": [f"h{i}" for i in range(70)],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FOLLOW_COOLDOWN_HOURS", 48.0)
    monkeypatch.setattr(config, "FOLLOW_BURST_THRESHOLD", 20)
    assert main._check_follow_cooldown(force=False) is False
    assert main._check_follow_cooldown(force=True) is True


def test_follow_cooldown_passes_old_or_small(tmp_path, monkeypatch):
    prog = tmp_path / "follow_progress.json"
    prog.write_text(json.dumps({"done": ["a"], "started_at": "2020-01-01T00:00:00+00:00"}))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FOLLOW_COOLDOWN_HOURS", 48.0)
    monkeypatch.setattr(config, "FOLLOW_BURST_THRESHOLD", 20)
    assert main._check_follow_cooldown(force=False) is True


def test_follow_cooldown_no_file_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert main._check_follow_cooldown(force=False) is True


def test_trust_warming_auto_on_for_young_account(tmp_path, monkeypatch):
    prog = tmp_path / "follow_progress.json"
    prog.write_text(json.dumps({
        "done": ["a"],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "TRUST_WARMING", "")
    assert main._trust_warming_active() is True


def test_trust_warming_off_for_old_account(tmp_path, monkeypatch):
    prog = tmp_path / "follow_progress.json"
    prog.write_text(json.dumps({"done": ["a"], "started_at": "2020-01-01T00:00:00+00:00"}))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "TRUST_WARMING", "")
    assert main._trust_warming_active() is False


def test_trust_warming_flag_forces(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "TRUST_WARMING", "1")
    assert main._trust_warming_active() is True
    monkeypatch.setattr(config, "TRUST_WARMING", "0")
    assert main._trust_warming_active() is False


def test_tier3_share_cap_math():
    assert int(250 * 0.40) == 100
    assert int(config.TOP_DIGEST_COUNT * config.MAX_EXTERNAL_SHARE) == 100
    assert config.MAX_FEED_EVALUATIONS == 1000
    assert config.MIN_EXTERNAL_LIKES == 25000
    assert config.MIN_EXTERNAL_COMMENTS == 150


def test_config_parses_new_env(monkeypatch):
    monkeypatch.setenv("MAX_EXTERNAL_SHARE", "0.30")
    monkeypatch.setenv("MAX_FEED_EVALUATIONS", "500")
    monkeypatch.setenv("MIN_EXTERNAL_LIKES", "10000")
    import importlib
    importlib.reload(config)
    try:
        assert config.MAX_EXTERNAL_SHARE == 0.30
        assert config.MAX_FEED_EVALUATIONS == 500
        assert config.MIN_EXTERNAL_LIKES == 10000
    finally:
        for k in ("MAX_EXTERNAL_SHARE", "MAX_FEED_EVALUATIONS", "MIN_EXTERNAL_LIKES"):
            monkeypatch.delenv(k, raising=False)
        importlib.reload(config)
