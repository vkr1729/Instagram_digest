"""
main.py — Main CLI orchestrator for Instagram Digest v1.0.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
import extractor
import local_server
import ranker
import site_builder
import storage_r2

logger = logging.getLogger("InstagramDigest.Main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MIN_CANDIDATE_RATIO = 0.5      # fraction of the expected candidate count
MAX_EMPTY_CREATOR_RATIO = 0.6  # fraction of creators that returned 0 reels
MIN_DEPLOY_ITEMS = int(config.TOP_DIGEST_COUNT * 0.6)


def _digest_item_count() -> int:
    """Return the item count of the persisted digest batch (0 when missing/unreadable)."""
    try:
        payload = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
        items = payload.get("items", [])
        return len(items) if isinstance(items, list) else 0
    except Exception:
        return 0


def run_full_sync(
    dry_run: bool = False,
    deploy: bool = False,
    days_back: int = 7,
    limit_per_creator: int = 15,
) -> int:
    """Execute complete end-to-end extraction, ranking, upload, and deployment pipeline."""
    week_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("Starting Instagram Digest weekly sync for week %s (dry_run=%s)...", week_id, dry_run)

    # 1. Pre-flight quota check on Cloudflare R2
    if not dry_run and config.R2_ACCOUNT_ID:
        if not storage_r2.check_preflight_quota():
            logger.error("Pre-flight quota check failed. Aborting to protect Cloudflare free limits.")
            return 1

    # 2. Load tracked and curated creators
    sources = extractor.load_sources()
    active_sources = [s for s in sources if s.get("enabled", True)]
    logger.info("Processing %d active creators from sources.json.", len(active_sources))

    if not active_sources:
        logger.warning("No active sources found. Add creators to sources.json or run --sync-following.")
        return 1

    # 3. Extract candidate reels across active creators (balanced across categories)
    candidates: list[dict[str, Any]] = []

    # Organize creators by category for balanced round-robin discovery
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for src in active_sources:
        cat = src.get("category", "entertainment")
        by_cat.setdefault(cat, []).append(src)

    ordered_sources: list[dict[str, Any]] = []
    cats = ["entertainment", "finance", "ai_tech", "niche", "health", "food"]
    max_len = max((len(by_cat.get(c, [])) for c in cats), default=0)
    for i in range(max_len):
        for c in cats:
            if i < len(by_cat.get(c, [])):
                ordered_sources.append(by_cat[c][i])

    ranked_reels: list[dict[str, Any]] = []
    with extractor.InstagramSession() as session:
        candidates_cache_file = config.DATA_DIR / "candidates_cache.json"
        if candidates_cache_file.exists():
            try:
                if time.time() - candidates_cache_file.stat().st_mtime < 12 * 3600:
                    candidates = json.loads(candidates_cache_file.read_text(encoding="utf-8"))
                    logger.info("Loaded %d candidate reels from fresh candidates_cache.json.", len(candidates))
            except Exception:
                candidates = []

        if not candidates:
            expected = 0
            empty_creators = 0
            empty_streak = 0
            try:
                # Ensure candidate gathering covers all active creators so all category quotas can be fulfilled
                for idx, src in enumerate(ordered_sources, 1):
                    handle = src.get("handle", "")
                    if not handle:
                        continue
                    cat = src.get("category", "")
                    max_candidate_reels = 6 if cat == "food" else min(limit_per_creator, 5)
                    expected += max_candidate_reels
                    logger.info("[%d/%d] Extracting candidate reels for @%s (%s)...", idx, len(ordered_sources), handle, cat)
                    reels = extractor.extract_creator_reels(
                        handle=handle,
                        max_reels=max_candidate_reels,
                        days_back=days_back,
                        fast_mode=True,  # Fast discovery from reels tab
                        session=session,
                    )
                    if not reels:
                        empty_creators += 1
                        empty_streak += 1
                        if empty_streak >= 2:
                            pause = min(120, 10 * 2 ** (empty_streak - 2))
                            logger.warning("Two empty creators in a row; backing off %ds.", pause)
                            time.sleep(pause)
                    else:
                        empty_streak = 0

                    candidates.extend(reels)
                    time.sleep(random.uniform(1.2, 2.8))
            except extractor.InstagramBlocked as exc:
                logger.error("Instagram blocked the session (%s). Aborting run without touching digest/site.", exc)
                candidates_cache_file.unlink(missing_ok=True)
                return 2

            if (len(candidates) < MIN_CANDIDATE_RATIO * expected
                    or empty_creators > MAX_EMPTY_CREATOR_RATIO * len(ordered_sources)):
                logger.error(
                    "Viability gate failed: %d candidates (expected >=%d), %d/%d creators empty. Aborting.",
                    len(candidates), int(MIN_CANDIDATE_RATIO * expected), empty_creators, len(ordered_sources)
                )
                candidates_cache_file.unlink(missing_ok=True)
                return 2

            logger.info("Extracted total %d candidate reels across creators.", len(candidates))
            try:
                candidates_cache_file.write_text(json.dumps(candidates, indent=2), encoding="utf-8")
            except Exception:
                pass

        # 4. Two-Pass Selection & Ranking (C3):
        # Pass 1: Cheap reach-only ranking to shortlist (2x top digest size)
        shortlist = ranker.rank_top_reels(
            candidates=candidates,
            sources=active_sources,
            top_n=config.TOP_DIGEST_COUNT * 2,
            max_per_creator=config.MAX_PER_CREATOR + 2,
            shuffle=False,
        )

        # Enrich shortlist with real metadata and filter by cutoff date
        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
        enriched: list[dict[str, Any]] = []
        logger.info("Enriching shortlist of %d reels with real metadata...", len(shortlist))
        for reel in shortlist:
            meta = extractor.extract_single_reel_metadata(reel, session=session)
            if not meta or (meta.get("timestamp") or 0) < cutoff_ts:
                logger.info(
                    "Skipping reel %s: too old, pinned, or unknown date (%s < %s)",
                    (meta or {}).get("id"), (meta or {}).get("timestamp"), cutoff_ts,
                )
                continue
            enriched.append(meta)

        # Pass 2: Final ranking on enriched candidates only (no fallback:
        # ranking the un-enriched shortlist would reintroduce stale/undated reels)
        ranked_reels = ranker.rank_top_reels(
            candidates=enriched,
            sources=active_sources,
            top_n=config.TOP_DIGEST_COUNT,
            max_per_creator=config.MAX_PER_CREATOR,
        )

    if not ranked_reels:
        logger.error("No reels qualified for Top Digest. Aborting run without touching digest/site.")
        return 2

    # 5. Media Download and R2 Upload (Multi-threaded B1, C4 closed browser session)
    uploaded_url_map: dict[str, str] = {}
    if not dry_run and ranked_reels:
        week_videos_dir = config.VIDEOS_DIR / week_id
        week_videos_dir.mkdir(parents=True, exist_ok=True)

        existing_r2_keys = storage_r2.get_existing_r2_keys(f"videos/{week_id}/")

        logger.info("Downloading and syncing Top %d reels with worker pool...", len(ranked_reels))

        def process_reel(reel: dict[str, Any]) -> tuple[str, str]:
            reel_id = reel["id"]
            handle = reel["creator_handle"]
            rank = reel.get("rank", 1)
            filename = f"{rank:02d}_{handle}_{reel_id}.mp4"
            local_video_path = week_videos_dir / filename

            # Check if this reel was already downloaded under a previous rank prefix
            existing_matches = list(week_videos_dir.glob(f"*_{handle}_{reel_id}.mp4")) or list(week_videos_dir.glob(f"*_{reel_id}.mp4"))
            if existing_matches:
                matched_file = existing_matches[0]
                if matched_file.resolve() != local_video_path.resolve():
                    try:
                        matched_file.rename(local_video_path)
                    except Exception:
                        pass

            # Download if not already cached
            if not local_video_path.exists():
                logger.info("Downloading reel [%s] #%02d @%s: %s", reel_id, rank, handle, reel["url"])
                success = extractor.download_reel_video(
                    reel["url"],
                    local_video_path,
                    video_cdn_url=reel.get("video_cdn_url"),
                )
                if not success:
                    logger.warning("Skipping upload for failed download %s", reel_id)
                    return (reel_id, "")

            # Upload to Cloudflare R2 (or fallback to local if R2 not configured)
            public_url = storage_r2.upload_reel_to_r2(
                local_video_path,
                week_id=week_id,
                key_name=filename,
                existing_keys=existing_r2_keys,
            )
            return (reel_id, public_url)

        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_id = {executor.submit(process_reel, r): r["id"] for r in ranked_reels}
            for future in as_completed(future_to_id):
                try:
                    rid, url = future.result()
                    if url:
                        uploaded_url_map[rid] = url
                except Exception as exc:
                    rid = future_to_id[future]
                    logger.warning("Worker error processing reel %s: %s", rid, exc)

        # Drop unplayable reels (C2)
        dropped = [r["id"] for r in ranked_reels if r["id"] not in uploaded_url_map]
        if dropped:
            logger.warning("Dropping %d unplayable reels: %s", len(dropped), ", ".join(dropped))
            ranked_reels = [r for r in ranked_reels if r["id"] in uploaded_url_map]

        # 6. Save digest batch payload (only playable reels saved!)
        ranker.save_digest_batch(ranked_reels, run_date=week_id)

        # 7. Execute 14-Day Rolling Purge (both R2 and local disk)
        storage_r2.purge_expired_r2_objects(max_age_days=config.RETENTION_DAYS)
        storage_r2.purge_expired_local_videos(max_age_days=config.RETENTION_DAYS)
    else:
        # Dry-run: save ranked reels
        ranker.save_digest_batch(ranked_reels, run_date=week_id)

    # 8. Compile Variant 1A Static Viewer Site
    r2_index, local_index = site_builder.build_site(
        digest_data={"run_date": week_id, "items": ranked_reels},
        r2_uploaded_urls=uploaded_url_map if not dry_run else None,
    )

    # 9. Deploy to GitHub Pages (Viability & Minimum items gate)
    if deploy and not dry_run:
        if len(ranked_reels) < MIN_DEPLOY_ITEMS:
            logger.error(
                "Only %d playable reels (minimum %d required); refusing to deploy over previous digest.",
                len(ranked_reels), MIN_DEPLOY_ITEMS
            )
            return 2
        site_builder.deploy_to_gh_pages()

    logger.info("Weekly sync completed successfully! Local viewer ready at %s", local_index)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Instagram Digest v1.0 — Weekly High-Signal Reel Curator")
    parser.add_argument("--sync", action="store_true", help="Run full weekly extraction, ranking, and sync")
    parser.add_argument("--serve", action="store_true", help="Run local dashboard HTTP server on port 8080")
    parser.add_argument("--port", type=int, default=8080, help="Port for local server (default: 8080)")
    parser.add_argument("--sync-following", action="store_true", help="Force sync followed creators from Chrome session")
    parser.add_argument("--build-only", action="store_true", help="Compile static site using existing digest data")
    parser.add_argument("--deploy", action="store_true", help="Deploy compiled site to GitHub Pages")
    parser.add_argument("--dry-run", action="store_true", help="Simulate pipeline without downloading or uploading videos")
    parser.add_argument("--limit-per-creator", type=int, default=15, help="Max candidate reels per creator (default: 15)")
    parser.add_argument("--days-back", type=int, default=7, help="Candidate publication window in days (default: 7)")
    args = parser.parse_args()

    # Serve mode
    if args.serve:
        local_server.run_local_server(port=args.port)
        return 0

    # Sync following on-demand
    if args.sync_following:
        extractor.sync_following_accounts(force=True)
        return 0

    # Build only mode
    if args.build_only:
        site_builder.build_site()
        if args.deploy:
            if _digest_item_count() < MIN_DEPLOY_ITEMS:
                logger.error(
                    "Digest has fewer than %d items; refusing to deploy over the previous digest.",
                    MIN_DEPLOY_ITEMS,
                )
                return 2
            site_builder.deploy_to_gh_pages()
        return 0

    # Deploy only mode
    if args.deploy and not args.sync:
        if _digest_item_count() < MIN_DEPLOY_ITEMS:
            logger.error(
                "Digest has fewer than %d items; refusing to deploy over the previous digest.",
                MIN_DEPLOY_ITEMS,
            )
            return 2
        site_builder.deploy_to_gh_pages()
        return 0

    # Default to running full sync (or when --sync is specified)
    return run_full_sync(
        dry_run=args.dry_run,
        deploy=args.deploy,
        days_back=args.days_back,
        limit_per_creator=args.limit_per_creator,
    )


if __name__ == "__main__":
    sys.exit(main())
