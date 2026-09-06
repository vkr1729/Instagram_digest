"""
test_ranker.py — Unit tests for fair-share viral multiplier ranking.
"""

import pytest
from ranker import (
    calculate_creator_baseline,
    compute_viral_score,
    rank_top_reels,
)


def test_calculate_creator_baseline():
    reels = [
        {"view_count": 1000},
        {"view_count": 5000},
        {"view_count": 2000},
    ]
    baseline = calculate_creator_baseline(reels)
    assert baseline == 2000.0


def test_compute_viral_score():
    reel = {"view_count": 10000, "like_count": 1000, "comment_count": 100}
    baseline = 2000.0
    score = compute_viral_score(reel, baseline)
    # views/baseline = 5.0; engagement = (1000 + 200)/10000 = 0.12; 5.0 * (1 + 0.6) = 8.0
    assert score > 5.0


def test_rank_top_reels_guaranteed_representation_and_capping():
    sources = [
        {"handle": "tech_lead", "name": "Tech Lead", "category": "tech"},
        {"handle": "fitness_pro", "name": "Fitness Pro", "category": "health"},
        {"handle": "science_guy", "name": "Science Guy", "category": "explainer"},
    ]

    candidates = []
    # tech_lead has 10 reels with huge views
    for i in range(10):
        candidates.append({
            "id": f"tl_{i}",
            "creator_handle": "tech_lead",
            "view_count": 100000 + (i * 5000),
            "like_count": 10000,
            "comment_count": 500,
        })

    # fitness_pro has 2 reels with medium views
    for i in range(2):
        candidates.append({
            "id": f"fp_{i}",
            "creator_handle": "fitness_pro",
            "view_count": 20000 + (i * 2000),
            "like_count": 2000,
            "comment_count": 100,
        })

    # science_guy has 1 reel
    candidates.append({
        "id": "sg_0",
        "creator_handle": "science_guy",
        "view_count": 5000,
        "like_count": 500,
        "comment_count": 50,
    })

    ranked = rank_top_reels(candidates, sources, top_n=20, max_per_creator=4)

    # 1. Check guaranteed representation: science_guy must be present
    sg_items = [r for r in ranked if r["creator_handle"] == "science_guy"]
    assert len(sg_items) == 1

    # 2. Check creator cap: tech_lead cannot have more than 4 reels
    tl_items = [r for r in ranked if r["creator_handle"] == "tech_lead"]
    assert len(tl_items) == 4

    # 3. Check fitness_pro has both reels included
    fp_items = [r for r in ranked if r["creator_handle"] == "fitness_pro"]
    assert len(fp_items) == 2

    # 4. Total selected = 4 + 2 + 1 = 7
    assert len(ranked) == 7

    # 5. Ranks are strictly ordered 1 to N
    for idx, item in enumerate(ranked, 1):
        assert item["rank"] == idx
        assert item["rank_display"] == f"#{idx:02d}"
