"""
ranker.py — Fair-share viral multiplier ranking algorithm for Top 100 Instagram reels.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any

import config

logger = logging.getLogger("InstagramDigest.Ranker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def calculate_creator_baseline(reels: list[dict[str, Any]]) -> float:
    """Calculate median view count baseline for a creator's recent reels."""
    views = [max(1, r.get("view_count", 0)) for r in reels]
    if not views:
        return 1000.0
    return float(statistics.median(views))


def compute_viral_score(reel: dict[str, Any], baseline_views: float) -> float:
    """
    Compute Bayesian-damped creator-normalized viral score:
    1. Reach Ratio: views relative to creator's median baseline.
    2. Sublinear Damping: (reach_ratio ** 0.75) * log10(views) dampens tiny micro-creator spikes
       while rewarding authentic community-wide breakout hits.
    3. Bayesian-smoothed Engagement: (likes + 2*comments + 5) / (views + 100) with 2x comment weight.
    4. Recency decay within the 7-day window.
    """
    import math

    views = max(1, reel.get("view_count", 0))
    likes = reel.get("like_count", 0)
    comments = reel.get("comment_count", 0)

    # 1. Reach ratio relative to creator's median baseline
    reach_ratio = views / max(200.0, baseline_views)

    # 2. Damped reach with logarithmic view scaling
    log_scale = math.log10(max(10.0, float(views)))
    damped_reach = math.pow(reach_ratio, 0.75) * log_scale

    # 3. Bayesian-smoothed engagement rate (weights comments 2x, Laplace smoothing prior)
    smooth_engagement = (likes + (comments * 2.0) + 5.0) / (views + 100.0)

    # 4. Composite viral score
    score = damped_reach * (1.0 + (smooth_engagement * 4.0))

    # 5. Mild recency bonus within 7-day window if timestamp is present
    ts = reel.get("timestamp")
    if ts:
        age_hours = max(0.0, (datetime.now(timezone.utc).timestamp() - ts) / 3600.0)
        recency_factor = 1.0 / math.pow((age_hours + 12.0) / 24.0, 0.15)
        score *= recency_factor

    return round(score, 3)


def get_blacklisted_creators() -> set[str]:
    """Retrieve set of blacklisted creator handles (lowercase)."""
    if hasattr(config, "BLACKLIST_FILE") and config.BLACKLIST_FILE.exists():
        try:
            data = json.loads(config.BLACKLIST_FILE.read_text(encoding="utf-8"))
            return set(h.lower().replace("@", "") for h in data.get("creators", []))
        except Exception:
            pass
    return set()


CATEGORY_ALIASES = {
    "tech": "ai_tech",
    "explainer": "niche",
    "culture": "entertainment",
}


def get_category_quotas(top_n: int) -> dict[str, int]:
    """Calculate reel quotas per category based on configured percentages."""
    cats = [c for c in getattr(config, "CATEGORIES", []) if c.get("id") != "all" and "target_pct" in c]
    if cats:
        return {c["id"]: max(1, round(top_n * c.get("target_pct", 0.10))) for c in cats}
    # Fallback to standard 6 categories
    return {
        "entertainment": max(1, round(top_n * 0.40)),
        "finance": max(1, round(top_n * 0.15)),
        "ai_tech": max(1, round(top_n * 0.15)),
        "niche": max(1, round(top_n * 0.10)),
        "health": max(1, round(top_n * 0.10)),
        "food": max(1, round(top_n * 0.10)),
    }


