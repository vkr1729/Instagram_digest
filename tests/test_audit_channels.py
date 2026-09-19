"""Tests for audit_channels.py (monthly account<->digest reconciliation)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audit_channels
import config


@pytest.fixture
def _files(tmp_path, monkeypatch):
    src = tmp_path / "sources.json"
    bl = tmp_path / "blacklist.json"
    monkeypatch.setattr(config, "SOURCES_FILE", src)
    monkeypatch.setattr(config, "BLACKLIST_FILE", bl)
    src.write_text(json.dumps([
        {"handle": "kept_one", "name": "Kept", "category": "niche", "enabled": True},
        {"handle": "unfollowed_one", "name": "Unfollowed", "category": "finance", "enabled": True},
        {"handle": "disabled_one", "name": "Disabled", "category": "food", "enabled": False},
    ]))
    bl.write_text(json.dumps({"creators": ["muted_person"]}))
    return src


def _mock_following(monkeypatch, accounts):
    monkeypatch.setattr(
        audit_channels.extractor, "sync_following_accounts",
        lambda force=False: accounts,
    )


def test_diff_both_directions(_files, monkeypatch):
    _mock_following(monkeypatch, [
        {"handle": "kept_one", "name": "Kept"},
        {"handle": "new_on_ig", "name": "New On Ig"},
        {"handle": "muted_person", "name": "Muted"},
        {"handle": "disabled_one", "name": "Disabled"},
    ])
    report = audit_channels.audit()
    assert report["following_count"] == 4
    missing = {e["handle"] for e in report["missing_from_digest"]}
    assert missing == {"new_on_ig"}
    unfollowed = {e["handle"] for e in report["not_followed_on_ig"]}
    assert unfollowed == {"unfollowed_one"}
    cat = next(e["category"] for e in report["missing_from_digest"])
    assert isinstance(cat, str) and cat


def test_in_sync_empty_diff(_files, monkeypatch):
    _mock_following(monkeypatch, [
        {"handle": "kept_one", "name": "Kept"},
        {"handle": "unfollowed_one", "name": "Unfollowed"},
        {"handle": "disabled_one", "name": "Disabled"},
    ])
    report = audit_channels.audit()
    assert report["missing_from_digest"] == []
    assert report["not_followed_on_ig"] == []


def test_import_missing_adds_to_sources(_files, monkeypatch):
    _mock_following(monkeypatch, [
        {"handle": "kept_one", "name": "Kept"},
        {"handle": "brand_new", "name": "Brand New"},
    ])
    report = audit_channels.audit()
    added = audit_channels.import_missing(report)
    assert added == 1
    sources = json.loads(config.SOURCES_FILE.read_text())
    assert "brand_new" in {s["handle"] for s in sources}
    assert audit_channels.import_missing(audit_channels.audit()) == 0


def test_audit_never_forces_scrape(_files, monkeypatch):
    calls = []
    monkeypatch.setattr(
        audit_channels.extractor, "sync_following_accounts",
        lambda force=False: (calls.append(force), [])[1],
    )
    audit_channels.audit()
    assert calls == [False]


def test_audit_handles_broken_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(config, "BLACKLIST_FILE", tmp_path / "nope2.json")
    monkeypatch.setattr(
        audit_channels.extractor, "sync_following_accounts",
        lambda force=False: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    report = audit_channels.audit()
    assert report["missing_from_digest"] == []
    assert report["not_followed_on_ig"] == []
