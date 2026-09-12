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


# Sync progress stages that may be resumed. The load gate and the reuse gate
# below must agree: "publishing" still carries the ranked list, so a
# mid-publish crash resumes at downloads instead of re-extracting.
RESUMABLE_SYNC_STAGES = ("extracting", "enriched", "ranked", "publishing", "cooling_down")
# Stages whose banked ranked list can be reused directly, skipping extraction.
RANKED_SYNC_STAGES = ("ranked", "publishing")

# Low-profile pacing (Instagram automation warning): slower than a human
# speed-reader, with periodic long breaks. Costs roughly an extra hour per
# weekly run at ~65 creators — meant for overnight runs. Download workers stay
# parallel (media CDN, low detection surface).
CREATOR_PAUSE = (7.0, 1.8, 3.0)  # mu, sigma, floor seconds between creators
CREATOR_BREAK_EVERY = 10  # creators visited between long breaks
CREATOR_BREAK_SECS = (180.0, 420.0)  # uniform range for long breaks
ENRICH_WORKERS = 1  # serial enrichment: no concurrent page loads
ENRICH_PAUSE = (5.0, 1.5, 2.5)  # mu, sigma, floor seconds per item
# Rate-limit backoff: minutes to sleep between retries of one creator before
# giving up (20+40+80 ≈ 140 min max per block; login redirects never sleep).
RATE_LIMIT_WAITS_MIN = (20, 40, 80)
# Single retry wait for feed-discovery rate limits (login deaths abort at once).
FEED_RETRY_WAIT_MIN = 20


def _alert_sync_abort(reason: str, detail: str) -> None:
    """Email the owner when a sync aborts without producing a digest (exit 2).

    Alert delivery itself must never break the abort path, hence the guard.
    """
    try:
        import notifier
        notifier.send_failure_alert_email(context=f"Sync aborted: {reason} — {detail}", exit_code=2)
    except Exception as alert_err:
        logger.warning("Failed to send abort alert email: %s", alert_err)


