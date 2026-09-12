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


def test_score_ordered_fill_with_category_ceiling():
    """
    No fixed percentage targets: after the 1-per-creator guarantee, remaining
    slots fill by pure viral score. Entertainment outscores everything here, so
    without a guard it would take nearly all 60 slots; the 50% ceiling must
    bind it at 30, with the next-best categories filling the rest by score.
    Categories must still be interleaved, not clumped.
    """
    from collections import Counter

    # (category, creator count, likes per reel) — engagement ratio drives score
    categories_setup = {
        "entertainment": (30, 8000),
        "health": (5, 4000),
        "finance": (10, 2000),
        "niche": (5, 1000),
    }

    sources = []
    candidates = []
    reel_id = 0

    for cat, (num_creators, likes) in categories_setup.items():
        for c_idx in range(num_creators):
            handle = f"{cat}_creator_{c_idx}"
            sources.append({
                "handle": handle,
                "name": f"{cat.title()} Creator {c_idx}",
                "category": cat,
            })
            # Generate 5 candidate reels per creator
            for r_idx in range(5):
                candidates.append({
                    "id": f"reel_{reel_id}",
                    "creator_handle": handle,
                    "view_count": 20000 + (r_idx * 1000),
                    "like_count": likes,
                    "comment_count": likes // 20,
                })
                reel_id += 1

    ranked = rank_top_reels(
        candidates,
        sources,
        top_n=60,
        max_per_creator=4,
        seed="2026-09-06",
        shuffle=True,
    )

    assert len(ranked) == 60

    counts = Counter(r["category"] for r in ranked)
    # Ceiling binds: 30 guaranteed entertainment reels, zero more despite top scores
    assert counts["entertainment"] == 30
    assert counts["entertainment"] <= 60 * 0.50
    # Next-best category (health) takes all 10 fill slots by score priority
    assert counts["health"] == 5 + 10
    assert counts["finance"] == 10  # guarantee only — outscored for fill slots
    assert counts["niche"] == 5     # guarantee only — outscored for fill slots

    # Guaranteed representation: every creator appears at least once
    handles = {r["creator_handle"] for r in ranked}
    assert handles == {s["handle"] for s in sources}

    # Per-creator cap still respected
    per_creator = Counter(r["creator_handle"] for r in ranked)
    assert max(per_creator.values()) <= 4

    # Verify interleaving (categories should not be clumped in one block)
    first_10_cats = [r["category"] for r in ranked[:10]]
    # In first 10 reels, there should be at least 3 distinct categories
    assert len(set(first_10_cats)) >= 3, f"Categories are clumped: {first_10_cats}"

