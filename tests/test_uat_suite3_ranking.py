"""
test_uat_suite3_ranking.py — Automated tests for Suite 3: Fair-Share Viral Ranking.
"""

import json
import pytest
import config
from ranker import (
    calculate_creator_baseline,
    compute_viral_score,
    rank_top_reels,
    save_digest_batch,
)


def test_uat_3_1_creator_normalization():
    """
    UAT-3.1 Creator Normalization:
    A boutique creator with baseline 1,000 views getting 10,000 views (10x baseline)
    should score higher than a mega creator with baseline 500,000 views getting 600,000 views (1.2x baseline).
    """
    boutique_reel = {"view_count": 10000, "like_count": 1500, "comment_count": 200}
    boutique_baseline = 1000.0

    mega_reel = {"view_count": 600000, "like_count": 30000, "comment_count": 1000}
    mega_baseline = 500000.0

    boutique_score = compute_viral_score(boutique_reel, boutique_baseline)
    mega_score = compute_viral_score(mega_reel, mega_baseline)

    assert boutique_score > mega_score


def test_uat_3_2_and_3_3_and_3_4_guaranteed_rep_capping_and_deterministic_output(tmp_path, monkeypatch):
    """
    UAT-3.2, 3.3, 3.4: Tests representation, 4-reel cap, deterministic rank order #01-#N.
    """
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "top100_digest.json")

    sources = [
        {"handle": f"creator_{i}", "name": f"Creator {i}", "category": "tech"}
        for i in range(10)
    ]

    candidates = []
    # Creator 0 has 20 reels
    for j in range(20):
        candidates.append({
            "id": f"c0_{j}",
            "creator_handle": "creator_0",
            "view_count": 50000 + (j * 1000),
            "like_count": 5000,
            "comment_count": 100,
        })

    # Creators 1 to 9 have 1 reel each
    for i in range(1, 10):
        candidates.append({
            "id": f"c{i}_0",
            "creator_handle": f"creator_{i}",
            "view_count": 10000,
            "like_count": 1000,
            "comment_count": 50,
        })

    ranked = rank_top_reels(candidates, sources, top_n=100, max_per_creator=4)

    # 1. Guaranteed representation: all 10 creators must be in ranked list
    creators_in_ranked = {r["creator_handle"] for r in ranked}
    for i in range(10):
        assert f"creator_{i}" in creators_in_ranked

    # 2. Creator Saturation Cap: creator_0 has at most 4 reels
    c0_count = sum(1 for r in ranked if r["creator_handle"] == "creator_0")
    assert c0_count == 4

    # 3. Total selected: 4 (creator_0) + 9 (creators 1-9) = 13
    assert len(ranked) == 13

    # 4. Deterministic output: ranks #01 to #13
    for idx, item in enumerate(ranked, 1):
        assert item["rank"] == idx
        assert item["rank_display"] == f"#{idx:02d}"

    # 5. Output file verification
    saved_path = save_digest_batch(ranked, run_date="2026-09-06")
    assert saved_path.exists()
    batch_json = json.loads(saved_path.read_text(encoding="utf-8"))
    assert batch_json["count"] == 13
    assert batch_json["run_date"] == "2026-09-06"
