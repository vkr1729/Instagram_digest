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
    Compute creator-normalized viral score.
    Combines ratio to creator baseline with engagement multiplier (likes/comments).
    """
    views = max(1, reel.get("view_count", 0))
    likes = reel.get("like_count", 0)
    comments = reel.get("comment_count", 0)

    # Relative reach multiplier (e.g. 2.5x normal views)
    reach_multiplier = views / max(100.0, baseline_views)

    # Engagement rate: (likes + 2*comments) / views
    engagement_rate = (likes + (comments * 2.0)) / max(10.0, float(views))

    # Composite viral score
    score = reach_multiplier * (1.0 + (engagement_rate * 5.0))
    return round(score, 3)


def rank_top_reels(
    candidates: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    top_n: int = config.TOP_DIGEST_COUNT,
    max_per_creator: int = config.MAX_PER_CREATOR,
) -> list[dict[str, Any]]:
    """
    Execute Fair-Share Ranking:
    1. Calculate baseline per creator.
    2. Compute viral score for every candidate.
    3. Guarantee at least 1 top reel for every active creator.
    4. Cap maximum reels per creator (e.g. max 4).
    5. Fill remaining slots with highest scoring outliers up to top_n.
    6. Sort final pool descending by score and assign ranks #01 to #N.
    """
    if not candidates:
        logger.warning("No candidate reels provided to ranker.")
        return []

    # Map sources by handle for category and display name lookup
    sources_by_handle = {s["handle"].lower(): s for s in sources if "handle" in s}

    # Group candidate reels by creator
    by_creator: dict[str, list[dict[str, Any]]] = {}
    for r in candidates:
        h = r["creator_handle"].lower()
        by_creator.setdefault(h, []).append(r)

    # Calculate baselines and assign scores
    scored_pool: list[dict[str, Any]] = []
    for handle, creator_reels in by_creator.items():
        baseline = calculate_creator_baseline(creator_reels)
        src = sources_by_handle.get(handle, {})
        creator_name = src.get("name") or handle
        category = src.get("category") or "tech"

        for reel in creator_reels:
            score = compute_viral_score(reel, baseline)
            item = dict(reel)
            item["creator_name"] = creator_name
            item["category"] = category
            item["viral_score"] = score
            scored_pool.append(item)

    # Sort each creator's reels descending by score
    creator_queues: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(scored_pool, key=lambda x: x["viral_score"], reverse=True):
        creator_queues.setdefault(item["creator_handle"].lower(), []).append(item)

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

    # Step 2: Pool remaining candidate reels across all creators
    remaining_pool: list[dict[str, Any]] = []
    for q in creator_queues.values():
        remaining_pool.extend(q)

    # Sort remaining candidates by score descending
    remaining_pool.sort(key=lambda x: x["viral_score"], reverse=True)

    # Step 3: Fill up to top_n respecting max_per_creator cap
    for item in remaining_pool:
        if len(selected) >= top_n:
            break
        h = item["creator_handle"].lower()
        if creator_counts[h] < max_per_creator and item["id"] not in used_ids:
            selected.append(item)
            creator_counts[h] += 1
            used_ids.add(item["id"])

    # Step 4: Final global sort by viral_score descending and assign ranks
    selected.sort(key=lambda x: x["viral_score"], reverse=True)
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
    config.DIGEST_BATCH_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Saved Top %d digest batch to %s", len(ranked_items), config.DIGEST_BATCH_FILE)
    return config.DIGEST_BATCH_FILE
