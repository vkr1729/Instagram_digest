"""
main.py — Main CLI orchestrator for Instagram Digest v5.0.0.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import re as _re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

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


def _quarantine_corrupt(path: Path, exc: Exception) -> None:
    """Preserve an unreadable state file alongside for forensics."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.corrupt-{ts}")
        backup.write_bytes(path.read_bytes())
        logger.warning("Quarantined corrupt %s to %s: %s", path, backup, exc)
    except Exception:
        logger.warning("Unreadable %s; starting fresh: %s", path, exc)


def _lock_info_path() -> Path:
    return config.DATA_DIR / ".pipeline.lock.info"


def _write_lock_info() -> None:
    """Best-effort holder sidecar so busy errors can name the owner."""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _lock_info_path().write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "cmd": " ".join(sys.argv[:4]),
        }), encoding="utf-8")
    except Exception:
        pass


def _clear_lock_info() -> None:
    try:
        _lock_info_path().unlink(missing_ok=True)
    except Exception:
        pass


def _holder_is_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def lock_holder_info() -> dict[str, Any] | None:
    """Read the holder sidecar, sweeping it when the owner is provably gone.

    Read-only w.r.t. the lock itself: flock self-heals on process death, so
    this only ever removes stale *metadata*, never breaks a live exclusion.
    """
    try:
        raw = json.loads(_lock_info_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or not raw.get("pid"):
        return None
    if not _holder_is_alive(raw["pid"]):
        _clear_lock_info()
        return None
    return raw


@contextmanager
def _pipeline_file_lock() -> Iterator[None]:
    """Cross-process exclusion for digest-mutating pipelines.

    ``local_server._PIPELINE_LOCK`` is a threading lock: it cannot see cron
    (run_weekly.sh), login-resume (resume_pending.sh) or manual CLI runs,
    which execute in separate processes. This flock-guarded file is the
    cross-process counterpart. Raises RuntimeError when another pipeline
    holds the lock.
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = config.DATA_DIR / ".pipeline.lock"
    if fcntl is None:  # pragma: no cover - non-POSIX fallback
        # PY-P2-1: no exclusion possible here — say so loudly instead of
        # silently allowing concurrent digest mutations.
        logger.warning("fcntl unavailable: cross-process pipeline lock disabled; "
                       "avoid concurrent cron/manual/dashboard runs on this platform.")
        yield
        return
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = lock_holder_info()
            detail = ""
            if holder:
                detail = (f" (held by pid {holder['pid']} since "
                          f"{holder.get('started_at', '?')}: {holder.get('cmd', '?')})")
            raise PipelineBusy(
                "another pipeline (sync/expand) holds data/.pipeline.lock" + detail
            )
        _write_lock_info()
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        _clear_lock_info()
        os.close(fd)


def get_last_run_info() -> dict[str, Any] | None:
    """Retrieve timestamp and metadata about the last completed sync run."""
    try:
        if config.LAST_RUN_FILE.exists():
            return json.loads(config.LAST_RUN_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        _quarantine_corrupt(config.LAST_RUN_FILE, exc)
    return None


def save_last_run_info(week_id: str, timestamp: float | None = None,
                       since_timestamp: int | None = None) -> dict[str, Any]:
    """Persist the timestamp and week_id of a successful sync run."""
    ts = timestamp if timestamp is not None else time.time()
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    info = {
        "timestamp": ts,
        "last_run_utc": dt_utc,
        "week_id": week_id,
        # Weekly runs cover the full window; ad-hoc runs are top-ups that must
        # not shorten the next weekly anchor (F20).
        "kind": "ad-hoc" if since_timestamp is not None else "weekly",
    }
    try:
        import atomic_io
        atomic_io.durable_write_json(config.LAST_RUN_FILE, info)
        logger.info("Saved last run info: %s (%s)", dt_utc, week_id)
    except Exception as exc:
        logger.warning("Failed saving last_run.json: %s", exc)
    return info


_SAFE_COMPONENT_RE = _re.compile(r"[^A-Za-z0-9._-]")


def _safe_component(value: Any, fallback: str) -> str:
    """Filename / R2-key component: strips path separators and glob
    metacharacters, never empty, never a dot-segment."""
    s = _SAFE_COMPONENT_RE.sub("", str(value or "")).strip(".")
    return s or fallback


def _digest_item_count() -> int:
    """Return the item count of the persisted digest batch (0 when missing/unreadable)."""
    try:
        payload = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
        items = payload.get("items", [])
        return len(items) if isinstance(items, list) else 0
    except Exception:
        return 0


def _persisted_digest_week() -> str:
    """Return the run_date the persisted live digest points at ("" when missing/unreadable).

    The Pages feed and the iOS app play this week's R2 keys until a new
    save_digest_batch lands, so the JIT purger must never delete it first.
    """
    try:
        payload = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
        week = payload.get("run_date") or ""
        return week if isinstance(week, str) else ""
    except Exception:
        return ""


def _purge_current_week_stray_r2_keys(week_id: str, ranked_ids: set[str]) -> list[str]:
    """Delete current-week R2 keys not referenced by the final ranked list.

    Scoped strictly to ``videos/<week_id>/``: a key is kept when its reel-id
    suffix (``_<reel_id>.mp4``) matches the final ranked set — the same
    suffix-match style as ``storage_r2.purge_unreferenced_r2_videos``. This
    reclaims partial/interrupted-run uploads (strays) BEFORE the upload phase
    so they never linger as orphans. Must run BEFORE ``save_digest_batch``
    (no digest references the new week yet, so the generic orphan purger
    cannot see these keys). Returns the purged key list.
    """
    prefix = f"videos/{week_id}/"
    try:
        existing = storage_r2.get_existing_r2_keys(prefix)
    except Exception as exc:
        logger.warning("Stray-key listing failed for %s: %s", prefix, exc)
        return []
    strays = []
    try:
        from urllib.parse import quote as _quote, unquote as _unquote
    except Exception:
        _quote = _unquote = None  # type: ignore[assignment]
    for k in (existing or set()):
        if not (k.startswith(prefix) and k.endswith(".mp4")):
            continue
        try:
            decoded = _unquote(k) if _unquote else k
        except Exception:
            decoded = k
        keep = False
        for rid in ranked_ids:
            if k.endswith(f"_{rid}.mp4") or decoded.endswith(f"_{rid}.mp4"):
                keep = True
                break
            if _quote:
                try:
                    if k.endswith(f"_{_quote(str(rid), safe='')}.mp4"):
                        keep = True
                        break
                except Exception:
                    pass
        if not keep:
            strays.append(k)
    if not strays:
        return []
    s3 = storage_r2.get_s3_client()
    if s3 is None:
        logger.info("R2 credentials not active; skipping current-week stray purge.")
        return []
    purged: list[str] = []
    try:
        for i in range(0, len(strays), 1000):
            chunk = strays[i:i + 1000]
            logger.info("Deleting %d stray current-week R2 object(s) under %s...", len(chunk), prefix)
            resp = s3.delete_objects(
                Bucket=config.R2_BUCKET_NAME,
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": False},
            )
            for err in resp.get("Errors") or []:
                logger.error("Failed deleting stray %s: %s", err.get("Key"), err.get("Message"))
            purged.extend([d.get("Key", "") for d in resp.get("Deleted") or []])
    except Exception as exc:
        logger.warning("Error during current-week stray purge: %s", exc)
    if purged:
        logger.info("Purged %d stray current-week object(s) from Cloudflare R2.", len(purged))
    return purged


# Sync progress stages that may be resumed. The load gate and the reuse gate
# below must agree: "publishing" still carries the ranked list, so a
# mid-publish crash resumes at downloads instead of re-extracting.
RESUMABLE_SYNC_STAGES = ("extracting", "enriched", "ranked", "publishing", "cooling_down", "shortfall_paused")
# Stages whose banked ranked list can be reused directly, skipping extraction.
RANKED_SYNC_STAGES = ("ranked", "publishing")

# A banked ranked list older than this must never be republished as a new week.
MAX_SYNC_RESUME_AGE_DAYS = 3


# Cross-week dedup ledger: reel IDs published in the last 30 days are
# filtered from future digests, so a reel that misses one week's cut cannot
# resurface the next week and break the "finite briefing" promise.
SEEN_IDS_FILE = config.DATA_DIR / "seen_reel_ids.json"
SEEN_IDS_RETENTION_DAYS = 30


def _load_seen_reel_ids() -> dict[str, float]:
    """Reel id -> first-seen unix timestamp (pruned to retention on load)."""
    try:
        raw = json.loads(SEEN_IDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    cutoff = time.time() - SEEN_IDS_RETENTION_DAYS * 86400
    return {rid: ts for rid, ts in raw.items() if isinstance(ts, (int, float)) and ts >= cutoff}


def _record_seen_reel_ids(reels: list[dict[str, Any]]) -> int:
    """Add published reel IDs to the ledger (atomic write). Returns new count."""
    seen = _load_seen_reel_ids()
    now = time.time()
    added = 0
    for r in reels:
        rid = str(r.get("id") or "")
        if rid and rid not in seen:
            seen[rid] = now
            added += 1
    try:
        import atomic_io
        atomic_io.durable_write_json(SEEN_IDS_FILE, seen)
    except Exception:
        pass
    return added


def _filter_seen_reel_ids(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop candidates published in a recent digest. Never drops everything:
    if the filter would empty the pool, it returns the pool unfiltered and
    logs loudly (a torn ledger must not abort a run)."""
    if not candidates:
        return candidates
    seen = _load_seen_reel_ids()
    if not seen:
        return candidates
    fresh = [c for c in candidates if str(c.get("id") or "") not in seen]
    dropped = len(candidates) - len(fresh)
    if dropped:
        logger.info("Cross-week dedup: filtered %d recently-published reels.", dropped)
    if not fresh:
        logger.warning("Cross-week dedup would drop all %d candidates; keeping pool unfiltered.",
                       len(candidates))
        return candidates
    return fresh


# Upload outbox: reels downloaded locally but never uploaded (network gap,
# R2 outage). Written at publish time, consumed by --reconcile. Survives
# the run, unlike in-memory retries — tonight's 21 would have been a
# 5-minute reconcile instead of surgery.
def _outbox_path(week_id: str) -> Path:
    return config.DATA_DIR / f"upload_outbox_{week_id}.json"


def _write_upload_outbox(week_id: str, reels: list[dict[str, Any]],
                         local_paths: dict[str, str]) -> None:
    """Persist upload-failed reels + their local file paths (atomic)."""
    import atomic_io
    payload = {
        "version": 1,
        "week_id": week_id,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "reels": reels,
        "local_paths": {rid: p for rid, p in local_paths.items()
                        if any(r.get("id") == rid for r in reels)},
    }
    atomic_io.durable_write_json(_outbox_path(week_id), payload)
    logger.info("Upload outbox: parked %d reels for --reconcile.", len(reels))


def _read_upload_outbox(week_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_outbox_path(week_id).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("reels"), list):
        return None
    return data


def run_reconcile(week_id: str | None = None, deploy: bool = False) -> int:
    """Finish a previous week's parked uploads without touching Instagram.

    Uploads outbox reels to R2, merges them into the live digest, rebuilds
    the site, and optionally deploys. Zero Meta access: safe on any network.
    Returns 0 on success, 1 when nothing was pending, 2 on partial failure.
    """
    try:
        with _pipeline_file_lock():
            return _run_reconcile(week_id, deploy)
    except PipelineBusy as exc:
        logger.error("%s; refusing to start.", exc)
        return 3


def _run_reconcile(week_id: str | None = None, deploy: bool = False) -> int:
    if week_id is None:
        try:
            digest = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
            week_id = digest.get("run_date") or ""
        except Exception:
            week_id = ""
    if not week_id:
        logger.error("No week given and no live digest to infer it from.")
        return 1
    box = _read_upload_outbox(week_id)
    if not box or not box.get("reels"):
        logger.info("Upload outbox for %s is empty; nothing to reconcile.", week_id)
        return 1
    reels = [r for r in box["reels"] if isinstance(r, dict) and r.get("id")]
    paths = {rid: Path(p) for rid, p in (box.get("local_paths") or {}).items()}
    logger.info("Reconciling %d parked uploads for week %s...", len(reels), week_id)

    existing_keys = storage_r2.get_existing_r2_keys(f"videos/{week_id}/")
    uploaded: dict[str, str] = {}

    def _up(reel: dict[str, Any]) -> tuple[str, str]:
        rid = str(reel["id"])
        local = paths.get(rid)
        if not local or not local.exists():
            week_dir = config.VIDEOS_DIR / week_id
            cands = list(week_dir.glob(f"*_{rid}.mp4"))
            local = cands[0] if cands else None
        if not local or not local.exists():
            logger.warning("Outbox reel %s has no local file; skipping.", rid)
            return rid, ""
        url = storage_r2.upload_reel_to_r2(local, week_id=week_id,
                                           key_name=local.name,
                                           existing_keys=existing_keys)
        return rid, url

    with ThreadPoolExecutor(max_workers=4) as executor:
        futs = {executor.submit(_up, r): str(r["id"]) for r in reels}
        for f in as_completed(futs):
            try:
                rid, url = f.result()
                if url:
                    uploaded[rid] = url
            except Exception as exc:
                logger.warning("Reconcile upload error for %s: %s", futs[f], exc)

    failed = [r for r in reels if str(r["id"]) not in uploaded]
    if failed:
        logger.error("Reconcile: %d/%d uploads still failing; outbox kept for retry.",
                     len(failed), len(reels))
        try:
            _write_upload_outbox(week_id, failed,
                                 {rid: str(paths[rid]) for rid in [str(r["id"]) for r in failed]
                                  if rid in paths})
        except Exception:
            pass
        return 2

    try:
        _outbox_path(week_id).unlink(missing_ok=True)
    except OSError:
        pass

    try:
        digest = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Reconcile uploaded %d reels but cannot read live digest: %s",
                     len(uploaded), exc)
        return 2
    if digest.get("run_date") != week_id:
        logger.error("Live digest is week %s, outbox is %s; refusing to merge across weeks.",
                     digest.get("run_date"), week_id)
        return 2
    items = digest.get("items", [])
    live_ids = {it.get("id") for it in items}
    merged = 0
    for reel in reels:
        rid = str(reel["id"])
        if rid in live_ids:
            continue
        uploaded_url = uploaded[rid]
        try:
            true_rank = int(uploaded_url.rsplit("/", 1)[-1].split("_", 1)[0])
        except (ValueError, IndexError):
            true_rank = int(reel.get("rank") or 0) or None
        if not true_rank:
            logger.warning("Reconcile: no original rank for %s; keeping upload order.", rid)
            true_rank = max([int(i.get("rank") or 0) for i in items] + [len(items)]) + 1
        reel["r2_url"] = reel["video_url"] = uploaded_url
        reel["rank"] = true_rank
        reel["rank_display"] = f"#{true_rank:02d}"
        items.append(reel)
        merged += 1
    items.sort(key=lambda it: int(it.get("rank") or 0))
    try:
        _record_seen_reel_ids([r for r in reels if str(r["id"]) in uploaded])
    except Exception:
        pass
    ranker.save_digest_batch(items, run_date=week_id)
    logger.info("Reconcile: merged %d reels; digest now %d items.", merged, len(items))
    url_map = {it["id"]: (it.get("r2_url") or it.get("video_url"))
               for it in items if it.get("id")}
    site_builder.build_site(digest_data={"run_date": week_id, "items": items},
                            r2_uploaded_urls=url_map)
    if deploy:
        if len(items) < MIN_DEPLOY_ITEMS:
            logger.error("Reconciled digest has %d items (< %d); refusing to deploy.",
                         len(items), MIN_DEPLOY_ITEMS)
            return 2
        site_builder.deploy_to_gh_pages()
    save_last_run_info(week_id)
    return 0