def rank_top_reels(
    candidates: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    top_n: int = config.TOP_DIGEST_COUNT,
    max_per_creator: int = config.MAX_PER_CREATOR,
    seed: str | int | None = None,
    shuffle: bool = True,
) -> list[dict[str, Any]]:
    """
    Execute Fair-Share Ranking with Category Quotas & Deterministic Interleaving:
    1. Calculate baseline per creator and viral scores.
    2. Normalize category assignments across the 6 thematic buckets.
    3. Guarantee representation (at least 1 top reel for every active creator).
    4. Fill category quotas (40% Entertainment, 15% Finance, 15% AI & Tech, 10% Niche, 10% Health, 10% Food).
    5. Cap maximum reels per creator (e.g. max 4).
    6. Fill remaining slots with highest scoring outliers up to top_n.
    7. Pseudo-randomly interleave/shuffle the final selected pool (using seed) so categories blend smoothly.
    8. Assign sequential ranks #01 to #N.
    """
    import random

    blacklist = get_blacklisted_creators()
    if blacklist:
        candidates = [c for c in candidates if c.get("creator_handle", "").lower().replace("@", "") not in blacklist]
        sources = [s for s in sources if s.get("handle", "").lower().replace("@", "") not in blacklist]

    if not candidates:
        logger.warning("No candidate reels provided to ranker.")
        return []

    # Map sources by handle for category and display name lookup
    sources_by_handle = {s["handle"].lower().replace("@", ""): s for s in sources if "handle" in s}

    # Group candidate reels by creator
    by_creator: dict[str, list[dict[str, Any]]] = {}
    for r in candidates:
        h = r["creator_handle"].lower().replace("@", "")
        by_creator.setdefault(h, []).append(r)

    # Calculate baselines and assign scores
    scored_pool: list[dict[str, Any]] = []
    for handle, creator_reels in by_creator.items():
        baseline = calculate_creator_baseline(creator_reels)
        src = sources_by_handle.get(handle, {})
        creator_name = src.get("name") or handle
        raw_category = src.get("category") or "entertainment"
        category = CATEGORY_ALIASES.get(raw_category, raw_category)

        for reel in creator_reels:
            score = compute_viral_score(reel, baseline)
            item = dict(reel)
            item["creator_name"] = creator_name
            item["category"] = category
            item["viral_score"] = score
            scored_pool.append(item)

    # Sort each creator's reels descending by viral_score
    creator_queues: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(scored_pool, key=lambda x: x["viral_score"], reverse=True):
        creator_queues.setdefault(item["creator_handle"].lower().replace("@", ""), []).append(item)

    selected: list[dict[str, Any]] = []
    creator_counts: dict[str, int] = {h: 0 for h in creator_queues}
    used_ids: set[str] = set()

    # Step 1: Guaranteed Representation — Pick #1 top reel for each active creator
    for handle, q in creator_queues.items():
        if q:
            top_pick = q.pop(0)
            selected.append(top_pick)
            creator_counts[handle] += 1
            used_ids.add(top_pick["id"])

    logger.info("Guaranteed representation selected %d reels (1 per creator).", len(selected))

    # Step 2: Pool remaining candidate reels by category
    quotas = get_category_quotas(top_n)
    category_counts: dict[str, int] = {}
    for s in selected:
        cat = s.get("category", "entertainment")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Group remaining reels by category
    remaining_by_category: dict[str, list[dict[str, Any]]] = {}
    for q in creator_queues.values():
        for item in q:
            cat = item.get("category", "entertainment")
            remaining_by_category.setdefault(cat, []).append(item)

    for cat in remaining_by_category:
        remaining_by_category[cat].sort(key=lambda x: x["viral_score"], reverse=True)

    # Step 3: Fulfill category quotas up to target count respecting creator caps
    for cat, target in quotas.items():
        cat_reels = remaining_by_category.get(cat, [])
        for item in cat_reels:
            if len(selected) >= top_n:
                break
            if category_counts.get(cat, 0) >= target:
                break
            h = item["creator_handle"].lower().replace("@", "")
            if creator_counts[h] < max_per_creator and item["id"] not in used_ids:
                selected.append(item)
                creator_counts[h] += 1
                category_counts[cat] = category_counts.get(cat, 0) + 1
                used_ids.add(item["id"])

    # Step 4: Fill any remaining capacity up to top_n from the global pool
    if len(selected) < top_n:
        overflow_pool: list[dict[str, Any]] = []
        for cat_reels in remaining_by_category.values():
            for item in cat_reels:
                if item["id"] not in used_ids:
                    overflow_pool.append(item)
        overflow_pool.sort(key=lambda x: x["viral_score"], reverse=True)

        for item in overflow_pool:
            if len(selected) >= top_n:
                break
            h = item["creator_handle"].lower().replace("@", "")
            if creator_counts[h] < max_per_creator and item["id"] not in used_ids:
                selected.append(item)
                creator_counts[h] += 1
                used_ids.add(item["id"])

    # Step 5: Deterministic Interleaving / Shuffling
    # Mixes categories evenly across the feed so it's not clumped category-by-category
    if shuffle:
        rng_seed = str(seed if seed is not None else "instagram_digest_weekly")
        rng = random.Random(rng_seed)
        rng.shuffle(selected)
    else:
        selected.sort(key=lambda x: x["viral_score"], reverse=True)

    # Step 6: Assign sequential ranks #01 to #N based on feed order
    for idx, item in enumerate(selected, 1):
        item["rank"] = idx
        item["rank_display"] = f"#{idx:02d}"

    logger.info("Fair-share ranking complete: selected %d reels across %d creators.",
                len(selected), len([c for c, count in creator_counts.items() if count > 0]))

    return selected


def save_digest_batch(ranked_items: list[dict[str, Any]], run_date: str | None = None) -> Path:
    """Save ranked Top 100 digest batch to disk."""
    if not run_date:
        run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "run_date": run_date,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "count": len(ranked_items),
        "items": ranked_items,
    }
    payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
    config.DIGEST_BATCH_FILE.write_text(payload_json, encoding="utf-8")

    # Also archive by week_id for multi-week switching
    if hasattr(config, "DIGESTS_DIR"):
        config.DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
        archive_path = config.DIGESTS_DIR / f"{run_date}.json"
        archive_path.write_text(payload_json, encoding="utf-8")

    logger.info("Saved Top %d digest batch to %s", len(ranked_items), config.DIGEST_BATCH_FILE)
    return config.DIGEST_BATCH_FILE
