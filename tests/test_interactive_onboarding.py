"""
test_interactive_onboarding.py — Unit tests for interactive channel onboarding.
"""

import json
import zipfile
from pathlib import Path
import pytest

import config
import interactive_onboarding


def test_infer_archetype_and_category():
    arch, cat = interactive_onboarding._infer_archetype_and_category("ai_code_wizard", "AI Engineer")
    assert arch == "educational"
    assert cat == "ai_tech"

    arch, cat = interactive_onboarding._infer_archetype_and_category("finance_daily", "Stock Market Investor")
    assert arch == "educational"
    assert cat == "finance"

    arch, cat = interactive_onboarding._infer_archetype_and_category("daily_memes", "Funny Clips")
    assert arch == "lifestyle"
    assert cat == "entertainment"


def test_infer_health_and_food_buckets():
    """Compound handles must land in health/food, not entertainment."""
    assert interactive_onboarding._infer_archetype_and_category("monitormunchies", "Murali") == ("educational", "food")
    assert interactive_onboarding._infer_archetype_and_category("foodpharmer", "Revant") == ("educational", "food")
    assert interactive_onboarding._infer_archetype_and_category("dr.pal.manickam", "Dr Pal") == ("educational", "health")


def test_reconcile_tolerates_malformed_rows(tmp_path, monkeypatch):
    """None list, non-dict rows, and missing handles never raise."""
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources_path)

    assert interactive_onboarding.reconcile_following(None)["total_extracted"] == 0
    report = interactive_onboarding.reconcile_following(
        [{"handle": None}, {"x": 1}, None, {"handle": "  "}, {"handle": "good_handle"}])
    assert report["missing_count"] == 1
    assert report["missing"][0]["handle"] == "good_handle"


def test_merge_tolerates_malformed_inputs(tmp_path, monkeypatch):
    """Non-string handles and None candidates are skipped, never crash."""
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources_path)

    assert interactive_onboarding.merge_imported_channels([None, 123], [{"handle": "x"}]) == 0
    assert interactive_onboarding.merge_imported_channels(["x"], None) == 0
    assert interactive_onboarding.merge_imported_channels(None, None) == 0


def test_merge_defaults_new_channels_to_niche_untagged(tmp_path, monkeypatch):
    """Plan Phase 5: new imports default to category niche, archetype null."""
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources_path)

    added = interactive_onboarding.merge_imported_channels(
        ["fresh_handle"], [{"handle": "fresh_handle", "name": "Fresh"}])
    assert added == 1
    updated = json.loads(sources_path.read_text(encoding="utf-8"))
    assert updated[0]["category"] == "niche"
    assert updated[0]["archetype"] is None
    assert updated[0]["enabled"] is True


def test_parse_following_export_text(tmp_path):
    txt_file = tmp_path / "following.txt"
    txt_file.write_text("@mkbhd\n# comment line\nveritasium\n\n@mrwhosetheboss\n", encoding="utf-8")

    items = interactive_onboarding.parse_following_export(txt_file)
    assert len(items) == 3
    assert [i["handle"] for i in items] == ["mkbhd", "veritasium", "mrwhosetheboss"]


def test_parse_following_export_json(tmp_path):
    json_file = tmp_path / "following.json"
    data = {
        "relationships_following": [
            {"string_list_data": [{"value": "hubermanlab"}]},
            {"string_list_data": [{"value": "dr.sujit_telagamsetty"}]},
        ]
    }
    json_file.write_text(json.dumps(data), encoding="utf-8")

    items = interactive_onboarding.parse_following_export(json_file)
    assert len(items) == 2
    assert items[0]["handle"] == "hubermanlab"
    assert items[1]["handle"] == "dr.sujit_telagamsetty"


def test_parse_following_export_zip(tmp_path):
    zip_path = tmp_path / "export.zip"
    data = {
        "relationships_following": [
            {"string_list_data": [{"value": "tech_creator"}]}
        ]
    }
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("followers_and_following/following.json", json.dumps(data))

    items = interactive_onboarding.parse_following_export(zip_path)
    assert len(items) == 1
    assert items[0]["handle"] == "tech_creator"


def test_reconcile_following_additive(tmp_path, monkeypatch):
    sources_path = tmp_path / "sources.json"
    existing = [
        {"handle": "existing_creator", "name": "Existing", "category": "finance", "archetype": "educational", "enabled": True}
    ]
    sources_path.write_text(json.dumps(existing), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources_path)

    following_list = [
        {"handle": "existing_creator", "name": "Existing"},
        {"handle": "brand_new_creator", "name": "Brand New"},
    ]

    report = interactive_onboarding.reconcile_following(following_list)
    assert report["total_extracted"] == 2
    assert report["already_present_count"] == 1
    assert report["missing_count"] == 1

    assert report["already_present"][0]["handle"] == "existing_creator"
    assert report["already_present"][0]["already_present"] is True

    assert report["missing"][0]["handle"] == "brand_new_creator"
    assert report["missing"][0]["already_present"] is False


def test_merge_imported_channels_preserves_existing(tmp_path, monkeypatch):
    sources_path = tmp_path / "sources.json"
    existing = [
        {"handle": "channel1", "name": "Channel One", "category": "tech", "archetype": "educational", "enabled": True}
    ]
    sources_path.write_text(json.dumps(existing), encoding="utf-8")
    monkeypatch.setattr(config, "SOURCES_FILE", sources_path)

    candidates = [
        {"handle": "channel2", "name": "Channel Two", "category": "finance", "archetype": "educational"},
        {"handle": "channel3", "name": "Channel Three", "category": "entertainment", "archetype": "lifestyle"},
    ]

    # Select only channel2
    added = interactive_onboarding.merge_imported_channels(["channel2"], candidates)
    assert added == 1

    updated = json.loads(sources_path.read_text(encoding="utf-8"))
    assert len(updated) == 2
    # Ensure channel1 is untouched
    assert updated[0]["handle"] == "channel1"
    # Ensure channel2 was added
    assert updated[1]["handle"] == "channel2"
    assert updated[1]["category"] == "finance"
    assert updated[1]["archetype"] == "educational"
    assert updated[1]["enabled"] is True