class PipelineBusy(RuntimeError):
    """Another sync/expand holds data/.pipeline.lock."""


def _retire_sync_file(path: Path, why: str) -> None:
    """Move a checkpoint out of the sync_progress_*.json namespace so neither
    this pipeline nor resume_pending.sh can pick it up again. Bytes are kept
    (forensics); only the name changes, so the glob no longer matches."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path.rename(path.with_name(f"{path.name}.retired-{ts}"))
        logger.info("Retired sync progress %s (%s).", path.name, why)
    except OSError as exc:
        logger.warning("Could not retire %s: %s", path, exc)

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


def _trust_warming_active() -> bool:
    """True when pacing should be slowed for a young/low-trust account.

    Explicit TRUST_WARMING=1/0 forces on/off. Otherwise auto: on while the
    account is < 14 days past its first recorded follow burst
    (data/follow_progress.json started_at).
    """
    flag = (config.TRUST_WARMING or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    try:
        prog = json.loads((config.DATA_DIR / "follow_progress.json").read_text(encoding="utf-8"))
        started = prog.get("started_at") or ""
        age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(started)).days
        return age_days < 14
    except Exception:
        return False


def _check_follow_cooldown(force: bool = False) -> bool:
    """Refuse scrape starts inside the post-mass-follow cooldown window.

    A follow-then-scrape burst on a fresh account is the highest-risk
    pattern for a checkpoint challenge. Returns True when the run may
    proceed. Pass force=True (--force) to override explicitly.
    """
    window_h = config.FOLLOW_COOLDOWN_HOURS
    if window_h <= 0 or force:
        return True
    try:
        prog = json.loads((config.DATA_DIR / "follow_progress.json").read_text(encoding="utf-8"))
    except Exception:
        return True
    done = prog.get("done") or []
    started = prog.get("started_at") or ""
    try:
        started_dt = datetime.fromisoformat(started)
    except Exception:
        return True
    age_h = (datetime.now(timezone.utc) - started_dt).total_seconds() / 3600.0
    if len(done) >= config.FOLLOW_BURST_THRESHOLD and age_h < window_h:
        logger.error(
            "Follow cooldown: %d follows %.1fh ago (< %.0fh window). "
            "Refusing scrape to protect the new account; re-run with --force to override.",
            len(done), age_h, window_h,
        )
        return False
    return True


def run_full_sync(
    dry_run: bool = False,
    deploy: bool = False,
    days_back: int = 7,
    limit_per_creator: int = 15,
    since_timestamp: int | None = None,
    resume: bool = False,
) -> int:
    """Execute complete end-to-end extraction, ranking, upload, and deployment pipeline."""
    try:
        with _pipeline_file_lock():
            return _run_full_sync(dry_run, deploy, days_back, limit_per_creator, since_timestamp, resume)
    except PipelineBusy as exc:
        logger.error("%s; refusing to start.", exc)
        return 3


def _run_full_sync(
    dry_run: bool = False,
    deploy: bool = False,
    days_back: int = 7,
    limit_per_creator: int = 15,
    since_timestamp: int | None = None,
    resume: bool = False,
) -> int:
    """Execute complete end-to-end extraction, ranking, upload, and deployment pipeline."""
    week_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if since_timestamp is not None:
        logger.info("Starting Instagram Digest ad-hoc sync for week %s (since_ts=%d, days_back=%d, dry_run=%s)...",
                    week_id, since_timestamp, days_back, dry_run)
    else:
        logger.info("Starting Instagram Digest weekly sync for week %s (days_back=%d, dry_run=%s)...",
                    week_id, days_back, dry_run)

    # 0. Session pre-check FIRST (before the multi-hour agy + scrape work):
    # a dead login must abort in seconds, not after burning a full run.
    # Skipped on dry runs (no network phase to protect) and on --resume
    # (banked work must always be allowed to complete; the resume path
    # revalidates before its own network phase).
    if not dry_run and not resume:
        session_ok = _ensure_valid_session(extractor.InstagramSession())
        if not session_ok:
            _alert_sync_abort("Instagram session invalid",
                              "pre-run validation failed after one cookie refresh")
            return 2

    # 1. R2 connectivity check (read-only). Owner-mandated order defers EVERY
    # R2 mutation until the full digest is prepared locally (Phase 5C+); the
    # rolling purges run post-publish instead of here, so a shortfall abort
    # returns without touching R2 at all.
    if not dry_run and config.R2_ACCOUNT_ID:
        usage_bytes, _ = storage_r2.get_bucket_storage_usage()
        if usage_bytes < 0:
            logger.error("CRITICAL: cannot verify R2 usage (outage?); refusing to start sync.")
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
        except Exception as exc:
            logger.warning("Ignoring unreadable sync progress: %s", exc)
            _quarantine_corrupt(sync_read_path, exc)
            _retire_sync_file(sync_read_path, "unreadable")
        else:
            banked_week = str(loaded_sync.get("week_id") or "") if isinstance(loaded_sync, dict) else ""
            try:
                banked_age_days = (
                    datetime.now(timezone.utc)
                    - datetime.strptime(banked_week, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                ).days
            except ValueError:
                banked_age_days = 10**6
            if (
                isinstance(loaded_sync, dict)
                and loaded_sync.get("version") == 1
                and loaded_sync.get("limit_per_creator") == limit_per_creator
                and loaded_sync.get("since_timestamp") == since_timestamp
                and loaded_sync.get("stage") in RESUMABLE_SYNC_STAGES
                and banked_age_days <= MAX_SYNC_RESUME_AGE_DAYS
            ):
                sync_progress = loaded_sync
                if loaded_sync.get("days_back") != days_back:
                    logger.info(
                        "Sync progress window drifted (banked days_back=%s, now %d); resuming anyway.",
                        loaded_sync.get("days_back"), days_back,
                    )
            else:
                logger.warning(
                    "Ignoring sync progress %s (parameters changed or banked work is %s days old); retiring it.",
                    sync_read_path.name, banked_age_days,
                )
                _retire_sync_file(sync_read_path, "parameters changed or stale")
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

    # AI Recommendation Engine: discover or reload similar creators for Tier 2
    import recommendations
    recommended_creators: list[dict[str, Any]] = []
    if sync_progress and sync_progress.get("recommended_creators"):
        recommended_creators = sync_progress["recommended_creators"]
        logger.info("Reusing %d frozen recommended creators from sync checkpoint.", len(recommended_creators))
    elif not dry_run:
        try:
            logger.info("Discovering AI-recommended creators via agy -p (600s per-category timeout)...")
            recommended_creators = recommendations.refresh_recommendations(timeout_per_category=600)
        except Exception as exc:
            logger.warning("Failed refreshing recommended creators: %s", exc)
            recommended_creators = recommendations.load_recommended_creators()
    else:
        recommended_creators = recommendations.load_recommended_creators()

    all_sources = list(active_sources)
    existing_handles = {s["handle"].lower().replace("@", "") for s in active_sources if "handle" in s}
    for rec in recommended_creators:
        rh = rec.get("handle", "").lower().replace("@", "")
        if rh and rh not in existing_handles:
            all_sources.append({
                "handle": rh,
                "name": rec.get("name") or rh,
                "category": rec.get("category", "entertainment"),
                "enabled": True,
                "is_recommended": True,
            })

    ranked_reels: list[dict[str, Any]] = []
    resume_ranked: list[dict[str, Any]] | None = None
    is_shortfall_resume = False
    banked_paths_map: dict[str, Path] = {}
    banked_urls_map: dict[str, str] = {}

    banked_ranked = [
        r for r in (sync_progress.get("ranked", []) if sync_progress else [])
        if isinstance(r, dict) and r.get("id")
    ]

    if sync_progress and (
        sync_progress.get("stage") == "shortfall_paused"
        or (
            sync_progress.get("stage") in RANKED_SYNC_STAGES
            and len(banked_ranked) < config.TOP_DIGEST_COUNT
        )
    ):
        if banked_ranked:
            is_shortfall_resume = True
            ranked_reels = banked_ranked
            logger.info(
                "Resuming weekly sync with deficit (%d / %d reels banked). Proceeding directly to Tier 3 feed top-up...",
                len(ranked_reels),
                config.TOP_DIGEST_COUNT,
            )
            for rid, pstr in (sync_progress.get("downloaded_paths") or {}).items():
                p = Path(pstr)
                if p.exists():
                    banked_paths_map[rid] = p
            for rid, url in (sync_progress.get("uploaded_url_map") or {}).items():
                if url:
                    banked_urls_map[rid] = url

    elif sync_progress and sync_progress.get("stage") in RANKED_SYNC_STAGES:
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
            # If resuming from shortfall_paused / deficit, skip candidate extraction and ranking completely!
            if is_shortfall_resume:
                deficit = config.TOP_DIGEST_COUNT - len(ranked_reels)
                already_external = sum(1 for r in ranked_reels if r.get("is_external"))
                max_external = int(config.TOP_DIGEST_COUNT * config.MAX_EXTERNAL_SHARE)
                tier3_room = max(0, max_external - already_external)
                tier3_target = min(deficit, tier3_room)
                if tier3_target < deficit:
                    logger.info(
                        "Tier 3 share cap on resume: topping up %d of %d deficit "
                        "(%d/%d external slots used).",
                        tier3_target, deficit, already_external, max_external,
                    )
                if deficit > 0 and not dry_run and tier3_target > 0:
                    logger.info("Tier 3 Top-up: discovering up to %d external reels from feed (max %d evaluations)...",
                                tier3_target, config.MAX_FEED_EVALUATIONS)
                    try:
                        existing_ids = {r["id"] for r in ranked_reels}
                        external_reels = []
                        for feed_attempt in (1, 2):
                            try:
                                external_reels = extractor.extract_external_reels_from_feed(
                                    session=session,
                                    target_count=tier3_target,
                                    existing_ids=existing_ids,
                                    active_sources=all_sources,
                                    max_evaluations=config.MAX_FEED_EVALUATIONS,
                                )
                                break
                            except extractor.InstagramChallenged as challenge_err:
                                logger.error("Instagram challenge during shortfall top-up: %s. "
                                             "Keeping banked reels and aborting.", challenge_err)
                                _alert_sync_abort("instagram challenge-gated", str(challenge_err))
                                try:
                                    import notifier
                                    notifier.send_cookie_alert_email()
                                except Exception as alert_err:
                                    logger.warning("Failed to send cookie alert email: %s", alert_err)
                                try:
                                    local_server.raise_cookie_attention(
                                        pipeline="weekly-sync",
                                        reason="Instagram challenge gate during shortfall top-up",
                                    )
                                except Exception as popup_err:
                                    logger.warning("Failed raising cookie attention popup: %s", popup_err)
                                session.close()
                                return 2
                            except extractor.CookieExpiredException as exc:
                                if "/accounts/login" in str(exc) or "login_required" in str(exc):
                                    break
                                if feed_attempt == 1:
                                    logger.warning("Rate limit during feed top-up; sleeping %d min...", FEED_RETRY_WAIT_MIN)
                                    time.sleep(FEED_RETRY_WAIT_MIN * 60)
                        if external_reels:
                            logger.info("Discovered %d external reels from feed for top-up.", len(external_reels))
                            combined = ranked_reels + external_reels
                            for idx, r in enumerate(combined, 1):
                                r["rank"] = idx
                                r["rank_display"] = f"#{idx:02d}"
                            ranked_reels = combined
                    except Exception as topup_err:
                        logger.warning("Error during shortfall top-up: %s", topup_err)
            else:
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
                if candidates_cache_file.exists() and not candidates:
                    # `not candidates` keeps banked resume candidates in charge:
                    # they are fresher (the run that wrote them was in flight),
                    # and the resume path continues extraction to completion
                    # anyway, so the cache can never improve on them.
                    try:
                        if time.time() - candidates_cache_file.stat().st_mtime < 12 * 3600:
                            cached = json.loads(candidates_cache_file.read_text(encoding="utf-8"))
                            cached_items = (
                                cached.get("candidates") if isinstance(cached, dict) else None
                            )
                            params_match = (
                                isinstance(cached, dict)
                                and cached.get("version") == 1
                                and cached.get("since_timestamp") == since_timestamp
                                and cached.get("days_back") == days_back
                                and cached.get("limit_per_creator") == limit_per_creator
                            )
                            if isinstance(cached_items, list) and params_match:
                                candidates = [
                                    c for c in cached_items
                                    if isinstance(c, dict) and c.get("id")
                                ]
                                logger.info(
                                    "Loaded %d candidate reels from fresh candidates_cache.json.",
                                    len(candidates),
                                )
                                cache_hit = True
                            elif isinstance(cached, list):
                                logger.info(
                                    "Ignoring legacy candidates_cache.json (no run parameters); re-extracting."
                                )
                            else:
                                logger.info(
                                    "Ignoring candidates_cache.json (run parameters changed)."
                                )
                    except Exception as exc:
                        # PY-P2-2: quarantine corrupt bytes so every retry stops
                        # failing identically on the same torn file.
                        _quarantine_corrupt(candidates_cache_file, exc)
                        try:
                            candidates_cache_file.unlink(missing_ok=True)
                        except OSError:
                            pass
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
                                except extractor.InstagramChallenged:
                                    # Never backoff-sleep a challenge: re-raise
                                    # at once for the instant-abort handler.
                                    raise
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
                        # Challenge-gated accounts abort instantly (no backoff
                        # sleeps: they never heal by waiting and grinding risks
                        # the account). Plain login/rate-limit paths keep the
                        # existing backoff behavior below.
                        if isinstance(exc, extractor.InstagramChallenged):
                            logger.error("Instagram challenge on @%s (%s). "
                                         "Aborting run with banked progress.", handle, exc)
                            _write_sync_progress("extracting", {
                                "done": done_map, "candidates": candidates,
                                "extraction_complete": False,
                                "total_sources": extraction_total,
                            })
                            _alert_sync_abort("instagram challenge-gated", str(exc))
                            try:
                                import notifier
                                notifier.send_cookie_alert_email()
                            except Exception as alert_err:
                                logger.warning("Failed to send cookie alert email: %s", alert_err)
                            try:
                                local_server.raise_cookie_attention(
                                    pipeline="weekly-sync",
                                    reason="Instagram challenge gate during creator extraction",
                                )
                            except Exception as popup_err:
                                logger.warning("Failed raising cookie attention popup: %s", popup_err)
                            return 2
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
                        # PY-P1-5: retire banked work instead of deleting it. A
                        # transient empty-grid/soft-block (0 candidates) must not
                        # destroy done_map/candidates a retry could resume; the
                        # retire renames out of the sync_progress_*.json namespace
                        # so neither this pipeline nor resume_pending.sh picks it
                        # up, while bytes stay available for forensics.
                        for hopeless in {sync_checkpoint, sync_read_path}:
                            try:
                                if hopeless.exists():
                                    _retire_sync_file(hopeless, "viability gate failed")
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
                        atomic_io.durable_write_json(candidates_cache_file, {
                            "version": 1,
                            "since_timestamp": since_timestamp,
                            "days_back": days_back,
                            "limit_per_creator": limit_per_creator,
                            "written_at": time.time(),
                            "candidates": candidates,
                        })
                    except Exception:
                        pass
                    _write_sync_progress("extracting", {
                        "done": done_map, "candidates": candidates,
                        "extraction_complete": True,
                        "total_sources": extraction_total,
                        "recommended_creators": recommended_creators,
                    })

                # Tier 2: Recommended Creators (up to 8 reels per creator, pinned + unpinned)
                if recommended_creators:
                    logger.info("Tier 2: Extracting candidate reels for %d recommended creators...", len(recommended_creators))
                    rec_by_cat: dict[str, list[dict[str, Any]]] = {}
                    for rec in recommended_creators:
                        c = rec.get("category", "entertainment")
                        rec_by_cat.setdefault(c, []).append(rec)
                    ordered_recs: list[dict[str, Any]] = []
                    rec_max_len = max((len(rec_by_cat.get(c, [])) for c in cats), default=0)
                    for i in range(rec_max_len):
                        for c in cats:
                            if i < len(rec_by_cat.get(c, [])):
                                ordered_recs.append(rec_by_cat[c][i])

                    for rec in ordered_recs:
                        h = rec.get("handle", "")
                        if not h or h in done_map:
                            continue
                        # Tier 2 must pull its weight: the pool-size stop only
                        # applies once Tier 1+2 candidates can plausibly clear
                        # the 150 deploy floor after date-filtering +
                        # enrichment attrition (~50% => 2x floor cover banked).
                        floor_cover = MIN_DEPLOY_ITEMS * 2
                        if len(candidates) >= max(config.TOP_DIGEST_COUNT * 2, floor_cover) \
                                and visited_this_run >= 18:
                            logger.info("Candidate pool reached %d; stopping Tier 2 recommended discovery.", len(candidates))
                            break
                        try:
                            logger.info("Tier 2: Extracting reels for recommended @%s (%s)...", h, rec.get("category", ""))
                            rec_reels = extractor.extract_creator_reels(
                                handle=h,
                                max_reels=8,
                                days_back=days_back,
                                fast_mode=True,
                                session=session,
                                include_pinned=True,
                            )
                            done_map[h] = not rec_reels
                            visited_this_run += 1
                            for r in rec_reels:
                                r["is_recommended"] = True
                            candidates.extend(rec_reels)
                            mu, sigma, floor = CREATOR_PAUSE
                            extractor.human_pause(mu=mu, sigma=sigma, floor=floor)
                        except extractor.InstagramChallenged as challenge_err:
                            # Same instant-abort as Tier 1: a gated account
                            # must stop now, not grind 50 more creators.
                            logger.error("Instagram challenge on recommended @%s (%s). "
                                         "Aborting run with banked progress.", h, challenge_err)
                            _write_sync_progress("extracting", {
                                "done": done_map, "candidates": candidates,
                                "extraction_complete": False,
                                "total_sources": extraction_total,
                            })
                            _alert_sync_abort("instagram challenge-gated", str(challenge_err))
                            try:
                                import notifier
                                notifier.send_cookie_alert_email()
                            except Exception as alert_err:
                                logger.warning("Failed to send cookie alert email: %s", alert_err)
                            try:
                                local_server.raise_cookie_attention(
                                    pipeline="weekly-sync",
                                    reason="Instagram challenge gate during Tier 2 extraction",
                                )
                            except Exception as popup_err:
                                logger.warning("Failed raising cookie attention popup: %s", popup_err)
                            return 2
                        except Exception as rec_err:
                            logger.warning("Tier 2 extraction error on @%s: %s", h, rec_err)

                # Combine active sources with recommended creators and build per-creator caps
                all_sources = list(active_sources)
                existing_handles = {s["handle"].lower().replace("@", "") for s in active_sources if "handle" in s}
                for rec in recommended_creators:
                    rh = rec.get("handle", "").lower().replace("@", "")
                    if rh and rh not in existing_handles:
                        all_sources.append({
                            "handle": rh,
                            "name": rec.get("name") or rh,
                            "category": rec.get("category", "entertainment"),
                            "enabled": True,
                            "is_recommended": True,
                        })

                caps_map: dict[str, int] = {}
                for s in active_sources:
                    h = s.get("handle", "").lower().replace("@", "")
                    if h:
                        caps_map[h] = config.MAX_PER_CREATOR
                for rec in recommended_creators:
                    h = rec.get("handle", "").lower().replace("@", "")
                    if h:
                        caps_map[h] = 8

                # 4. Two-Pass Selection & Ranking (C3):
                # Pass 1: Cheap reach-only ranking to shortlist (2x top digest size)
                if banked_shortlist is not None:
                    shortlist = banked_shortlist
                    logger.info("Reusing %d banked shortlist reels from sync progress.", len(shortlist))
                else:
                    shortlist = ranker.rank_top_reels(
                        candidates=candidates,
                        sources=all_sources,
                        top_n=config.TOP_DIGEST_COUNT * 2,
                        max_per_creator={h: cap + 2 for h, cap in caps_map.items()},
                        shuffle=False,
                    )

                # Enrichment, media-API-first: one cheap media/{id}/info/ GET
                # per shortlist reel returns timestamp + metrics + video URL
                # AND applies the date cutoff up front, so stale reels never
                # burn a ~7s Playwright visit. Only API misses fall through to
                # the per-reel browser path below.
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
                    logger.info("Enriching shortlist of %d reels via media-info API first (cutoff_ts=%s)...", len(shortlist), cutoff_ts)

                api_prefiltered: list[dict[str, Any]] = todo
                if todo and not dry_run:
                    try:
                        api_prefiltered = extractor.enrich_candidates_via_media_api(
                            todo, cutoff_timestamp=cutoff_ts)
                        logger.info("media-info pre-filter: %d/%d shortlist reels fresh with full metadata.",
                                    len(api_prefiltered), len(todo))
                    except extractor.InstagramChallenged as challenge_err:
                        # Account is gated mid-run: bank everything and abort
                        # NOW. No per-reel fallback (it would grind a locked
                        # account for hours), no retry.
                        logger.error("Instagram challenge during enrichment: %s. "
                                     "Banking work and aborting.", challenge_err)
                        _write_sync_progress("enriched", {
                            "candidates": candidates,
                            "shortlist": shortlist,
                            "enriched": list(enriched_by_id.values()),
                            "extraction_complete": True,
                            "recommended_creators": recommended_creators,
                        })
                        _alert_sync_abort("instagram challenge-gated",
                                          f"{challenge_err}")
                        try:
                            import notifier
                            notifier.send_cookie_alert_email()
                        except Exception as alert_err:
                            logger.warning("Failed to send cookie alert email: %s", alert_err)
                        try:
                            local_server.raise_cookie_attention(
                                pipeline="weekly-sync",
                                reason="Instagram challenge gate during enrichment; clear it in Chrome",
                            )
                        except Exception as popup_err:
                            logger.warning("Failed raising cookie attention popup: %s", popup_err)
                        session.close()
                        return 2
                    except Exception as api_err:
                        logger.warning("media-info pre-filter failed, falling back to per-reel: %s", api_err)
                        api_prefiltered = todo
                for r in api_prefiltered:
                    if r.get("id") and (r.get("timestamp") or enriched_by_id.get(r.get("id"), {}).get("timestamp")):
                        enriched_by_id[r["id"]] = r
                still_missing = [r for r in todo if not enriched_by_id.get(r.get("id"), {}).get("timestamp")]

                newly_enriched = 0
                if still_missing:
                    # Per-reel browser fallback, API misses only.
                    # Serial enrichment contract (max_workers=ENRICH_WORKERS == 1):
                    # Reuse the existing authenticated `session` directly on the main thread.
                    def _enrich_item(r: dict[str, Any]) -> dict[str, Any] | None:
                        try:
                            mu, sigma, floor = ENRICH_PAUSE
                            if not dry_run:
                                extractor.human_pause(mu=mu, sigma=sigma, floor=floor)
                            m = extractor.extract_single_reel_metadata(r, session=session)
                            if not m:
                                return None
                            # Pinned reels are exempt from cutoff date
                            if not m.get("is_pinned") and (m.get("timestamp") or 0) < cutoff_ts:
                                return None
                            return m
                        except extractor.InstagramChallenged:
                            raise
                        except Exception as exc:
                            logger.debug("Enrichment error on reel %s: %s", r.get("id"), exc)
                            return None

                    challenged_reel = None
                    for r in still_missing:
                        try:
                            res = _enrich_item(r)
                            if res and res.get("id"):
                                enriched_by_id[res["id"]] = res
                                newly_enriched += 1
                                if newly_enriched % 25 == 0:
                                    _write_sync_progress("enriched", {
                                        "candidates": candidates,
                                        "shortlist": shortlist,
                                        "enriched": list(enriched_by_id.values()),
                                        "extraction_complete": True,
                                        "recommended_creators": recommended_creators,
                                    })
                        except extractor.InstagramChallenged as challenge_err:
                            challenged_reel = challenge_err
                            break
                        except Exception as exc:
                            logger.debug("Enrichment iteration error: %s", exc)
                    if challenged_reel is not None:
                        logger.error("Instagram challenge during per-reel enrichment: %s. "
                                     "Banking work and aborting.", challenged_reel)
                        _write_sync_progress("enriched", {
                            "candidates": candidates,
                            "shortlist": shortlist,
                            "enriched": list(enriched_by_id.values()),
                            "extraction_complete": True,
                            "recommended_creators": recommended_creators,
                        })
                        _alert_sync_abort("instagram challenge-gated", str(challenged_reel))
                        try:
                            import notifier
                            notifier.send_cookie_alert_email()
                        except Exception as alert_err:
                            logger.warning("Failed to send cookie alert email: %s", alert_err)
                        try:
                            local_server.raise_cookie_attention(
                                pipeline="weekly-sync",
                                reason="Instagram challenge gate during per-reel enrichment",
                            )
                        except Exception as popup_err:
                            logger.warning("Failed raising cookie attention popup: %s", popup_err)
                        session.close()
                        return 2

                enriched = [enriched_by_id[rid] for rid in shortlist_ids if rid in enriched_by_id]
                logger.info("Enriched %d valid reels within date window out of %d candidates.", len(enriched), len(shortlist))
                _write_sync_progress("enriched", {
                    "candidates": candidates,
                    "shortlist": shortlist,
                    "enriched": enriched,
                    "extraction_complete": True,
                    "recommended_creators": recommended_creators,
                })

                # Pass 2: Final ranking on enriched candidates only.
                # Cross-week dedup first: drop reels published in a recent
                # digest so they cannot resurface week after week.
                enriched = _filter_seen_reel_ids(enriched)
                ranked_reels = ranker.rank_top_reels(
                    candidates=enriched,
                    sources=all_sources,
                    top_n=config.TOP_DIGEST_COUNT,
                    max_per_creator=caps_map,
                )

                # Pass 3: External Reels Discovery (Tier 3). Capped by share:
                # externals are lower-signal by design (estimated views,
                # timestamp=now), and long discovery-feed scrolls are the
                # highest-risk surface on a fresh account. Tier 3 fills at
                # most MAX_EXTERNAL_SHARE of the digest and stops after
                # MAX_FEED_EVALUATIONS evals; the rest stays a deficit for
                # shortfall_paused + resume instead of a grind.
                deficit = config.TOP_DIGEST_COUNT - len(ranked_reels)
                max_external = int(config.TOP_DIGEST_COUNT * config.MAX_EXTERNAL_SHARE)
                tier3_target = min(deficit, max_external)
                if tier3_target < deficit:
                    logger.info(
                        "Tier 3 share cap: filling %d of %d deficit (max %.0f%% externals); "
                        "remainder stays a deficit for resume.",
                        tier3_target, deficit, config.MAX_EXTERNAL_SHARE * 100,
                    )
                if deficit > 0 and not dry_run and tier3_target > 0:
                    logger.info(
                        "Channels produced %d reels (%d below target %d). Discovering up to %d external high-signal reels from feed (eval cap %d)...",
                        len(ranked_reels), deficit, config.TOP_DIGEST_COUNT,
                        tier3_target, config.MAX_FEED_EVALUATIONS,
                    )
                    try:
                        existing_ids = {r["id"] for r in ranked_reels}
                        external_reels = []
                        feed_blocked = None
                        for feed_attempt in (1, 2):
                            try:
                                external_reels = extractor.extract_external_reels_from_feed(
                                    session=session,
                                    target_count=tier3_target,
                                    existing_ids=existing_ids,
                                    active_sources=all_sources,
                                    max_evaluations=config.MAX_FEED_EVALUATIONS,
                                )
                                feed_blocked = None
                                break
                            except extractor.InstagramChallenged as challenge_err:
                                # Gated account: abort at once, no retry sleep.
                                logger.error("Instagram challenge during Tier 3 discovery: %s. "
                                             "Banking channel reels and aborting.", challenge_err)
                                _write_sync_progress("enriched", {
                                    "candidates": candidates,
                                    "shortlist": shortlist, "enriched": enriched,
                                    "extraction_complete": True,
                                    "total_sources": extraction_total,
                                    "recommended_creators": recommended_creators,
                                })
                                _alert_sync_abort("instagram challenge-gated", str(challenge_err))
                                try:
                                    import notifier
                                    notifier.send_cookie_alert_email()
                                except Exception as alert_err:
                                    logger.warning("Failed to send cookie alert email: %s", alert_err)
                                try:
                                    local_server.raise_cookie_attention(
                                        pipeline="weekly-sync",
                                        reason="Instagram challenge gate during Tier 3 discovery",
                                    )
                                except Exception as popup_err:
                                    logger.warning("Failed raising cookie attention popup: %s", popup_err)
                                session.close()
                                return 2
                            except extractor.CookieExpiredException as exc:
                                feed_blocked = exc
                                if "/accounts/login" in str(exc) or "login_required" in str(exc):
                                    break
                                if feed_attempt == 1:
                                    logger.warning(
                                        "Rate limit during feed discovery; sleeping %d min, then one retry...",
                                        FEED_RETRY_WAIT_MIN,
                                    )
                                    _write_sync_progress("cooling_down", {
                                        "done": done_map, "candidates": candidates,
                                        "shortlist": shortlist, "enriched": enriched,
                                        "extraction_complete": True,
                                        "total_sources": extraction_total,
                                        "blocked_handle": "__feed__",
                                        "resumes_in_min": FEED_RETRY_WAIT_MIN,
                                        "recommended_creators": recommended_creators,
                                    })
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
                _write_sync_progress("ranked", {
                    "ranked": ranked_reels,
                    "recommended_creators": recommended_creators,
                })
            elif not dry_run:
                # Hopeless run: drop staged progress so the next attempt starts fresh.
                for hopeless in {sync_checkpoint, sync_read_path}:
                    try:
                        hopeless.unlink(missing_ok=True)
                    except OSError:
                        pass

    if not ranked_reels:
        logger.error("No reels qualified for Top Digest. Aborting run without touching digest/site.")
        _alert_sync_abort("no qualifying reels", "Top Digest selection came back empty")
        return 2

    # 5. Media Download (local-only), Byte-Budget + Shortfall Gates, Scoped JIT
    # Purge, then R2 Upload. Owner-mandated order: no R2 mutation before the
    # shortfall gate — the full digest must be playable on local disk first.
    uploaded_url_map: dict[str, str] = dict(banked_urls_map)
    if not dry_run:
        week_videos_dir = config.VIDEOS_DIR / week_id
        week_videos_dir.mkdir(parents=True, exist_ok=True)

        # 5A: Download all ranked reels to local disk first
        logger.info("Phase 5A: Downloading Top %d reels to local disk...", len(ranked_reels))

        def download_reel(reel: dict[str, Any]) -> tuple[str, Path | None]:
            reel_id = _safe_component(reel["id"], "")
            handle = _safe_component(reel["creator_handle"], "creator")
            rank = reel.get("rank", 1)
            if not reel_id:
                logger.warning("Skipping reel with unusable id %r", reel.get("id"))
                return (str(reel.get("id")), None)
            # Filenames are URL-encoded: reel IDs carry '-'/'_' and handles
            # carry '.' that glob + R2 key matching mishandle raw.
            from urllib.parse import quote as _quote
            reel_id_enc = _quote(reel_id, safe="")
            handle_enc = _quote(handle, safe="")
            filename = f"{rank:02d}_{handle_enc}_{reel_id_enc}.mp4"
            local_video_path = week_videos_dir / filename

            # Check if this reel was already downloaded under a previous rank prefix
            existing_matches = list(week_videos_dir.glob(f"*_{handle_enc}_{reel_id_enc}.mp4")) or \
                list(week_videos_dir.glob(f"*_{handle}_{reel_id}.mp4")) or \
                list(week_videos_dir.glob(f"*_{reel_id}.mp4"))
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
                    return (reel_id, None)

            return (reel_id, local_video_path)

        downloaded_paths: dict[str, Path] = {
            rid: p for rid, p in banked_paths_map.items() if p.exists()
        }
        to_download = [r for r in ranked_reels if r["id"] not in downloaded_paths]
        if downloaded_paths:
            logger.info("Reusing %d already downloaded reels from banked progress.", len(downloaded_paths))

        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_id = {executor.submit(download_reel, r): r["id"] for r in to_download}
            download_done = 0
            for future in as_completed(future_to_id):
                try:
                    rid, pth = future.result()
                    if pth and pth.exists():
                        downloaded_paths[rid] = pth
                except Exception as exc:
                    rid = future_to_id[future]
                    logger.warning("Worker error downloading reel %s: %s", rid, exc)
                download_done += 1

        # Drop reels whose downloads failed
        ranked_reels = [r for r in ranked_reels if r["id"] in downloaded_paths]

        # 5B: 5.8 GB Byte-Budget Guard
        # If reels balloon in size (e.g. 4K/high bitrate), cap the batch at the highest-scoring
        # viral reels that fit within the 6.0 GB R2 headroom (5.8 GB limit leaves 200 MB margin).
        max_feed_bytes = getattr(config, "MAX_FEED_BATCH_BYTES", int(5.8 * 1024 * 1024 * 1024))
        budgeted_reels: list[dict[str, Any]] = []
        total_batch_bytes = 0
        budget_capped = False
        for r in ranked_reels:
            fpath = downloaded_paths.get(r["id"])
            fsize = fpath.stat().st_size if fpath and fpath.exists() else 0
            if total_batch_bytes + fsize > max_feed_bytes and len(budgeted_reels) >= MIN_DEPLOY_ITEMS:
                logger.warning(
                    "Byte budget reached: capping digest at %d reels (%.1f MB / max %.1f MB) to protect 6 GB R2 headroom.",
                    len(budgeted_reels), total_batch_bytes / (1024 * 1024), max_feed_bytes / (1024 * 1024)
                )
                budget_capped = True
                break
            total_batch_bytes += fsize
            budgeted_reels.append(r)

        ranked_reels = budgeted_reels

        # 5C: Shortfall-preservation gate — BEFORE any R2 mutation. If final
        # playable (locally downloaded) reels are under MIN_DEPLOY_ITEMS,
        # never touch R2 and never overwrite the healthy live digest.
        # Downloads are local-only so this abort is side-effect free on the
        # remote; all banked work is checkpointed as shortfall_paused for
        # resume / feed top-up.
        prev_count = _digest_item_count()
        if not ranked_reels or (
            (prev_count >= MIN_DEPLOY_ITEMS or deploy) and len(ranked_reels) < MIN_DEPLOY_ITEMS
        ):
            logger.error(
                "Only %d playable reels (previous digest: %d, minimum %d required); "
                "preserving work as shortfall_paused checkpoint.",
                len(ranked_reels), prev_count, MIN_DEPLOY_ITEMS,
            )
            _write_sync_progress("shortfall_paused", {
                "ranked": ranked_reels,
                "recommended_creators": recommended_creators,
                "downloaded_paths": {rid: str(p) for rid, p in downloaded_paths.items() if p.exists()},
                "uploaded_url_map": uploaded_url_map,
                "deficit": config.TOP_DIGEST_COUNT - len(ranked_reels),
            })
            _alert_sync_abort(
                "digest shortfall paused",
                f"only {len(ranked_reels)} playable reels (minimum {MIN_DEPLOY_ITEMS} required) - checkpoint preserved for resume",
            )
            return 2

        # 5D: JIT purge previous weeks FIRST (scoped: everything under videos/
        # EXCEPT videos/<current_week_id>/ and the live-digest week), then the
        # pre-flight quota check against the real batch size. Purging first
        # makes the quota math current_R2_usage(excluding purged weeks) +
        # batch_bytes < quota. Current-week strays from partial/interrupted
        # uploads are deleted before upload too (keyed by reel-id suffix, same
        # style as purge_unreferenced_r2_videos but scoped to the new week,
        # which no saved digest references yet).
        live_week = _persisted_digest_week()
        keep_weeks = {live_week} if live_week and live_week != week_id else set()
        if config.R2_ACCOUNT_ID:
            storage_r2.purge_previous_weeks_videos(
                current_week_id=week_id, keep_week_ids=keep_weeks)
            _purge_current_week_stray_r2_keys(
                week_id, {str(r["id"]) for r in ranked_reels if r.get("id")})
            if not storage_r2.check_preflight_quota(estimated_new_bytes=total_batch_bytes):
                logger.error("Pre-flight quota check failed after JIT purge. Aborting to protect Cloudflare free limits.")
                _alert_sync_abort("r2 quota exceeded", f"batch {total_batch_bytes} bytes exceeds remaining quota")
                return 1

        # 5E: Parallel R2 Upload Phase
        existing_r2_keys = storage_r2.get_existing_r2_keys(f"videos/{week_id}/")
        logger.info("Phase 5E: Syncing %d reels (%.1f MB) to Cloudflare R2...", len(ranked_reels), total_batch_bytes / (1024 * 1024))

        def upload_reel(reel: dict[str, Any]) -> tuple[str, str]:
            reel_id = reel["id"]
            if reel_id in uploaded_url_map and uploaded_url_map[reel_id]:
                return (reel_id, uploaded_url_map[reel_id])
            local_video_path = downloaded_paths.get(reel_id)
            if not local_video_path or not local_video_path.exists():
                return (reel_id, "")
            filename = local_video_path.name
            public_url = storage_r2.upload_reel_to_r2(
                local_video_path,
                week_id=week_id,
                key_name=filename,
                existing_keys=existing_r2_keys,
            )
            return (reel_id, public_url)

        to_upload = [r for r in ranked_reels if r["id"] not in uploaded_url_map or not uploaded_url_map[r["id"]]]
        if uploaded_url_map:
            logger.info("Reusing %d already uploaded reels from banked progress.", len([r for r in ranked_reels if r["id"] in uploaded_url_map]))

        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_id = {executor.submit(upload_reel, r): r["id"] for r in to_upload}
            published_done = len(ranked_reels) - len(to_upload)
            for future in as_completed(future_to_id):
                try:
                    rid, url = future.result()
                    if url:
                        uploaded_url_map[rid] = url
                except Exception as exc:
                    rid = future_to_id[future]
                    logger.warning("Worker error uploading reel %s: %s", rid, exc)
                published_done += 1
                _write_sync_progress("publishing", {
                    "ranked": ranked_reels,
                    "published": published_done,
                    "published_total": len(ranked_reels),
                    "recommended_creators": recommended_creators,
                    "downloaded_paths": {rid: str(p) for rid, p in downloaded_paths.items() if p.exists()},
                    "uploaded_url_map": uploaded_url_map,
                })

        # Drop unplayable reels (C2). Downloads were already filtered before
        # the shortfall gate, so this post-upload filter should be ~empty —
        # it only catches reels whose upload itself failed. Those are NOT
        # discarded: they go to the persistent upload outbox
        # (data/upload_outbox_<week>.json) so --reconcile can finish them
        # later without re-scraping anything.
        dropped = [r["id"] for r in ranked_reels if r["id"] not in uploaded_url_map]
        if dropped:
            logger.warning("Parking %d upload-failed reels in the outbox (not dropping): %s",
                           len(dropped), ", ".join(dropped))
            try:
                _write_upload_outbox(
                    week_id,
                    [r for r in ranked_reels if r["id"] not in uploaded_url_map],
                    {rid: str(p) for rid, p in downloaded_paths.items() if p.exists()},
                )
            except Exception as ob_err:
                logger.warning("Failed writing upload outbox: %s", ob_err)
            ranked_reels = [r for r in ranked_reels if r["id"] in uploaded_url_map]

        # Persist the real object URL so later expansions never derive keys
        # from the calendar day they run on (week-drift fix).
        for r in ranked_reels:
            if r.get("id") in uploaded_url_map:
                r["r2_url"] = r["video_url"] = uploaded_url_map[r["id"]]

        # 6. Save digest batch payload (only playable reels saved!).
        # Cross-week dedup ledger: record these reel IDs as seen for 30 days
        # so a reel that misses one week's cut cannot resurface next week.
        # Empty-save guard: if NOTHING is playable (total upload outage),
        # preserve the healthy live digest instead of clobbering it with
        # zero items — the outbox above already parked everything.
        if not ranked_reels:
            logger.error(
                "Zero playable reels after upload phase (previous digest: %d items); "
                "preserving live digest, outbox holds %d for --reconcile.",
                _digest_item_count(), len(dropped),
            )
            _write_sync_progress("shortfall_paused", {
                "ranked": [],
                "recommended_creators": recommended_creators,
                "downloaded_paths": {rid: str(p) for rid, p in downloaded_paths.items() if p.exists()},
                "uploaded_url_map": uploaded_url_map,
                "deficit": config.TOP_DIGEST_COUNT,
            })
            _alert_sync_abort(
                "zero playable reels",
                f"all {len(dropped)} uploads failed - outbox preserved for reconcile",
            )
            return 2
        try:
            _record_seen_reel_ids(ranked_reels)
        except Exception as dedup_err:
            logger.warning("Failed recording seen-reel ledger: %s", dedup_err)
        extra_manifest = {"budget_capped": True} if budget_capped else None
        ranker.save_digest_batch(ranked_reels, run_date=week_id, extra_manifest=extra_manifest)

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

        # Staged work is now in the digest: clear BOTH the current-week file and
        # the older-day file this run resumed from. Leaving the latter behind made
        # every later run re-resume it and resume_pending.sh start a full
        # sync+deploy at every login.
        for done_file in {sync_checkpoint, sync_read_path}:
            try:
                done_file.unlink(missing_ok=True)
            except OSError:
                pass

        # 7. Execute 14-Day Rolling Purge (both R2 and local disk)
        storage_r2.purge_expired_r2_objects(max_age_days=config.RETENTION_DAYS)
        storage_r2.purge_unreferenced_r2_videos()
        storage_r2.purge_expired_local_videos(max_age_days=config.RETENTION_DAYS)
    else:
        # Dry-run preview only: never persist. Saving here would clobber the
        # live digest (data/top100_digest.json + data/digests/<week>.json)
        # that the dashboard serves and that --build-only/--deploy read.
        logger.info(
            "Dry-run complete: %d reels ranked; live digest left untouched.",
            len(ranked_reels),
        )

    if dry_run:
        # Dry-run contract: zero mutations. build_site() prunes share pages /
        # thumbnails outside the passed set and rewrites data.json + archives,
        # so compiling here would destroy live assets for a mere preview.
        logger.info("Dry-run: skipping site compile; live site left untouched.")
        logger.info("Sync completed successfully! (dry-run, no artifacts written)")
        return 0

    # 8. Compile Variant 1A Static Viewer Site
    r2_index, local_index = site_builder.build_site(
        digest_data={"run_date": week_id, "items": ranked_reels},
        r2_uploaded_urls=uploaded_url_map if not dry_run else None,
    )

    # 9. Deploy to GitHub Pages (Viability & Minimum items gate)
    if deploy and not dry_run:
        site_builder.deploy_to_gh_pages()

    if not dry_run:
        save_last_run_info(week_id, since_timestamp=since_timestamp)
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
    try:
        with _pipeline_file_lock():
            return _run_expand(target_count, deploy)
    except PipelineBusy as exc:
        logger.error("%s; refusing to start.", exc)
        return 3


def _run_expand(target_count: int = 100, deploy: bool = False) -> int:
    """
    Expand active digest by discovering N extra reels from the Reels feed.
    Preserves all existing active reels in data/top100_digest.json and R2.
    Downloads and uploads ONLY the new reels, re-ranks, rebuilds site, and deploys if requested.
    """
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

    # An expansion belongs to the digest's week, not the calendar day it runs
    # on: R2 keys, local files, checkpoints and the pruner are all keyed on
    # run_date. Deriving from today re-points every existing reel at a key
    # that does not exist when +100 runs on a later UTC day than the sync.
    week_id = digest_data.get("run_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("Starting +%d reel expansion for week %s (deploy=%s)...", target_count, week_id, deploy)

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
        except Exception as exc:
            _quarantine_corrupt(path, exc)
            return []
        raw = data.get("reels") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        return [r for r in raw if isinstance(r, dict) and r.get("id")]

    resumed: list[dict[str, Any]] = _read_checkpoint(read_path) if read_path.exists() else []
    if read_path.exists():
        if resumed:
            # Drop banked reels an intervening sync already integrated so a
            # resume cannot append a duplicate id to the manifest.
            before = len(resumed)
            resumed = [r for r in resumed if r.get("id") not in existing_ids]
            if len(resumed) != before:
                logger.info(
                    "Filtered %d checkpoint reel(s) already in the digest.",
                    before - len(resumed),
                )
            if resumed:
                logger.info(
                    "Resuming +%d expansion from checkpoint: %d reels already discovered.",
                    target_count, len(resumed),
                )
            else:
                logger.info("Checkpoint reels already integrated; discovering fresh reels.")
        else:
            logger.warning("Ignoring unreadable expand checkpoint: %s", read_path.name)
            # _read_checkpoint already quarantined corrupt bytes; remove the
            # unreadable file so it cannot be re-read as empty progress.
            try:
                if not list(config.DATA_DIR.glob(f"{read_path.name}.corrupt-*")):
                    _quarantine_corrupt(read_path, ValueError("unreadable checkpoint"))
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
    from urllib.parse import quote as _urlquote
    for item in existing_items:
        rid = item.get("id")
        if rid:
            r2_url = item.get("r2_url") or item.get("video_url") or (
                f"{config.R2_PUBLIC_DOMAIN}/videos/{week_id}/{item.get('rank', 1):02d}_"
                f"{_urlquote(_safe_component(item.get('creator_handle'), 'creator'), safe='')}_"
                f"{_urlquote(_safe_component(rid, 'reel'), safe='')}.mp4"
            )
            uploaded_url_map[rid] = r2_url

    def _pending_path(reel: dict[str, Any]) -> Path:
        return week_videos_dir / (
            f"_pending_{_urlquote(_safe_component(reel['creator_handle'], 'creator'), safe='')}_"
            f"{_urlquote(_safe_component(reel['id'], 'reel'), safe='')}.mp4"
        )

    def _final_name(reel: dict[str, Any], rank_num: int) -> str:
        return (f"{rank_num:02d}_{_urlquote(_safe_component(reel['creator_handle'], 'creator'), safe='')}_"
                f"{_urlquote(_safe_component(reel['id'], 'reel'), safe='')}.mp4")

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

    # Restore discovery order, then assign ranks after the highest existing
    # rank. The sync drops unplayables without renumbering, so len()+1 can
    # collide with a surviving rank (e.g. ranks [1,2,4] + len-based 4).
    order = {id(r): i for i, r in enumerate(external_reels)}
    downloaded.sort(key=lambda r: order.get(id(r), 0))
    _base_rank = max([int(i.get("rank") or 0) for i in existing_items] + [len(existing_items)])
    for offset, reel in enumerate(downloaded):
        rank_num = _base_rank + 1 + offset
        reel["rank"] = rank_num
        reel["rank_display"] = f"#{rank_num:02d}"
        filename = _final_name(reel, rank_num)
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
        rank_num = _base_rank + 1 + offset
        if reel["rank"] != rank_num:
            old = week_videos_dir / _final_name(reel, reel["rank"])
            new = week_videos_dir / _final_name(reel, rank_num)
            try:
                old.rename(new)
            except OSError:
                pass
            reel["rank"] = rank_num
            reel["rank_display"] = f"#{rank_num:02d}"

    def upload_new_reel(reel: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        filename = _final_name(reel, reel["rank"])
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

    # 5. Deploy to GitHub Pages if requested (same viability gate as sync:
    # never push a shrunken digest over a healthy one).
    if deploy:
        if len(combined_items) < MIN_DEPLOY_ITEMS:
            logger.error(
                "Only %d reels after expansion (minimum %d required); "
                "refusing to deploy over previous digest.",
                len(combined_items), MIN_DEPLOY_ITEMS,
            )
            _clear_expand_progress()
            return 2
        site_builder.deploy_to_gh_pages()

    _clear_expand_progress()
    return 0


def _estimate_run(since_ts_override: int | None = None) -> int:
    """Read-only pre-flight estimate: project R2 usage for the next digest.

    Sizes the upcoming batch from storage_r2.estimate_weekly_batch_bytes
    (most recent real week dir + 10% headroom) and compares against current
    R2 usage and the 8 GB safety quota, accounting for the JIT purge of
    previous weeks that runs before upload. Never downloads, uploads,
    purges, or writes anything. Prints the projection and returns 0 when
    the batch fits, 1 when it would exceed quota.
    """
    batch_bytes = storage_r2.estimate_weekly_batch_bytes()
    current_bytes, count = storage_r2.get_bucket_storage_usage()
    if current_bytes < 0:
        logger.error("Cannot verify R2 usage (outage?); estimate unavailable.")
        return 1
    live_week = _persisted_digest_week()
    live_keys = storage_r2.get_existing_r2_keys(f"videos/{live_week}/") if live_week else set()
    try:
        all_keys = storage_r2.get_existing_r2_keys("videos/")
        purgeable_keys = [k for k in all_keys if k not in live_keys]
        logger.info("JIT purge scope: %d previous-week keys would be deleted before upload.",
                    len(purgeable_keys))
    except Exception:
        pass
    quota = config.R2_STORAGE_QUOTA_BYTES
    print(f"Estimated batch : {batch_bytes / 1073741824:.2f} GB ({config.TOP_DIGEST_COUNT} reels)")
    print(f"R2 current     : {current_bytes / 1073741824:.2f} GB ({count} objects)")
    print(f"Live week kept : {live_week or '(none)'} ({len(live_keys)} keys)")
    print(f"Quota (safety) : {quota / 1073741824:.2f} GB")
    projected = current_bytes + batch_bytes
    fits_raw = projected < quota
    print(f"Without purge  : {projected / 1073741824:.2f} GB -> {'FITS' if fits_raw else 'EXCEEDS quota'}")
    print("With JIT purge : previous weeks are deleted before upload, so the "
          "live-week + new-batch total applies (checked again pre-upload).")
    return 0 if fits_raw else 1


def _build_only(deploy: bool = False) -> int:
    """Compile the static site from the live digest batch (lock must be held)."""
    site_builder.build_site()
    if deploy:
        return _deploy_only()
    return 0


def _deploy_only() -> int:
    """Deploy the compiled site to GitHub Pages (lock must be held)."""
    if _digest_item_count() < MIN_DEPLOY_ITEMS:
        logger.error(
            "Digest has fewer than %d items; refusing to deploy over the previous digest.",
            MIN_DEPLOY_ITEMS,
        )
        return 2
    site_builder.deploy_to_gh_pages()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Instagram Digest v{config.APP_VERSION} — Weekly High-Signal Reel Curator")
    parser.add_argument("--sync", action="store_true", help="Run full weekly extraction, ranking, and sync")
    parser.add_argument("--ad-hoc", action="store_true", help="Run ad-hoc midweek sync picking reels between now and the last run timestamp")
    parser.add_argument("--expand", type=int, default=0, help="Expand active digest with N new external reels from Reels feed")
    parser.add_argument("--serve", action="store_true", help="Run local dashboard HTTP server on port 8080")
    parser.add_argument("--port", type=int, default=8080, help="Port for local server (default: 8080)")
    parser.add_argument("--sync-following", action="store_true", help="Force sync followed creators from Chrome session")
    parser.add_argument("--build-only", action="store_true", help="Compile static site using existing digest data")
    parser.add_argument("--deploy", action="store_true", help="Deploy compiled site to GitHub Pages")
    parser.add_argument("--dry-run", action="store_true", help="Simulate pipeline without downloading or uploading videos")
    parser.add_argument("--estimate", action="store_true", help="Read-only R2 usage projection for the next digest (no downloads/uploads/purges)")
    parser.add_argument("--force", action="store_true", help="Override the post-mass-follow cooldown guard (fresh-account protection)")
    parser.add_argument("--limit-per-creator", type=int, default=15, help="Max candidate reels per creator (default: 15; discovery visits at most 5/creator, 6 for food)")
    parser.add_argument("--reconcile", nargs="?", const="", default=None, metavar="WEEK",
                        help="Finish parked uploads for WEEK (default: live digest week) without touching Instagram")
    parser.add_argument("--days-back", type=int, default=7, help="Candidate publication window in days (default: 7)")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted or shortfall-paused sync run")
    parser.add_argument("--lock-status", action="store_true", help="Show which process holds data/.pipeline.lock, if any (read-only)")
    args = parser.parse_args()

    # Lock-holder readout: read-only, never touches the lock itself.
    if args.lock_status:
        holder = lock_holder_info()
        if holder:
            print(f"LOCKED by pid {holder['pid']} since {holder.get('started_at', '?')}: {holder.get('cmd', '?')}")
        else:
            print("FREE: no live pipeline holds data/.pipeline.lock.")
        return 0

    # Reconcile mode: finish parked uploads, zero Meta access (safe on any
    # network). Runs before everything else and never scrapes.
    if args.reconcile is not None:
        return run_reconcile(args.reconcile or None, deploy=args.deploy)

    # Expand mode
    if args.expand != 0:
        if args.expand < 0:
            parser.error("--expand requires a positive reel count")
        return run_expand(target_count=args.expand, deploy=args.deploy)

    # Pre-flight estimate (read-only): project R2 usage for the upcoming
    # digest without downloading or mutating anything. Purely advisory.
    if args.estimate:
        return _estimate_run(since_ts_override=None)

    # Serve mode
    if args.serve:
        local_server.run_local_server(port=args.port)
        return 0

    # Sync following on-demand
    if args.sync_following:
        extractor.sync_following_accounts(force=True)
        return 0

    # Build only mode (digest-mutating: reads the live batch while sync/expand
    # may rewrite it — hold the same cross-process lock).
    if args.build_only:
        try:
            with _pipeline_file_lock():
                return _build_only(deploy=args.deploy)
        except PipelineBusy as exc:
            logger.error("%s; refusing to start.", exc)
            return 3

    # Deploy only mode
    if args.deploy and not args.sync and not args.ad_hoc:
        try:
            with _pipeline_file_lock():
                return _deploy_only()
        except PipelineBusy as exc:
            logger.error("%s; refusing to start.", exc)
            return 3

    days_back = args.days_back
    since_ts = None
    last_run = get_last_run_info()
    # Ad-hoc runs narrow the window but must not shorten the NEXT weekly run:
    # only anchor to a previous WEEKLY run (kind tag, F20). An ad-hoc success
    # would otherwise shrink Friday's digest to "since the ad-hoc".
    anchorable = last_run and last_run.get("kind", "weekly") != "ad-hoc"
    if args.ad_hoc or (anchorable and "timestamp" in (last_run or {}) and args.days_back == 7):
        if last_run and "timestamp" in last_run:
            elapsed = time.time() - last_run["timestamp"]
            if 3600 <= elapsed <= 7 * 86400:
                since_ts = int(last_run["timestamp"])
                days_back = max(1, int(round(elapsed / 86400.0)))
                logger.info("Anchor to last run: picking reels between %s (~%d days ago) and now",
                            last_run.get("last_run_utc"), days_back)
        elif args.ad_hoc:
            logger.info("Ad-hoc run: no previous run timestamp stored; defaulting to %d days back", days_back)

    # Default to running full sync (or when --sync or --ad-hoc is specified).
    # The follow-cooldown guard refuses fresh-account follow-then-scrape
    # bursts unless --force overrides it. Resumes are exempt: banked work
    # must always be allowed to complete.
    if not args.resume and not _check_follow_cooldown(force=args.force):
        return 2
    if _trust_warming_active():
        logger.info("Trust warming active: doubling creator/enrich pacing for the young account.")
        global CREATOR_PAUSE, ENRICH_PAUSE
        CREATOR_PAUSE = (CREATOR_PAUSE[0] * 2, CREATOR_PAUSE[1] * 2, CREATOR_PAUSE[2] * 2)
        ENRICH_PAUSE = (ENRICH_PAUSE[0] * 2, ENRICH_PAUSE[1] * 2, ENRICH_PAUSE[2] * 2)
    return run_full_sync(
        dry_run=args.dry_run,
        deploy=args.deploy,
        days_back=days_back,
        limit_per_creator=args.limit_per_creator,
        since_timestamp=since_ts,
        resume=args.resume,
    )


if __name__ == "__main__":
    sys.exit(main())
