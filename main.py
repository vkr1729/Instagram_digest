"""
main.py — Main CLI orchestrator for Instagram Digest v1.0.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
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


def get_last_run_info() -> dict[str, Any] | None:
    """Retrieve timestamp and metadata about the last completed sync run."""
    try:
        if config.LAST_RUN_FILE.exists():
            return json.loads(config.LAST_RUN_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed reading last_run.json: %s", exc)
    return None


def save_last_run_info(week_id: str, timestamp: float | None = None) -> dict[str, Any]:
    """Persist the timestamp and week_id of a successful sync run."""
    ts = timestamp if timestamp is not None else time.time()
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    info = {
        "timestamp": ts,
        "last_run_utc": dt_utc,
        "week_id": week_id,
    }
    try:
        import atomic_io
        atomic_io.durable_write_json(config.LAST_RUN_FILE, info)
        logger.info("Saved last run info: %s (%s)", dt_utc, week_id)
    except Exception as exc:
        logger.warning("Failed saving last_run.json: %s", exc)
    return info


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
    since_timestamp: int | None = None,
) -> int:
    """Execute complete end-to-end extraction, ranking, upload, and deployment pipeline."""
    week_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if since_timestamp is not None:
        logger.info("Starting Instagram Digest ad-hoc sync for week %s (since_ts=%d, days_back=%d, dry_run=%s)...",
                    week_id, since_timestamp, days_back, dry_run)
    else:
        logger.info("Starting Instagram Digest weekly sync for week %s (days_back=%d, dry_run=%s)...",
                    week_id, days_back, dry_run)

    # 1. Pre-flight cleanup & quota check on Cloudflare R2
    if not dry_run and config.R2_ACCOUNT_ID:
        storage_r2.purge_expired_r2_objects(max_age_days=config.RETENTION_DAYS)
        storage_r2.purge_unreferenced_r2_videos()
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
                    extractor.human_pause(mu=2.0, sigma=0.7, floor=0.8)
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
                import atomic_io
                atomic_io.durable_write_json(candidates_cache_file, candidates)
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

        # Enrich shortlist with real metadata and filter by cutoff date.
        # Capped at 2 concurrent browsers drawn from a session pool (was: 6
        # workers each spawning a fresh browser per reel). Sessions are
        # checked out exclusively, so a page is never shared across threads.
        cutoff_ts = since_timestamp if since_timestamp is not None else int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
        enriched: list[dict[str, Any]] = []
        logger.info("Enriching shortlist of %d reels with real metadata (cutoff_ts=%s, max_workers=2)...", len(shortlist), cutoff_ts)

        import queue as _queue
        _session_pool: _queue.Queue = _queue.Queue()
        _pool_sessions = [extractor.InstagramSession() for _ in range(2)]
        for _s in _pool_sessions:
            _s.start()
            _session_pool.put(_s)

        def _enrich_item(r: dict[str, Any]) -> dict[str, Any] | None:
            sess = _session_pool.get()
            try:
                extractor.human_pause(mu=1.6, sigma=0.6, floor=0.7)
                m = extractor.extract_single_reel_metadata(r, session=sess)
                if not m or (m.get("timestamp") or 0) < cutoff_ts:
                    return None
                return m
            finally:
                _session_pool.put(sess)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(_enrich_item, r) for r in shortlist]
                for f in as_completed(futures):
                    try:
                        res = f.result()
                        if res:
                            enriched.append(res)
                    except Exception as exc:
                        logger.debug("Enrichment error: %s", exc)
        finally:
            for _s in _pool_sessions:
                try:
                    _s.close()
                except Exception:
                    pass

        logger.info("Enriched %d valid reels within date window out of %d candidates.", len(enriched), len(shortlist))

        # Pass 2: Final ranking on enriched candidates only (no fallback:
        # ranking the un-enriched shortlist would reintroduce stale/undated reels)
        ranked_reels = ranker.rank_top_reels(
            candidates=enriched,
            sources=active_sources,
            top_n=config.TOP_DIGEST_COUNT,
            max_per_creator=config.MAX_PER_CREATOR,
        )

        # Pass 3: External Reels Discovery to fill remaining quota up to TOP_DIGEST_COUNT
        deficit = config.TOP_DIGEST_COUNT - len(ranked_reels)
        if deficit > 0 and not dry_run:
            logger.info(
                "Followed channels produced %d reels (%d below target %d). Discovering external high-signal reels...",
                len(ranked_reels), deficit, config.TOP_DIGEST_COUNT
            )
            try:
                existing_ids = {r["id"] for r in ranked_reels}
                external_reels = extractor.extract_external_reels_from_feed(
                    session=session,
                    target_count=deficit,
                    existing_ids=existing_ids,
                    active_sources=active_sources,
                )
                if external_reels:
                    logger.info("Discovered %d external high-signal reels from feed.", len(external_reels))
                    combined = ranked_reels + external_reels
                    for idx, r in enumerate(combined, 1):
                        r["rank"] = idx
                        r["rank_display"] = f"#{idx:02d}"
                    ranked_reels = combined
            except extractor.CookieExpiredException as exc:
                logger.warning("Cookie expired during external discovery: %s", exc)
                try:
                    import notifier
                    notifier.send_cookie_alert_email()
                except Exception as alert_err:
                    logger.warning("Failed to send cookie alert email: %s", alert_err)
            except Exception as exc:
                logger.warning("External reels discovery failed: %s", exc)

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

        if deploy and len(ranked_reels) < MIN_DEPLOY_ITEMS:
            logger.error(
                "Only %d playable reels (minimum %d required); refusing to deploy over previous digest.",
                len(ranked_reels), MIN_DEPLOY_ITEMS
            )
            return 2

        # 6. Save digest batch payload (only playable reels saved!)
        ranker.save_digest_batch(ranked_reels, run_date=week_id)

        # 7. Execute 14-Day Rolling Purge (both R2 and local disk)
        storage_r2.purge_expired_r2_objects(max_age_days=config.RETENTION_DAYS)
        storage_r2.purge_unreferenced_r2_videos()
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
        site_builder.deploy_to_gh_pages()

    if not dry_run:
        save_last_run_info(week_id)
        # 10. Send notification email confirming weekly refresh
        try:
            import notifier
            ext_cnt = sum(1 for r in ranked_reels if r.get("is_external"))
            fol_cnt = len(ranked_reels) - ext_cnt
            notifier.send_digest_email(
                week_id=week_id,
                count=len(ranked_reels),
                followed_count=fol_cnt,
                external_count=ext_cnt,
                top_reels=ranked_reels[:5],
                site_url=config.PAGES_BASE_URL if deploy else None,
            )
        except Exception as exc:
            logger.warning("Failed to send refresh confirmation email: %s", exc)

    logger.info("Sync completed successfully! Local viewer ready at %s", local_index)
    return 0


def run_expand(target_count: int = 100, deploy: bool = False) -> int:
    """
    Expand active digest by discovering N extra reels from the Reels feed.
    Preserves all existing active reels in data/top100_digest.json and R2.
    Downloads and uploads ONLY the new reels, re-ranks, rebuilds site, and deploys if requested.
    """
    week_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("Starting +%d reel expansion for week %s (deploy=%s)...", target_count, week_id, deploy)

    if not config.DIGEST_BATCH_FILE.exists():
        logger.error("No active digest found (%s). Run full sync first.", config.DIGEST_BATCH_FILE)
        return 1

    try:
        digest_data = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed loading active digest: %s", exc)
        return 1

    existing_items: list[dict[str, Any]] = digest_data.get("items", [])
    if not existing_items:
        logger.error("Active digest has 0 items. Run full sync first.")
        return 1

    existing_ids = {item["id"] for item in existing_items if "id" in item}
    logger.info("Preserving %d existing reels from active digest without deletion.", len(existing_items))

    # 1. Extract external reels from Reels feed
    with extractor.InstagramSession() as session:
        sources = extractor.load_sources()
        active_sources = [s for s in sources if s.get("enabled", True)]
        try:
            external_reels = extractor.extract_external_reels_from_feed(
                session=session,
                target_count=target_count,
                existing_ids=existing_ids,
                active_sources=active_sources,
            )
        except extractor.CookieExpiredException as exc:
            logger.warning("Cookie expired during external discovery: %s", exc)
            try:
                import notifier
                notifier.send_cookie_alert_email()
            except Exception as alert_err:
                logger.warning("Failed to send cookie alert email: %s", alert_err)
            return 2
        except Exception as exc:
            logger.error("Failed discovering external reels: %s", exc)
            return 2

    if not external_reels:
        logger.warning("No new external reels could be extracted from feed.")
        return 2

    logger.info("Discovered %d new external reels from feed.", len(external_reels))

    # 2. Download and upload ONLY the newly discovered reels.
    # Append-only invariance: existing items keep their ranks AND their R2
    # keys forever. New reels are downloaded to rank-stable temp names first;
    # final ranks (and hence R2 keys) are assigned only after filtering out
    # failed downloads, so keys always match the digest manifest.
    week_videos_dir = config.VIDEOS_DIR / week_id
    week_videos_dir.mkdir(parents=True, exist_ok=True)
    for stale in week_videos_dir.glob("_pending_*.mp4"):
        try:
            stale.unlink()
        except OSError:
            pass
    existing_r2_keys = storage_r2.get_existing_r2_keys(f"videos/{week_id}/")

    uploaded_url_map: dict[str, str] = {}
    for item in existing_items:
        rid = item.get("id")
        if rid:
            r2_url = item.get("r2_url") or item.get("video_url") or f"{config.R2_PUBLIC_DOMAIN}/videos/{week_id}/{item.get('rank', 1):02d}_{item.get('creator_handle')}_{rid}.mp4"
            uploaded_url_map[rid] = r2_url

    def _pending_path(reel: dict[str, Any]) -> Path:
        return week_videos_dir / f"_pending_{reel['creator_handle']}_{reel['id']}.mp4"

    def download_new_reel(reel: dict[str, Any]) -> dict[str, Any] | None:
        tmp_path = _pending_path(reel)
        if not (tmp_path.exists() and tmp_path.stat().st_size > 0):
            success = extractor.download_reel_video(
                reel["url"],
                tmp_path,
                video_cdn_url=reel.get("video_cdn_url"),
            )
            if not success:
                logger.warning("Skipping failed download for expanded reel %s", reel["id"])
                return None
        return reel

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(download_new_reel, r) for r in external_reels]
        downloaded: list[dict[str, Any]] = []
        for f in as_completed(futures):
            try:
                res = f.result()
                if res is not None:
                    downloaded.append(res)
            except Exception as exc:
                logger.warning("Error downloading expanded reel: %s", exc)

    # Restore discovery order, then assign contiguous ranks after existing.
    order = {id(r): i for i, r in enumerate(external_reels)}
    downloaded.sort(key=lambda r: order.get(id(r), 0))
    for offset, reel in enumerate(downloaded):
        rank_num = len(existing_items) + 1 + offset
        reel["rank"] = rank_num
        reel["rank_display"] = f"#{rank_num:02d}"
        filename = f"{rank_num:02d}_{reel['creator_handle']}_{reel['id']}.mp4"
        final_path = week_videos_dir / filename
        tmp_path = _pending_path(reel)
        try:
            if final_path.exists() and final_path.stat().st_size > 0:
                tmp_path.unlink(missing_ok=True)
            else:
                tmp_path.rename(final_path)
        except OSError as exc:
            logger.warning("Skipping expanded reel %s (rename failed): %s", reel["id"], exc)
            reel["rank"] = 0

    downloadable = [r for r in downloaded if r.get("rank")]
    # Re-compact ranks in case a rename failed above (keeps numbering gapless).
    for offset, reel in enumerate(downloadable):
        rank_num = len(existing_items) + 1 + offset
        if reel["rank"] != rank_num:
            old = week_videos_dir / f"{reel['rank']:02d}_{reel['creator_handle']}_{reel['id']}.mp4"
            new = week_videos_dir / f"{rank_num:02d}_{reel['creator_handle']}_{reel['id']}.mp4"
            try:
                old.rename(new)
            except OSError:
                pass
            reel["rank"] = rank_num
            reel["rank_display"] = f"#{rank_num:02d}"

    def upload_new_reel(reel: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        filename = f"{reel['rank']:02d}_{reel['creator_handle']}_{reel['id']}.mp4"
        public_url = storage_r2.upload_reel_to_r2(
            week_videos_dir / filename,
            week_id=week_id,
            key_name=filename,
            existing_keys=existing_r2_keys,
        )
        return (reel["id"], public_url, reel)

    new_ranked: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(upload_new_reel, r) for r in downloadable]
        for f in as_completed(futures):
            try:
                rid, pub_url, reel_obj = f.result()
                if pub_url:
                    uploaded_url_map[rid] = pub_url
                    reel_obj["r2_url"] = pub_url
                    reel_obj["video_url"] = pub_url
                    new_ranked.append(reel_obj)
                else:
                    logger.warning("Skipping unplayable expanded reel %s", rid)
            except Exception as exc:
                logger.warning("Error uploading expanded reel: %s", exc)

    new_ranked.sort(key=lambda r: r.get("rank", 9999))
    combined_items = existing_items + new_ranked

    logger.info("Expansion successfully integrated: %d existing + %d new = %d total reels.",
                len(existing_items), len(new_ranked), len(combined_items))

    # 3. Save combined digest payload
    ranker.save_digest_batch(combined_items, run_date=week_id)

    # 4. Rebuild static site
    site_builder.build_site(
        digest_data={"run_date": week_id, "items": combined_items},
        r2_uploaded_urls=uploaded_url_map,
    )

    # 5. Deploy to GitHub Pages if requested
    if deploy:
        site_builder.deploy_to_gh_pages()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Instagram Digest v1.0 — Weekly High-Signal Reel Curator")
    parser.add_argument("--sync", action="store_true", help="Run full weekly extraction, ranking, and sync")
    parser.add_argument("--ad-hoc", action="store_true", help="Run ad-hoc midweek sync picking reels between now and the last run timestamp")
    parser.add_argument("--expand", type=int, default=0, help="Expand active digest with N new external reels from Reels feed")
    parser.add_argument("--serve", action="store_true", help="Run local dashboard HTTP server on port 8080")
    parser.add_argument("--port", type=int, default=8080, help="Port for local server (default: 8080)")
    parser.add_argument("--sync-following", action="store_true", help="Force sync followed creators from Chrome session")
    parser.add_argument("--build-only", action="store_true", help="Compile static site using existing digest data")
    parser.add_argument("--deploy", action="store_true", help="Deploy compiled site to GitHub Pages")
    parser.add_argument("--dry-run", action="store_true", help="Simulate pipeline without downloading or uploading videos")
    parser.add_argument("--limit-per-creator", type=int, default=15, help="Max candidate reels per creator (default: 15)")
    parser.add_argument("--days-back", type=int, default=7, help="Candidate publication window in days (default: 7)")
    args = parser.parse_args()

    # Expand mode
    if args.expand > 0:
        return run_expand(target_count=args.expand, deploy=args.deploy)

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
    if args.deploy and not args.sync and not args.ad_hoc:
        if _digest_item_count() < MIN_DEPLOY_ITEMS:
            logger.error(
                "Digest has fewer than %d items; refusing to deploy over the previous digest.",
                MIN_DEPLOY_ITEMS,
            )
            return 2
        site_builder.deploy_to_gh_pages()
        return 0

    days_back = args.days_back
    since_ts = None
    last_run = get_last_run_info()
    if args.ad_hoc or (last_run and "timestamp" in last_run and args.days_back == 7):
        if last_run and "timestamp" in last_run:
            elapsed = time.time() - last_run["timestamp"]
            if 3600 <= elapsed <= 7 * 86400:
                since_ts = int(last_run["timestamp"])
                days_back = max(1, int(round(elapsed / 86400.0)))
                logger.info("Anchor to last run: picking reels between %s (~%d days ago) and now",
                            last_run.get("last_run_utc"), days_back)
        elif args.ad_hoc:
            logger.info("Ad-hoc run: no previous run timestamp stored; defaulting to %d days back", days_back)

    # Default to running full sync (or when --sync or --ad-hoc is specified)
    return run_full_sync(
        dry_run=args.dry_run,
        deploy=args.deploy,
        days_back=days_back,
        limit_per_creator=args.limit_per_creator,
        since_timestamp=since_ts,
    )


if __name__ == "__main__":
    sys.exit(main())
