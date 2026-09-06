"""
main.py — Main CLI orchestrator for Instagram Digest v1.0.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
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

    # 2. Sync / refresh followed accounts
    sources = extractor.sync_following_accounts(force=False)
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
        cat = src.get("category", "culture")
        by_cat.setdefault(cat, []).append(src)

    ordered_sources: list[dict[str, Any]] = []
    cats = ["tech", "health", "explainer", "culture"]
    max_len = max((len(by_cat.get(c, [])) for c in cats), default=0)
    for i in range(max_len):
        for c in cats:
            if i < len(by_cat.get(c, [])):
                ordered_sources.append(by_cat[c][i])

    target_candidate_count = max(config.TOP_DIGEST_COUNT + 35, 135)
    with extractor.InstagramSession() as session:
        for src in ordered_sources:
            if len(candidates) >= target_candidate_count:
                break
            handle = src.get("handle", "")
            if not handle:
                continue
            reels = extractor.extract_creator_reels(
                handle=handle,
                max_reels=min(limit_per_creator, 5),
                days_back=days_back,
                fast_mode=True,  # Fast discovery from reels tab
                session=session,
            )
            candidates.extend(reels)
            time.sleep(0.2)

        logger.info("Extracted total %d candidate reels across creators.", len(candidates))

        # 4. Rank candidates using Fair-Share Viral Multiplier
        ranked_reels = ranker.rank_top_reels(
            candidates=candidates,
            sources=active_sources,
            top_n=config.TOP_DIGEST_COUNT,
            max_per_creator=config.MAX_PER_CREATOR,
        )

        if not ranked_reels:
            logger.warning("No reels qualified for Top Digest.")
            if candidates:
                ranked_reels = candidates[:config.TOP_DIGEST_COUNT]

        # Save digest batch payload
        ranker.save_digest_batch(ranked_reels, run_date=week_id)

        # 5. Media Download and R2 Upload
        uploaded_url_map: dict[str, str] = {}
        if not dry_run and ranked_reels:
            week_videos_dir = config.VIDEOS_DIR / week_id
            week_videos_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Downloading and syncing Top %d reels...", len(ranked_reels))
            for reel in ranked_reels:
                reel_id = reel["id"]
                handle = reel["creator_handle"]
                rank = reel.get("rank", 1)
                filename = f"{rank:02d}_{handle}_{reel_id}.mp4"
                local_video_path = week_videos_dir / filename

                # Download if not already cached
                if not local_video_path.exists():
                    logger.info("Downloading reel [%s] #%02d @%s: %s", reel_id, rank, handle, reel["url"])
                    # Enrich metadata if caption is generic
                    if reel.get("caption", "").startswith("Reel by @"):
                        meta = extractor.extract_single_reel_metadata(reel, session=session)
                        if meta:
                            for mk, mv in meta.items():
                                if mv and mk not in ("rank", "rank_display", "viral_score"):
                                    reel[mk] = mv

                    success = extractor.download_reel_video(
                        reel["url"],
                        local_video_path,
                        video_cdn_url=reel.get("video_cdn_url"),
                        session=session,
                    )
                    if not success:
                        logger.warning("Skipping upload for failed download %s", reel_id)
                        continue
                    time.sleep(0.3)

                # Upload to Cloudflare R2 (or fallback to local)
                public_url = storage_r2.upload_reel_to_r2(local_video_path, week_id=week_id, key_name=filename)
                uploaded_url_map[reel_id] = public_url

            # 6. Execute 14-Day Rolling Purge (both R2 and local disk)
            storage_r2.purge_expired_r2_objects(max_age_days=config.RETENTION_DAYS)
            storage_r2.purge_expired_local_videos(max_age_days=config.RETENTION_DAYS)

    # 7. Compile Variant 1A Static Viewer Site
    r2_index, local_index = site_builder.build_site(
        digest_data={"run_date": week_id, "items": ranked_reels},
        r2_uploaded_urls=uploaded_url_map,
    )

    # 8. Deploy to GitHub Pages
    if deploy and not dry_run:
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
            site_builder.deploy_to_gh_pages()
        return 0

    # Deploy only mode
    if args.deploy and not args.sync:
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
