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


def test_rank_top_reels_per_creator_cap_dict():
    """Verify max_per_creator as a dict allows higher caps (e.g. 8) for recommended channels while keeping 4 for followed."""
    from collections import Counter

    sources = [
        {"handle": "followed_user", "name": "Followed User", "category": "tech"},
        {"handle": "recommended_star", "name": "Recommended Star", "category": "health"},
    ]

    candidates = []
    for i in range(12):
        candidates.append({
            "id": f"fu_{i}",
            "creator_handle": "followed_user",
            "view_count": 50000 + (i * 1000),
            "like_count": 5000,
            "comment_count": 200,
        })
    for i in range(12):
        candidates.append({
            "id": f"rs_{i}",
            "creator_handle": "recommended_star",
            "view_count": 60000 + (i * 1000),
            "like_count": 6000,
            "comment_count": 300,
        })

    caps = {"followed_user": 4, "recommended_star": 8}
    ranked = rank_top_reels(candidates, sources, top_n=20, max_per_creator=caps, shuffle=False)

    counts = Counter(r["creator_handle"] for r in ranked)
    assert counts["followed_user"] == 4
    assert counts["recommended_star"] == 8
    assert len(ranked) == 12


def test_favorites_get_guarantee_cap_and_boost(monkeypatch):
    """Favorites: 2 guaranteed base picks, cap up to FAVORITE_MAX, gentler scoring."""
    import config
    from collections import Counter

    monkeypatch.setattr(config, "FAVORITE_GUARANTEED_PICKS", 2)
    monkeypatch.setattr(config, "FAVORITE_MAX_PER_CREATOR", 8)
    monkeypatch.setattr(config, "FAVORITE_SCORE_BOOST", 1.5)
    monkeypatch.setattr(config, "MAX_CATEGORY_SHARE", 1.0)

    sources = [
        {"handle": "fav_creator", "name": "Fav", "category": "niche", "favorite": True},
        {"handle": "normal_creator", "name": "Normal", "category": "niche"},
    ]
    candidates = []
    for i in range(5):
        candidates.append({
            "id": f"fav_{i}",
            "creator_handle": "fav_creator",
            "view_count": 5000,
            "like_count": 50,
            "comment_count": 5,
        })
    for i in range(5):
        candidates.append({
            "id": f"norm_{i}",
            "creator_handle": "normal_creator",
            "view_count": 50000,
            "like_count": 5000,
            "comment_count": 200,
        })

    ranked = rank_top_reels(candidates, sources, top_n=10, max_per_creator=4, shuffle=False)
    counts = Counter(r["creator_handle"] for r in ranked)
    assert counts["fav_creator"] >= 2
    assert all(r.get("is_favorite") for r in ranked if r["creator_handle"] == "fav_creator")
    assert all(not r.get("is_favorite") for r in ranked if r["creator_handle"] == "normal_creator")

    only_fav = [{"id": f"f{i}", "creator_handle": "fav_creator",
                 "view_count": 50000 + i * 100, "like_count": 5000, "comment_count": 200}
                for i in range(10)]
    ranked2 = rank_top_reels(only_fav, sources, top_n=10, max_per_creator=4, shuffle=False)
    assert len([r for r in ranked2 if r["creator_handle"] == "fav_creator"]) == 8


def test_favorite_guarantee_respects_top_n(monkeypatch):
    """More guaranteed picks than slots: digest never exceeds top_n."""
    import config

    monkeypatch.setattr(config, "FAVORITE_GUARANTEED_PICKS", 2)
    monkeypatch.setattr(config, "MAX_CATEGORY_SHARE", 1.0)
    sources = [{"handle": f"c{i}", "name": f"C{i}", "category": "niche", "favorite": True}
               for i in range(5)]
    candidates = [{"id": f"r{i}_{j}", "creator_handle": f"c{i}",
                   "view_count": 10000, "like_count": 100, "comment_count": 10}
                  for i in range(5) for j in range(2)]
    ranked = rank_top_reels(candidates, sources, top_n=3, max_per_creator=8, shuffle=False)
    assert len(ranked) == 3


def test_favorite_guarantee_takes_top_scores_first(monkeypatch):
    """Overflow truncation keeps the highest-score first-picks, not insertion order."""
    import config

    monkeypatch.setattr(config, "FAVORITE_GUARANTEED_PICKS", 2)
    monkeypatch.setattr(config, "FAVORITE_SCORE_BOOST", 1.0)
    monkeypatch.setattr(config, "MAX_CATEGORY_SHARE", 1.0)
    sources = [
        {"handle": "weak", "name": "Weak", "category": "niche", "favorite": True},
        {"handle": "strong", "name": "Strong", "category": "niche", "favorite": True},
    ]
    candidates = [
        {"id": "w0", "creator_handle": "weak", "view_count": 1000, "like_count": 1, "comment_count": 0},
        {"id": "w1", "creator_handle": "weak", "view_count": 1000, "like_count": 1, "comment_count": 0},
        {"id": "s0", "creator_handle": "strong", "view_count": 90000, "like_count": 9000, "comment_count": 400},
        {"id": "s1", "creator_handle": "strong", "view_count": 80000, "like_count": 8000, "comment_count": 400},
    ]
    ranked = rank_top_reels(candidates, sources, top_n=2, max_per_creator=8, shuffle=False)
    assert [r["id"] for r in ranked] == ["s0", "s1"]


def test_favorite_boost_idempotent_across_passes(monkeypatch):
    """Re-ranking pass-1 output (stamped is_favorite) must not compound the boost."""
    import config

    monkeypatch.setattr(config, "FAVORITE_GUARANTEED_PICKS", 2)
    monkeypatch.setattr(config, "FAVORITE_MAX_PER_CREATOR", 8)
    monkeypatch.setattr(config, "FAVORITE_SCORE_BOOST", 1.5)
    monkeypatch.setattr(config, "MAX_CATEGORY_SHARE", 1.0)
    sources = [{"handle": "fav", "name": "Fav", "category": "niche", "favorite": True}]
    raw = [{"id": f"f{i}", "creator_handle": "fav",
            "view_count": 20000, "like_count": 2000, "comment_count": 100} for i in range(4)]
    once = rank_top_reels(raw, sources, top_n=4, max_per_creator=8, shuffle=False)
    twice = rank_top_reels(once, sources, top_n=4, max_per_creator=8, shuffle=False)
    assert [r["viral_score"] for r in once] == [r["viral_score"] for r in twice]


def test_favorite_string_value_is_not_promoted(monkeypatch):
    """A manual-edit "favorite": "false" string must not promote the creator."""
    import config
    from collections import Counter

    monkeypatch.setattr(config, "FAVORITE_GUARANTEED_PICKS", 2)
    monkeypatch.setattr(config, "MAX_CATEGORY_SHARE", 1.0)
    sources = [
        {"handle": "strfav", "name": "S", "category": "niche", "favorite": "false"},
        {"handle": "plain", "name": "P", "category": "niche"},
    ]
    candidates = [{"id": f"{h}_{i}", "creator_handle": h,
                   "view_count": 10000, "like_count": 100, "comment_count": 10}
                  for h in ("strfav", "plain") for i in range(3)]
    ranked = rank_top_reels(candidates, sources, top_n=10, max_per_creator=4, shuffle=False)
    counts = Counter(r["creator_handle"] for r in ranked)
    assert counts["strfav"] == counts["plain"] == 3
    assert all(not r.get("is_favorite") for r in ranked)

