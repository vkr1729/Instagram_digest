"""
storage_r2.py — Cloudflare R2 S3-compatible media uploader, quota guard, and 14-day rolling purger.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import threading

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import config

# Guards the check-then-add fast path on a shared existing_keys set passed
# by multi-threaded upload callers (main.run_full_sync / run_expand).
_R2_KEYS_LOCK = threading.Lock()

logger = logging.getLogger("InstagramDigest.StorageR2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def get_s3_client():
    """Create S3 client for Cloudflare R2."""
    if not (config.R2_ACCOUNT_ID and config.R2_ACCESS_KEY_ID and config.R2_SECRET_ACCESS_KEY):
        return None

    try:
        return boto3.client(
            "s3",
            endpoint_url=config.R2_ENDPOINT_URL,
            aws_access_key_id=config.R2_ACCESS_KEY_ID,
            aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
            region_name="auto",
        )
    except Exception as exc:
        logger.warning("Could not initialize S3 client for R2: %s", exc)
        return None


def get_bucket_storage_usage() -> tuple[int, int]:
    """Calculate total size in bytes and object count in R2 bucket."""
    s3 = get_s3_client()
    if not s3:
        return 0, 0

    total_bytes = 0
    total_objects = 0
    paginator = s3.get_paginator("list_objects_v2")

    try:
        for page in paginator.paginate(Bucket=config.R2_BUCKET_NAME):
            contents = page.get("Contents") or []
            for obj in contents:
                total_bytes += obj.get("Size", 0)
                total_objects += 1
    except ClientError as e:
        logger.warning("Error calculating R2 bucket usage: %s", e)
        return 0, 0

    return total_bytes, total_objects


def check_preflight_quota(estimated_new_bytes: int = 1000 * 1024 * 1024) -> bool:
    """
    Strict pre-flight safety check:
    Ensures current_storage + new_batch < 5 GB (half of Cloudflare's 10 GB free tier).
    """
    current_bytes, count = get_bucket_storage_usage()
    projected = current_bytes + estimated_new_bytes
    current_mb = current_bytes / (1024 * 1024)
    projected_mb = projected / (1024 * 1024)
    quota_mb = config.R2_STORAGE_QUOTA_BYTES / (1024 * 1024)

    logger.info("R2 Quota Pre-Flight: Current=%.1f MB (%d objects), Projected=%.1f MB, Quota=%.1f MB",
                current_mb, count, projected_mb, quota_mb)

    if projected >= config.R2_STORAGE_QUOTA_BYTES:
        logger.error(
            "CRITICAL: Cloudflare R2 safety quota exceeded! (Projected: %.1f MB >= Quota: %.1f MB). "
            "Upload aborted to prevent billing.", projected_mb, quota_mb
        )
        return False
    return True


def purge_expired_r2_objects(max_age_days: int = config.RETENTION_DAYS) -> list[str]:
    """Purge objects on Cloudflare R2 older than max_age_days (default config.RETENTION_DAYS)."""
    s3 = get_s3_client()
    if not s3:
        logger.info("R2 credentials not active; skipping remote R2 purge.")
        return []

    purged = []
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    logger.info("Purging Cloudflare R2 objects older than %s (%d-day rolling window)...", cutoff_dt.date(), max_age_days)

    stale_keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=config.R2_BUCKET_NAME, Prefix="videos/"):
            for obj in page.get("Contents") or []:
                key = obj.get("Key", "")
                last_modified = obj.get("LastModified", datetime.now(timezone.utc))

                # Check date in key name (e.g. videos/2026-W10/...) or last_modified
                key_match = re.search(r"(\d{4}-\d{2}-\d{2})", key)
                is_stale = False
                if key_match:
                    try:
                        k_date = datetime.strptime(key_match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        if k_date < cutoff_dt:
                            is_stale = True
                    except ValueError:
                        pass

                if not is_stale and last_modified < cutoff_dt:
                    is_stale = True

                if is_stale:
                    stale_keys.append(key)

        # Batch delete in chunks of up to 1000 keys
        for i in range(0, len(stale_keys), 1000):
            chunk = stale_keys[i : i + 1000]
            logger.info("Batch deleting %d expired R2 objects...", len(chunk))
            s3.delete_objects(
                Bucket=config.R2_BUCKET_NAME,
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )
            purged.extend(chunk)
    except ClientError as e:
        logger.warning("Error during R2 purge: %s", e)

    logger.info("Purged %d expired objects from Cloudflare R2.", len(purged))
    return purged


def purge_unreferenced_r2_videos() -> list[str]:
    """
    Purge unreferenced / orphan video objects on Cloudflare R2 for completed digests.
    Ensures that stale videos from re-ranking or deleted sources do not exhaust the R2 storage quota.
    """
    s3 = get_s3_client()
    if not s3:
        return []

    # 1. Collect referenced reel IDs across known digests.
    # Matching is by (week, reel-id suffix), NOT by rank-prefixed key: ranks
    # are display order and shift on re-rank/expand, while the reel id is
    # stable. Exact rank-key matching mislabels live objects as orphans.
    # A digest that fails to parse or holds zero items is NEVER ground truth
    # for its week (a torn write must not mass-delete live objects).
    known_weeks: set[str] = set()
    active_ids_by_week: dict[str, set[str]] = {}

    digest_files = []
    if config.DIGESTS_DIR.exists():
        digest_files.extend(config.DIGESTS_DIR.glob("*.json"))
    if config.DIGEST_BATCH_FILE.exists() and config.DIGEST_BATCH_FILE not in digest_files:
        digest_files.append(config.DIGEST_BATCH_FILE)

    for df in digest_files:
        try:
            data = json.loads(df.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skipping unreadable digest %s during orphan purge: %s", df, exc)
            continue
        wk = data.get("run_date") or df.stem
        items = data.get("items") or []
        if not wk or wk == "top100_digest" or not items:
            continue
        known_weeks.add(wk)
        week_ids = active_ids_by_week.setdefault(wk, set())
        for item in items:
            rid = item.get("id")
            if rid:
                week_ids.add(str(rid))

    if not known_weeks or not any(active_ids_by_week.values()):
        return []

    def _is_referenced(key: str, week: str) -> bool:
        ids = active_ids_by_week.get(week) or set()
        return any(key.endswith(f"_{rid}.mp4") for rid in ids)

    # 2. Find orphan keys in known week prefixes
    orphan_keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    try:
        for wk in known_weeks:
            for page in paginator.paginate(Bucket=config.R2_BUCKET_NAME, Prefix=f"videos/{wk}/"):
                for obj in page.get("Contents") or []:
                    k = obj.get("Key", "")
                    if k and k.endswith(".mp4") and not _is_referenced(k, wk):
                        orphan_keys.append(k)

        # Batch delete in chunks of up to 1000 keys (verbose: surface errors)
        purged: list[str] = []
        for i in range(0, len(orphan_keys), 1000):
            chunk = orphan_keys[i : i + 1000]
            logger.info("Batch deleting %d unreferenced R2 video objects...", len(chunk))
            resp = s3.delete_objects(
                Bucket=config.R2_BUCKET_NAME,
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": False},
            )
            for err in resp.get("Errors") or []:
                logger.error("Failed deleting orphan %s: %s", err.get("Key"), err.get("Message"))
            purged.extend([d.get("Key", "") for d in resp.get("Deleted") or []] or chunk)

        if purged:
            logger.info("Purged %d unreferenced video objects from Cloudflare R2.", len(purged))
        return purged
    except ClientError as e:
        logger.warning("Error during R2 unreferenced videos purge: %s", e)
        return []


def get_existing_r2_keys(prefix: str = "videos/") -> set[str]:
    """List all existing keys on R2 with given prefix in a single/paginated scan."""
    s3 = get_s3_client()
    if not s3:
        return set()
    keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=config.R2_BUCKET_NAME, Prefix=prefix):
            for obj in page.get("Contents") or []:
                k = obj.get("Key")
                if k:
                    keys.add(k)
    except Exception as exc:
        logger.warning("Error listing R2 keys for prefix %s: %s", prefix, exc)
    return keys


def purge_expired_local_videos(max_age_days: int = config.RETENTION_DAYS) -> list[str]:
    """Purge local downloaded MP4 files older than max_age_days from videos/."""
    purged = []
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).date()
    logger.info("Purging local MP4 files older than %s...", cutoff_date)

    if not config.VIDEOS_DIR.exists():
        return []

    for f in config.VIDEOS_DIR.glob("**/*.mp4"):
        if f.is_file():
            # Check date pattern or file modification time
            match = re.search(r"(\d{4}-\d{2}-\d{2})", f.name) or re.search(r"(\d{4}-\d{2}-\d{2})", str(f.parent))
            is_stale = False
            if match:
                try:
                    f_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                    if f_date < cutoff_date:
                        is_stale = True
                except ValueError:
                    pass

            if not is_stale:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).date()
                if mtime < cutoff_date:
                    is_stale = True

            if is_stale:
                logger.info("Purging local stale video: %s", f.name)
                f.unlink(missing_ok=True)
                purged.append(f.name)

    logger.info("Purged %d expired local video files.", len(purged))
    return purged


def upload_reel_to_r2(
    local_file: Path,
    week_id: str,
    key_name: str | None = None,
    existing_keys: set[str] | None = None,
) -> str:
    """
    Upload a local video file to Cloudflare R2.
    Returns the public CDN URL (or local fallback path if R2 is not configured).
    Never returns a local fallback path when remote R2 is configured and fails.
    """
    if not local_file.exists():
        logger.warning("Local file does not exist: %s", local_file)
        return ""

    if not key_name:
        key_name = local_file.name

    r2_key = f"videos/{week_id}/{key_name}"

    s3 = get_s3_client()
    if s3 and config.R2_PUBLIC_DOMAIN:
        public_url = f"{config.R2_PUBLIC_DOMAIN}/{r2_key}"

        # Check if already present on R2 (fast-path via pre-scanned keys or head_object)
        if existing_keys is not None:
            with _R2_KEYS_LOCK:
                if r2_key in existing_keys:
                    logger.info("Object %s already exists on R2, skipping upload: %s", key_name, public_url)
                    return public_url
        else:
            try:
                s3.head_object(Bucket=config.R2_BUCKET_NAME, Key=r2_key)
                logger.info("Object %s already exists on R2, skipping upload: %s", key_name, public_url)
                return public_url
            except ClientError:
                pass  # Does not exist yet, proceed to upload

        try:
            logger.info("Uploading %s to R2 (%s)...", local_file.name, r2_key)
            s3.upload_file(
                str(local_file),
                config.R2_BUCKET_NAME,
                r2_key,
                ExtraArgs={"ContentType": "video/mp4", "CacheControl": "public, max-age=1209600, immutable"},
            )
            logger.info("Uploaded successfully: %s", public_url)
            if existing_keys is not None:
                with _R2_KEYS_LOCK:
                    existing_keys.add(r2_key)
            return public_url
        except Exception as exc:
            logger.error("Failed uploading to R2: %s", exc)
            return ""  # Remote configured but upload failed; caller must drop this unplayable reel

    # Local fallback path only if R2 is not configured at all
    return f"/videos/{week_id}/{key_name}" if not s3 else ""
