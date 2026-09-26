#!/usr/bin/env python3
"""
scripts/topup_digest.py — Splice out watched reels and top up with new reels.

1. Deletes local MP4s for the first 136 watched reels from videos/2026-09-11/.
2. Preserves the remaining 164 unwatched reels from 2026-09-11 and copies their MP4s into videos/2026-09-14/ as ranks 1..164.
3. Selects the top 136 unseen reels from data/candidates_cache.json using fair-share viral multiplier ranking.
4. Enriches metadata and downloads MP4s for the 136 new reels into videos/2026-09-14/ as ranks 165..300.
5. Uploads all 300 videos to Cloudflare R2 under videos/2026-09-14/.
6. Combines the 164 kept + 136 new into an exact 300-reel digest.
7. Saves data/top100_digest.json and data/digests/2026-09-14.json.
8. Updates data/last_run.json to today (2026-09-14).
9. Builds static PWA via site_builder.build_site(week_id="2026-09-14").
10. Deploys to GitHub Pages via site_builder.deploy_to_gh_pages().
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import atomic_io
import config
import extractor
import main
import ranker
import site_builder
import storage_r2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("InstagramDigest.TopUp")


def topup_digest(watched_count: int = 136, new_week_id: str = "2026-09-14", deploy: bool = True) -> int:
    try:
        with main._pipeline_file_lock():
            return _topup_digest(watched_count, new_week_id, deploy)
    except main.PipelineBusy as exc:
        logger.error("%s; refusing to start.", exc)
        return 3


def _topup_digest(watched_count: int = 136, new_week_id: str = "2026-09-14", deploy: bool = True) -> int:
    # 1. Load active digest (quarantine corrupt bytes for forensics, PY-P1-4)
    if not config.DIGEST_BATCH_FILE.exists():
        logger.error("Active digest %s does not exist.", config.DIGEST_BATCH_FILE)
        return 1

    try:
        digest_data = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        main._quarantine_corrupt(config.DIGEST_BATCH_FILE, exc)
        logger.error("Active digest %s is corrupt; quarantined, refusing to continue.", config.DIGEST_BATCH_FILE)
        return 1
    existing_items: list[dict[str, Any]] = digest_data.get("items", [])
    if len(existing_items) < watched_count:
        logger.error("Active digest has %d items, fewer than watched_count %d.", len(existing_items), watched_count)
        return 1

    old_week_id = digest_data.get("run_date") or "2026-09-11"
    old_video_dir = config.VIDEOS_DIR / old_week_id
    new_video_dir = config.VIDEOS_DIR / new_week_id
    new_video_dir.mkdir(parents=True, exist_ok=True)

    watched_items = existing_items[:watched_count]
    kept_items = existing_items[watched_count:]
    logger.info("Splitting digest: dropping %d watched reels, keeping %d unwatched reels.", len(watched_items), len(kept_items))

    # 2. Delete local video files for the watched reels
    deleted_local = 0
    if old_video_dir.exists():
        for item in watched_items:
            rid = main._safe_component(item.get("id"), "")
            if not rid:
                continue
            for match in old_video_dir.glob(f"*_{rid}.mp4"):
                try:
                    match.unlink(missing_ok=True)
                    deleted_local += 1
                except OSError as exc:
                    logger.warning("Could not delete watched video %s: %s", match.name, exc)
    logger.info("Deleted %d local video files for watched reels to reclaim disk space.", deleted_local)

    # 3. Load dry-run candidate cache
    candidates_cache_file = config.DATA_DIR / "candidates_cache.json"
    if not candidates_cache_file.exists():
        logger.error("Candidates cache %s not found.", candidates_cache_file)
        return 1

    cached = None
    try:
        cached = json.loads(candidates_cache_file.read_text(encoding="utf-8"))
    except Exception as exc:
        main._quarantine_corrupt(candidates_cache_file, exc)
        logger.error("Candidates cache %s is corrupt; quarantined.", candidates_cache_file)
        return 1
    cands = cached.get("candidates", []) if isinstance(cached, dict) else []
    logger.info("Loaded %d candidates from %s.", len(cands), candidates_cache_file.name)

    watched_ids = {i["id"] for i in watched_items if i.get("id")}
    kept_ids = {i["id"] for i in kept_items if i.get("id")}

    # 4. Filter to unseen candidates
    unseen = [c for c in cands if c.get("id") and c["id"] not in watched_ids and c["id"] not in kept_ids]
    logger.info("Found %d unseen candidates not in previous digest.", len(unseen))

    needed_new = config.TOP_DIGEST_COUNT - len(kept_items)
    logger.info("Target: selecting top %d new reels to restore feed to %d reels.", needed_new, config.TOP_DIGEST_COUNT)

    sources = extractor.load_sources()
    active_sources = [s for s in sources if s.get("enabled", True)]

    # 5. Rank unseen candidates to select top needed_new
    ranked_new = ranker.rank_top_reels(
        candidates=unseen,
        sources=active_sources,
        top_n=needed_new,
        max_per_creator=config.MAX_PER_CREATOR + 2,
        shuffle=False,
    )
    if len(ranked_new) < needed_new:
        ranked_ids = {r["id"] for r in ranked_new}
        extra = [c for c in unseen if c.get("id") not in ranked_ids]
        ranked_new.extend(extra[:needed_new - len(ranked_new)])
    logger.info("Selected %d top new candidate reels.", len(ranked_new))

    # PY-P1-8: mirror main.py's cutoff filter — never backfill unranked,
    # undated, or stale reels past the recency window. Ranked items carry
    # real timestamps; raw cache extras with timestamp==0 or older than the
    # cutoff are dropped instead of appended (UAT-2.3).
    cutoff_ts = int(time.time()) - 7 * 86400
    fresh_new = []
    dropped_stale = 0
    for r in ranked_new:
        ts = r.get("timestamp") or 0
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            ts = 0
        if ts and ts >= cutoff_ts:
            fresh_new.append(r)
        else:
            dropped_stale += 1
    if dropped_stale:
        logger.warning("Dropping %d stale/undated backfill reels past the 7-day cutoff.", dropped_stale)
    ranked_new = fresh_new
    logger.info("Selected %d fresh new candidate reels within date window.", len(ranked_new))

    # 6. Migrate kept reels to ranks 1..len(kept_items) in new_week_id
    reindexed_kept: list[dict[str, Any]] = []
    for idx, item in enumerate(kept_items):
        new_rank = idx + 1
        item_copy = dict(item)
        item_copy["rank"] = new_rank
        item_copy["rank_display"] = f"#{new_rank:02d}"
        clean_handle = main._safe_component(item.get("creator_handle"), "creator")
        rid = main._safe_component(item.get("id"), "reel")
        dest_filename = f"{new_rank:02d}_{clean_handle}_{rid}.mp4"
        dest_path = new_video_dir / dest_filename

        # Find existing video in old_week_id
        if not (dest_path.exists() and dest_path.stat().st_size > 0):
            found_src = None
            if old_video_dir.exists():
                matches = list(old_video_dir.glob(f"*_{rid}.mp4"))
                if matches and matches[0].stat().st_size > 0:
                    found_src = matches[0]

            if found_src:
                try:
                    shutil.copy2(found_src, dest_path)
                except OSError:
                    try:
                        os.link(found_src, dest_path)
                    except OSError:
                        pass

        reindexed_kept.append(item_copy)

    # 7. Enrich and download new reels with session
    logger.info("Enriching metadata and downloading videos for %d new reels...", len(ranked_new))
    enriched_new: list[dict[str, Any]] = []

    with extractor.InstagramSession() as session:
        for idx, r in enumerate(ranked_new):
            clean_handle = main._safe_component(r.get("creator_handle"), "creator")
            rid = main._safe_component(r.get("id"), "reel")

            # Same anti-automation pacing as the weekly pipeline: never hammer
            # reel pages back-to-back from the owner's session.
            mu, sigma, floor = main.ENRICH_PAUSE
            extractor.human_pause(mu=mu, sigma=sigma, floor=floor)
            # Enrich metadata
            try:
                meta = extractor.extract_single_reel_metadata(r, session=session)
                if meta:
                    r.update(meta)
            except Exception as exc:
                logger.debug("Enrich metadata error for %s: %s", r.get("id"), exc)

            # Mirror main.py: enriched reels older than the cutoff (or still
            # undated) are dropped, never downloaded or published.
            try:
                enriched_ts = int(r.get("timestamp") or 0)
            except (TypeError, ValueError):
                enriched_ts = 0
            if not enriched_ts or enriched_ts < cutoff_ts:
                logger.warning("Dropping stale/undated reel %s after enrich; skipping download.", r.get("id"))
                continue

            new_rank = len(reindexed_kept) + len(enriched_new) + 1
            r["rank"] = new_rank
            r["rank_display"] = f"#{new_rank:02d}"
            filename = f"{new_rank:02d}_{clean_handle}_{rid}.mp4"
            dest_path = new_video_dir / filename

            # Download video if not already present
            if not (dest_path.exists() and dest_path.stat().st_size > 0):
                success = extractor.download_reel_video(
                    r["url"],
                    dest_path,
                    video_cdn_url=r.get("video_cdn_url"),
                    session=session,
                )
                if not success:
                    logger.warning("Download failed for reel %s (#%02d @%s)", r.get("id"), new_rank, clean_handle)

            enriched_new.append(r)
            if (idx + 1) % 10 == 0 or (idx + 1) == len(ranked_new):
                logger.info("Processed [%d/%d] new reels.", idx + 1, len(ranked_new))

        # Also download any kept reel that was missing on disk
        for idx, item in enumerate(reindexed_kept):
            clean_handle = main._safe_component(item.get("creator_handle"), "creator")
            rid = main._safe_component(item.get("id"), "reel")
            dest_filename = f"{item['rank']:02d}_{clean_handle}_{rid}.mp4"
            dest_path = new_video_dir / dest_filename
            if not (dest_path.exists() and dest_path.stat().st_size > 0):
                logger.info("Downloading missing kept reel [%s] (#%02d @%s)...", rid, item['rank'], clean_handle)
                extractor.download_reel_video(
                    item.get("url") or f"https://www.instagram.com/reel/{rid}/",
                    dest_path,
                    video_cdn_url=item.get("video_cdn_url"),
                    session=session,
                )

    # 8. Upload to Cloudflare R2
    logger.info("Checking and uploading videos to Cloudflare R2 for week %s...", new_week_id)
    existing_r2_keys = storage_r2.get_existing_r2_keys(f"videos/{new_week_id}/")
    all_300 = reindexed_kept + enriched_new

    def _upload_item(reel: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        clean_handle = main._safe_component(reel.get("creator_handle"), "creator")
        rid = main._safe_component(reel.get("id"), "reel")
        filename = f"{reel['rank']:02d}_{clean_handle}_{rid}.mp4"
        local_file = new_video_dir / filename
        public_url = storage_r2.upload_reel_to_r2(
            local_file,
            week_id=new_week_id,
            key_name=filename,
            existing_keys=existing_r2_keys,
        )
        return (reel["id"], public_url, reel)

    uploaded_url_map: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_upload_item, r) for r in all_300]
        uploaded_count = 0
        for f in as_completed(futures):
            try:
                rid, pub_url, reel_obj = f.result()
                if pub_url:
                    reel_obj["r2_url"] = pub_url
                    reel_obj["video_url"] = pub_url
                    uploaded_url_map[rid] = pub_url
                    uploaded_count += 1
            except Exception as exc:
                logger.warning("Upload error: %s", exc)

    logger.info("R2 sync complete: %d/%d reels active.", uploaded_count, len(all_300))

    # C2: drop unplayables (mirror main.py) — never render a card we cannot play.
    dropped = [r["id"] for r in all_300 if r.get("id") not in uploaded_url_map]
    if dropped:
        logger.warning("Dropping %d unplayable reels: %s", len(dropped), ", ".join(str(d) for d in dropped[:10]))
        all_300 = [r for r in all_300 if r.get("id") in uploaded_url_map]

    # F2: never shrink the live digest.
    prev_count = len(existing_items)
    if not all_300 or (prev_count >= main.MIN_DEPLOY_ITEMS
                       and len(all_300) < main.MIN_DEPLOY_ITEMS):
        logger.error("Only %d playable reels (previous %d, minimum %d); refusing to overwrite.",
                     len(all_300), prev_count, main.MIN_DEPLOY_ITEMS)
        return 2

    # 9. Save updated digest batch
    ranker.save_digest_batch(all_300, run_date=new_week_id)
    logger.info("Saved updated 300-reel digest to %s and data/digests/%s.json.", config.DIGEST_BATCH_FILE.name, new_week_id)

    # 10. Update last_run.json to today (a fresh week: anchorable).
    main.save_last_run_info(new_week_id, since_timestamp=int(time.time()), kind="weekly")
    logger.info("Updated data/last_run.json to week %s.", new_week_id)

    # 11. Build and deploy static PWA
    logger.info("Compiling static site for week %s...", new_week_id)
    site_builder.build_site(
        digest_data={"run_date": new_week_id, "items": all_300},
        r2_uploaded_urls=uploaded_url_map,
    )

    if deploy:
        if len(all_300) < main.MIN_DEPLOY_ITEMS:
            logger.error("Only %d reels (minimum %d); refusing to deploy.",
                         len(all_300), main.MIN_DEPLOY_ITEMS)
            return 2
        logger.info("Deploying updated site to GitHub Pages...")
        site_builder.deploy_to_gh_pages()
        logger.info("Successfully deployed to GitHub Pages!")

    return 0


if __name__ == "__main__":
    # B32: no hardcoded re-runs — the constants below once rewrote the live
    # digest and last_run.json with a stale week on every accidental run.
    import argparse as _argparse
    _p = _argparse.ArgumentParser(description="One-shot digest top-up (explicit args required).")
    _p.add_argument("--watched-count", type=int, required=True)
    _p.add_argument("--new-week-id", required=True, help="YYYY-MM-DD for the new digest week")
    _p.add_argument("--no-deploy", action="store_true")
    _a = _p.parse_args()
    sys.exit(topup_digest(watched_count=_a.watched_count, new_week_id=_a.new_week_id,
                          deploy=not _a.no_deploy))