def _ensure_valid_session(session) -> bool:
    """Probe the session; on failure refresh Chrome cookies once and retry.

    Stale files self-heal with no human involved. A dead login stays dead
    (return False) so the caller aborts with an alert instead of retrying
    forever. Never raises.
    """
    try:
        if session.validate():
            return True
    except Exception as exc:
        logger.warning("Session validation error: %s", exc)
    logger.warning("Session invalid; refreshing Chrome cookies and retrying once...")
    try:
        import cookie_exporter
        cookie_exporter.export_instagram_cookies()
    except Exception as exc:
        logger.warning("Cookie refresh failed: %s", exc)
        return False
    for op in ("close", "start"):
        try:
            getattr(session, op)()
        except Exception as exc:
            logger.warning("Session %s during refresh retry failed: %s", op, exc)
            return False
    try:
        return bool(session.validate())
    except Exception as exc:
        logger.warning("Session re-validation error: %s", exc)
        return False


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

    # --- Staged resume for weekly sync (skipped on dry runs) ---
    # Progress means "work banked but not yet in the digest". Resume only when
    # the run parameters match (same anchor window + per-creator limit) so a
    # retry continues the same operation instead of mixing windows. Aborts
    # never delete banked work; only a completed digest (or a hopeless
    # viability verdict) clears it.
    sync_checkpoint = config.DATA_DIR / f"sync_progress_{week_id}.json"
    sync_read_path = sync_checkpoint
    if not dry_run and not sync_read_path.exists():
        older_sync = sorted(config.DATA_DIR.glob("sync_progress_*.json"))
        if older_sync:
            logger.info(
                "No sync progress for week %s; resuming from %s.",
                week_id, older_sync[-1].name,
            )
            sync_read_path = older_sync[-1]
    sync_progress: dict[str, Any] | None = None
    if not dry_run and sync_read_path.exists():
        try:
            loaded_sync = json.loads(sync_read_path.read_text(encoding="utf-8"))
            if (
                isinstance(loaded_sync, dict)
                and loaded_sync.get("version") == 1
                and loaded_sync.get("limit_per_creator") == limit_per_creator
                and loaded_sync.get("since_timestamp") == since_timestamp
                and loaded_sync.get("stage") in RESUMABLE_SYNC_STAGES
            ):
                sync_progress = loaded_sync
                if loaded_sync.get("days_back") != days_back:
                    logger.info(
                        "Sync progress window drifted (banked days_back=%s, now %d); resuming anyway.",
                        loaded_sync.get("days_back"), days_back,
                    )
            else:
                logger.warning(
                    "Ignoring sync progress %s (run parameters changed since it was written).",
                    sync_read_path.name,
                )
        except Exception as exc:
            logger.warning("Ignoring unreadable sync progress: %s", exc)
    if not dry_run:
        for stale_sync in config.DATA_DIR.glob("sync_progress_*.json"):
            if stale_sync != sync_checkpoint and stale_sync != sync_read_path:
                try:
                    stale_sync.unlink()
                except OSError:
                    pass

    def _write_sync_progress(stage: str, extra: dict[str, Any] | None = None) -> None:
        if dry_run:
            return
        payload = {
            "version": 1, "week_id": week_id,
            "days_back": days_back, "limit_per_creator": limit_per_creator,
            "since_timestamp": since_timestamp, "stage": stage,
        }
        if extra:
            payload.update(extra)
        try:
            import atomic_io
            atomic_io.durable_write_json(sync_checkpoint, payload)
        except Exception as io_err:
            logger.warning("Failed writing sync progress: %s", io_err)

    ranked_reels: list[dict[str, Any]] = []
    resume_ranked: list[dict[str, Any]] | None = None
    if sync_progress and sync_progress.get("stage") in RANKED_SYNC_STAGES:
        banked_ranked = [
            r for r in sync_progress.get("ranked", [])
            if isinstance(r, dict) and r.get("id")
        ]
        if banked_ranked:
            resume_ranked = banked_ranked
            logger.info(
                "Resuming weekly sync after ranking: %d reels banked, skipping to downloads.",
                len(resume_ranked),
            )
    if resume_ranked is not None:
        ranked_reels = resume_ranked
    else:
        with extractor.InstagramSession() as session:
            # Seed from an aborted run's staged progress: skip creators already
            # visited and reuse banked candidates/enrichment.
            done_map: dict[str, bool] = {}
            banked_enriched: dict[str, dict[str, Any]] = {}
            banked_shortlist: list[dict[str, Any]] | None = None
            extraction_complete = False
            if sync_progress and sync_progress.get("stage") in ("extracting", "enriched", "cooling_down"):
                banked_cands = [
                    r for r in sync_progress.get("candidates", [])
                    if isinstance(r, dict) and r.get("id")
                ]
                if banked_cands and not candidates:
                    candidates = banked_cands
                done_map = {
                    str(h): bool(e) for h, e in (sync_progress.get("done") or {}).items()
                }
                banked_enriched = {
                    r["id"]: r for r in sync_progress.get("enriched", [])
                    if isinstance(r, dict) and r.get("id")
                }
                stored_shortlist = [
                    r for r in sync_progress.get("shortlist", [])
                    if isinstance(r, dict) and r.get("id")
                ]
                banked_shortlist = stored_shortlist or None
                extraction_complete = sync_progress.get("stage") == "enriched" or bool(
                    sync_progress.get("extraction_complete")
                )
                if done_map or banked_cands:
                    logger.info(
                        "Seeded %d candidates (%d creators visited) from sync progress.",
                        len(candidates), len(done_map),
                    )
            candidates_cache_file = config.DATA_DIR / "candidates_cache.json"
            cache_hit = False
            if candidates_cache_file.exists():
                try:
                    if time.time() - candidates_cache_file.stat().st_mtime < 12 * 3600:
                        candidates = json.loads(candidates_cache_file.read_text(encoding="utf-8"))
                        logger.info("Loaded %d candidate reels from fresh candidates_cache.json.", len(candidates))
                        cache_hit = True
                except Exception:
                    candidates = []
            if cache_hit:
                # A fresh cache covers every creator post-gate; nothing to visit.
                extraction_complete = True

            per_source: list[tuple[dict[str, Any], str, int]] = []
            for src in ordered_sources:
                handle = src.get("handle", "")
                if not handle:
                    continue
                cat = src.get("category", "")
                max_candidate_reels = 6 if cat == "food" else min(limit_per_creator, 5)
                per_source.append((src, handle, max_candidate_reels))
            expected_total = sum(max_n for _, _, max_n in per_source)
            remaining_sources = [
                (src, handle, max_n) for src, handle, max_n in per_source
                if handle not in done_map
            ]
            extraction_total = len(remaining_sources) + len(done_map)
            if extraction_complete:
                remaining_sources = []
            visited_this_run = 0
            if remaining_sources:
                empty_streak = 0
                if not _ensure_valid_session(session):
                    if candidates or done_map:
                        _write_sync_progress("extracting", {
                            "done": done_map, "candidates": candidates,
                            "extraction_complete": False,
                            "total_sources": extraction_total,
                        })
                    _alert_sync_abort("Instagram session blocked", "validation failed after one cookie refresh")
                    try:
                        local_server.raise_cookie_attention(
                            pipeline="weekly-sync",
                            reason="Session validation failed; press refresh to verify the login",
                        )
                    except Exception as popup_err:
                        logger.warning("Failed raising cookie attention popup: %s", popup_err)
                    return 2
                try:
                    def _extract_with_backoff(handle, max_candidate_reels):
                        # Rate limits sleep through the night instead of killing
                        # the run; login redirects re-raise at once (dead cookies
                        # won't heal by waiting). Heartbeats keep the dashboard
                        # progress file fresh during long sleeps.
                        for attempt in range(len(RATE_LIMIT_WAITS_MIN) + 1):
                            try:
                                return extractor.extract_creator_reels(
                                    handle=handle,
                                    max_reels=max_candidate_reels,
                                    days_back=days_back,
                                    fast_mode=True,  # Fast discovery from reels tab
                                    session=session,
                                )
                            except extractor.InstagramBlocked as exc:
                                msg = str(exc)
                                if "/accounts/login" in msg or "login_required" in msg:
                                    raise
                                if attempt >= len(RATE_LIMIT_WAITS_MIN):
                                    logger.error(
                                        "Rate limit persists after %d backoffs on @%s; aborting with banked progress.",
                                        attempt, handle,
                                    )
                                    raise
                                wait_min = RATE_LIMIT_WAITS_MIN[attempt]
                                logger.warning(
                                    "Rate limit on @%s (%s). Sleeping %d min (retry %d/%d)...",
                                    handle, msg, wait_min, attempt + 1, len(RATE_LIMIT_WAITS_MIN),
                                )
                                _write_sync_progress("cooling_down", {
                                    "done": done_map, "candidates": candidates,
                                    "extraction_complete": False,
                                    "total_sources": extraction_total,
                                    "blocked_handle": handle,
                                    "resumes_in_min": wait_min,
                                })
                                time.sleep(wait_min * 60)
                        raise AssertionError("unreachable backoff exit")
                    # Ensure candidate gathering covers all active creators so every creator is represented
                    for idx, (src, handle, max_candidate_reels) in enumerate(remaining_sources, 1):
                        cat = src.get("category", "")
                        logger.info("[%d/%d] Extracting candidate reels for @%s (%s)...", idx, len(remaining_sources), handle, cat)
                        reels = _extract_with_backoff(handle, max_candidate_reels)
                        done_map[handle] = not reels
                        visited_this_run += 1
                        if not reels:
                            empty_streak += 1
                            if empty_streak >= 2:
                                pause = min(120, 10 * 2 ** (empty_streak - 2))
                                logger.warning("Two empty creators in a row; backing off %ds.", pause)
                                time.sleep(pause)
                        else:
                            empty_streak = 0

                        candidates.extend(reels)
                        mu, sigma, floor = CREATOR_PAUSE
                        extractor.human_pause(mu=mu, sigma=sigma, floor=floor)
                        if visited_this_run % CREATOR_BREAK_EVERY == 0:
                            rest = random.uniform(*CREATOR_BREAK_SECS)
                            logger.info("Low-profile break: resting %.0fs after %d creators...",
                                        rest, visited_this_run)
                            time.sleep(rest)
                        if visited_this_run % 5 == 0:
                            _write_sync_progress("extracting", {
                                "done": done_map, "candidates": candidates,
                                "extraction_complete": False,
                                "total_sources": extraction_total,
                            })
                except extractor.InstagramBlocked as exc:
                    logger.error("Instagram blocked the session (%s). Aborting run without touching digest/site.", exc)
                    _write_sync_progress("extracting", {
                        "done": done_map, "candidates": candidates,
                        "extraction_complete": False,
                        "total_sources": extraction_total,
                    })
                    if "/accounts/login" in str(exc) or "login_required" in str(exc):
                        # Cookie death in the creator path (login redirect
                        # surfaces as InstagramBlocked, not CookieExpired):
                        # same email + popup treatment as the feed path.
                        try:
                            import notifier
                            notifier.send_cookie_alert_email()
                        except Exception as alert_err:
                            logger.warning("Failed to send cookie alert email: %s", alert_err)
                        try:
                            local_server.raise_cookie_attention(
                                pipeline="weekly-sync",
                                reason="Instagram session expired during creator extraction",
                            )
                        except Exception as popup_err:
                            logger.warning("Failed raising cookie attention popup: %s", popup_err)
                    else:
                        _alert_sync_abort("Instagram session blocked", str(exc))
                    return 2

                empty_total = sum(1 for was_empty in done_map.values() if was_empty)
                if (len(candidates) < MIN_CANDIDATE_RATIO * expected_total
                        or empty_total > MAX_EMPTY_CREATOR_RATIO * len(per_source)):
                    logger.error(
                        "Viability gate failed: %d candidates (expected >=%d), %d/%d creators empty. Aborting.",
                        len(candidates), int(MIN_CANDIDATE_RATIO * expected_total), empty_total, len(per_source)
                    )
                    try:
                        sync_checkpoint.unlink(missing_ok=True)
                    except OSError:
                        pass
                    _alert_sync_abort(
                        "viability gate failed",
                        f"{len(candidates)} candidates, {empty_total}/{len(per_source)} creators empty",
                    )
                    return 2

                logger.info("Extracted total %d candidate reels across creators.", len(candidates))
                try:
                    import atomic_io
                    atomic_io.durable_write_json(candidates_cache_file, candidates)
                except Exception:
                    pass
                _write_sync_progress("extracting", {
                    "done": done_map, "candidates": candidates,
                    "extraction_complete": True,
                    "total_sources": extraction_total,
                })

            # 4. Two-Pass Selection & Ranking (C3):
            # Pass 1: Cheap reach-only ranking to shortlist (2x top digest size)
            if banked_shortlist is not None:
                shortlist = banked_shortlist
                logger.info("Reusing %d banked shortlist reels from sync progress.", len(shortlist))
            else:
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
            shortlist_ids = [r.get("id") for r in shortlist]
            todo = [r for r in shortlist if r.get("id") not in banked_enriched]
            enriched_by_id: dict[str, dict[str, Any]] = {
                rid: banked_enriched[rid] for rid in shortlist_ids
                if rid in banked_enriched
            }
            if banked_enriched:
                logger.info(
                    "Reusing %d banked enriched reels; enriching %d remaining.",
                    len(enriched_by_id), len(todo),
                )
            else:
                logger.info("Enriching shortlist of %d reels with real metadata (cutoff_ts=%s, max_workers=2)...", len(shortlist), cutoff_ts)

            newly_enriched = 0
            if todo:
                import queue as _queue
                _session_pool: _queue.Queue = _queue.Queue()
                _pool_sessions = [extractor.InstagramSession() for _ in range(ENRICH_WORKERS)]
                for _s in _pool_sessions:
                    _s.start()
                    _session_pool.put(_s)

                def _enrich_item(r: dict[str, Any]) -> dict[str, Any] | None:
                    sess = _session_pool.get()
                    try:
                        mu, sigma, floor = ENRICH_PAUSE
                        extractor.human_pause(mu=mu, sigma=sigma, floor=floor)
                        m = extractor.extract_single_reel_metadata(r, session=sess)
                        if not m or (m.get("timestamp") or 0) < cutoff_ts:
                            return None
                        return m
                    finally:
                        _session_pool.put(sess)

                try:
                    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as executor:
                        futures = [executor.submit(_enrich_item, r) for r in todo]
                        for f in as_completed(futures):
                            try:
                                res = f.result()
                                if res and res.get("id"):
                                    enriched_by_id[res["id"]] = res
                                    newly_enriched += 1
                                    if newly_enriched % 25 == 0:
                                        _write_sync_progress("enriched", {
                                            "candidates": candidates,
                                            "shortlist": shortlist,
                                            "enriched": list(enriched_by_id.values()),
                                            "extraction_complete": True,
                                        })
                            except Exception as exc:
                                logger.debug("Enrichment error: %s", exc)
                finally:
                    for _s in _pool_sessions:
                        try:
                            _s.close()
                        except Exception:
                            pass

            enriched = [enriched_by_id[rid] for rid in shortlist_ids if rid in enriched_by_id]
            logger.info("Enriched %d valid reels within date window out of %d candidates.", len(enriched), len(shortlist))
            _write_sync_progress("enriched", {
                "candidates": candidates,
                "shortlist": shortlist,
                "enriched": enriched,
                "extraction_complete": True,
            })

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
                    external_reels = []
                    feed_blocked = None
                    for feed_attempt in (1, 2):
                        try:
                            external_reels = extractor.extract_external_reels_from_feed(
                                session=session,
                                target_count=deficit,
                                existing_ids=existing_ids,
                                active_sources=active_sources,
                            )
                            feed_blocked = None
                            break
                        except extractor.CookieExpiredException as exc:
                            # Feed blocks masquerade as cookie deaths (login URL
                            # check inside the extractor). Only true login
                            # redirects skip the retry: sleeping won't fix those.
                            feed_blocked = exc
                            if "/accounts/login" in str(exc) or "login_required" in str(exc):
                                break
                            if feed_attempt == 1:
                                logger.warning(
                                    "Rate limit during feed discovery; sleeping %d min, then one retry...",
                                    FEED_RETRY_WAIT_MIN,
                                )
                                time.sleep(FEED_RETRY_WAIT_MIN * 60)
                    if feed_blocked is not None:
                        raise feed_blocked
                    if external_reels:
                        logger.info("Discovered %d external high-signal reels from feed.", len(external_reels))
                        combined = ranked_reels + external_reels
                        for idx, r in enumerate(combined, 1):
                            r["rank"] = idx
                            r["rank_display"] = f"#{idx:02d}"
                        ranked_reels = combined
                except extractor.CookieExpiredException as exc:
                    if "/accounts/login" in str(exc) or "login_required" in str(exc):
                        logger.warning("Cookie expired during external discovery: %s", exc)
                        try:
                            import notifier
                            notifier.send_cookie_alert_email()
                        except Exception as alert_err:
                            logger.warning("Failed to send cookie alert email: %s", alert_err)
                        try:
                            local_server.raise_cookie_attention(
                                pipeline="weekly-sync",
                                reason="Instagram session expired during weekly discovery",
                            )
                        except Exception as popup_err:
                            logger.warning("Failed raising cookie attention popup: %s", popup_err)
                    else:
                        logger.warning(
                            "Feed discovery rate-limited twice; publishing channel reels only: %s", exc)
                except Exception as exc:
                    logger.warning("External reels discovery failed: %s", exc)

            if ranked_reels:
                _write_sync_progress("ranked", {"ranked": ranked_reels})
            elif not dry_run:
                # Hopeless run: drop staged progress so the next attempt starts fresh.
                try:
                    sync_checkpoint.unlink(missing_ok=True)
                except OSError:
                    pass

    if not ranked_reels:
        logger.error("No reels qualified for Top Digest. Aborting run without touching digest/site.")
        _alert_sync_abort("no qualifying reels", "Top Digest selection came back empty")
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
            published_done = 0
            for future in as_completed(future_to_id):
                try:
                    rid, url = future.result()
                    if url:
                        uploaded_url_map[rid] = url
                except Exception as exc:
                    rid = future_to_id[future]
                    logger.warning("Worker error processing reel %s: %s", rid, exc)
                published_done += 1
                _write_sync_progress("publishing", {
                    "ranked": ranked_reels,
                    "published": published_done,
                    "published_total": len(ranked_reels),
                })

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
            _alert_sync_abort(
                "deploy refused",
                f"only {len(ranked_reels)} playable reels (minimum {MIN_DEPLOY_ITEMS})",
            )
            return 2

        # 6. Save digest batch payload (only playable reels saved!)
        ranker.save_digest_batch(ranked_reels, run_date=week_id)

        # Staged work is now in the digest: clear sync progress so the next
        # run starts fresh instead of replaying it.
        try:
            (config.DATA_DIR / f"sync_progress_{week_id}.json").unlink(missing_ok=True)
        except OSError:
            pass

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

    # Resume support: discoveries from a run killed mid-scroll (cookie death,
    # internet drop, shutdown) are checkpointed to data/expand_checkpoint_*.json.
    # Pick them up and discover only the remainder instead of starting over.
    # The checkpoint means "discovered but not yet in the digest".
    checkpoint_file = config.DATA_DIR / f"expand_checkpoint_{week_id}.json"
    read_path = checkpoint_file
    if not read_path.exists():
        older = sorted(config.DATA_DIR.glob("expand_checkpoint_*.json"))
        if older:
            logger.info(
                "No checkpoint for week %s; resuming from %s.",
                week_id, older[-1].name,
            )
            read_path = older[-1]

    def _write_checkpoint(items: list[dict[str, Any]]) -> None:
        # Envelope carries the original target so unattended auto-resume can
        # top up correctly without being told the count again.
        payload = {"version": 1, "target_count": target_count, "reels": list(items)}
        try:
            import atomic_io
            atomic_io.durable_write_json(checkpoint_file, payload)
        except Exception as io_err:
            logger.warning("Failed writing expand checkpoint: %s", io_err)

    progress_file = config.DATA_DIR / f"expand_progress_{week_id}.json"

    def _write_expand_progress(phase: str, done: int, total: int,
                               extra: dict[str, Any] | None = None) -> None:
        # Live lane for the dashboard progress bar (polled while running).
        # Separate from the resume checkpoint: deleted on success, and the
        # dashboard ignores files untouched for a while after a crash.
        payload = {
            "version": 1, "week_id": week_id, "target_count": target_count,
            "phase": phase, "done": done, "total": total,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            payload.update(extra)
        try:
            import atomic_io
            atomic_io.durable_write_json(progress_file, payload)
        except Exception as io_err:
            logger.warning("Failed writing expand progress: %s", io_err)

    def _clear_expand_progress() -> None:
        try:
            progress_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _read_checkpoint(path: Path) -> list[dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw = data.get("reels") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        return [r for r in raw if isinstance(r, dict) and r.get("id")]

    resumed: list[dict[str, Any]] = _read_checkpoint(read_path) if read_path.exists() else []
    if read_path.exists():
        if resumed:
            logger.info(
                "Resuming +%d expansion from checkpoint: %d reels already discovered.",
                target_count, len(resumed),
            )
        else:
            logger.warning("Ignoring unreadable expand checkpoint: %s", read_path.name)
            try:
                read_path.unlink(missing_ok=True)
            except OSError:
                pass
    # Writes land on the current week file; anything else is stale. Never drop
    # read_path itself until its items are integrated: a failure before the
    # first checkpoint write would otherwise lose the migrated resume.
    for stale in config.DATA_DIR.glob("expand_checkpoint_*.json"):
        if stale != checkpoint_file and stale != read_path:
            try:
                stale.unlink()
            except OSError:
                pass
    for stale_progress in config.DATA_DIR.glob("expand_progress_*.json"):
        if stale_progress != progress_file:
            try:
                stale_progress.unlink()
            except OSError:
                pass
    _write_expand_progress("discovering", len(resumed), target_count,
                             {"banked": len(resumed)})
    resumed_ids = {r["id"] for r in resumed}
    existing_ids |= resumed_ids

    fresh_so_far: list[dict[str, Any]] = []

    def _on_discovery_progress(snapshot: list[dict[str, Any]]) -> None:
        # Stream-checkpoint every 10 finds: a shutdown mid-scroll loses at most
        # the finds since the last snapshot, never the whole run.
        fresh_so_far.clear()
        fresh_so_far.extend(snapshot)
        fresh = [r for r in snapshot if r.get("id") not in resumed_ids]
        _write_checkpoint(resumed + fresh)
        _write_expand_progress("discovering", len(resumed) + len(fresh), target_count,
                               {"banked": len(resumed)})

    # 1. Extract external reels from Reels feed (topping up the checkpoint)
    external_reels: list[dict[str, Any]] = list(resumed)
    remaining = target_count - len(resumed)
    if remaining > 0:
        with extractor.InstagramSession() as session:
            sources = extractor.load_sources()
            active_sources = [s for s in sources if s.get("enabled", True)]
            try:
                fresh = extractor.extract_external_reels_from_feed(
                    session=session,
                    target_count=remaining,
                    existing_ids=existing_ids,
                    active_sources=active_sources,
                    on_progress=_on_discovery_progress,
                )
                external_reels += [r for r in fresh if r.get("id") not in resumed_ids]
            except extractor.CookieExpiredException as exc:
                partial = getattr(exc, "partial", None) or fresh_so_far
                salvaged = [r for r in partial if isinstance(r, dict) and r.get("id") not in resumed_ids]
                checkpoint = resumed + salvaged
                if checkpoint:
                    _write_checkpoint(checkpoint)
                    logger.warning(
                        "Cookie expired during external discovery; checkpointed %d reels (%d resumed, %d new) for resume.",
                        len(checkpoint), len(resumed), len(salvaged),
                    )
                else:
                    logger.warning("Cookie expired during external discovery: %s", exc)
                try:
                    import notifier
                    notifier.send_cookie_alert_email()
                except Exception as alert_err:
                    logger.warning("Failed to send cookie alert email: %s", alert_err)
                try:
                    local_server.raise_cookie_attention(
                        pipeline="expand",
                        reason="Instagram session expired during +100 discovery",
                    )
                except Exception as popup_err:
                    logger.warning("Failed raising cookie attention popup: %s", popup_err)
                return 2
            except Exception as exc:
                # Internet drop, browser crash, etc: salvage whatever was found
                # (exception partials, else the last progress snapshot) so the
                # next +100 resumes instead of restarting. No cookie mail here:
                # this path is not a cookie diagnosis.
                partial = getattr(exc, "partial", None) or fresh_so_far
                salvaged = [r for r in partial if isinstance(r, dict) and r.get("id") not in resumed_ids]
                if salvaged:
                    _write_checkpoint(resumed + salvaged)
                    logger.warning(
                        "Discovery interrupted (%s); checkpointed %d reels for resume.",
                        exc, len(resumed) + len(salvaged),
                    )
                else:
                    logger.error("Failed discovering external reels: %s", exc)
                return 2
    else:
        logger.info(
            "Checkpoint already covers +%d target; integrating %d resumed reels without new discovery.",
            target_count, len(resumed),
        )

    if not external_reels:
        logger.warning("No new external reels could be extracted from feed.")
        return 2

    logger.info(
        "Expanded with %d new external reels (%d resumed from checkpoint).",
        len(external_reels), len(resumed),
    )

    # Persist everything discovered before downloads start: a shutdown or crash
    # during the (long) download/upload phase still resumes instead of losing
    # the discoveries. Cleared below once reels reach the digest.
    _write_checkpoint(external_reels)
    _write_expand_progress("downloading", 0, len(external_reels))

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
        download_done = 0
        for f in as_completed(futures):
            try:
                res = f.result()
                if res is not None:
                    downloaded.append(res)
            except Exception as exc:
                logger.warning("Error downloading expanded reel: %s", exc)
            download_done += 1
            _write_expand_progress("downloading", download_done, len(external_reels))

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
    _write_expand_progress("uploading", 0, max(1, len(downloadable)))
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(upload_new_reel, r) for r in downloadable]
        upload_done = 0
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
            upload_done += 1
            _write_expand_progress("uploading", upload_done, max(1, len(downloadable)))

    new_ranked.sort(key=lambda r: r.get("rank", 9999))
    combined_items = existing_items + new_ranked

    logger.info("Expansion successfully integrated: %d existing + %d new = %d total reels.",
                len(existing_items), len(new_ranked), len(combined_items))

    # 3. Save combined digest payload
    ranker.save_digest_batch(combined_items, run_date=week_id)

    # Checkpoint fulfilled for reels that reached the digest. Anything discovered
    # but not integrated (failed downloads/uploads, e.g. an internet drop
    # mid-phase) stays checkpointed so the next +100 retries it.
    integrated_ids = {r["id"] for r in new_ranked if r.get("id")}
    leftover = [r for r in external_reels if r.get("id") not in integrated_ids]
    if leftover:
        _write_checkpoint(leftover)
        logger.warning(
            "%d reels failed download/upload; kept in checkpoint for the next +100.",
            len(leftover),
        )
    else:
        # Everything integrated: drop the current file and any older file we
        # resumed from, so the next run cannot re-integrate banked reels.
        for done_file in {checkpoint_file, read_path}:
            try:
                done_file.unlink(missing_ok=True)
            except OSError:
                pass

    # 4. Rebuild static site
    _write_expand_progress("finalizing", 1, 1)
    site_builder.build_site(
        digest_data={"run_date": week_id, "items": combined_items},
        r2_uploaded_urls=uploaded_url_map,
    )

    # 5. Deploy to GitHub Pages if requested
    if deploy:
        site_builder.deploy_to_gh_pages()

    _clear_expand_progress()
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
