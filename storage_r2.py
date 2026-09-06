"""
storage_r2.py — Cloudflare R2 S3-compatible media uploader, quota guard, and 14-day rolling purger.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import config

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
    """Purge objects on Cloudflare R2 older than max_age_days (strictly 14 days)."""
    s3 = get_s3_client()
    if not s3:
        logger.info("R2 credentials not active; skipping remote R2 purge.")
        return []

    purged = []
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    logger.info("Purging Cloudflare R2 objects older than %s (14-day rolling window)...", cutoff_dt.date())

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
                    logger.info("Deleting expired R2 object: %s (LastModified: %s)", key, last_modified)
                    s3.delete_object(Bucket=config.R2_BUCKET_NAME, Key=key)
                    purged.append(key)
    except ClientError as e:
        logger.warning("Error during R2 purge: %s", e)

    logger.info("Purged %d expired objects from Cloudflare R2.", len(purged))
    return purged


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


def upload_reel_to_r2(local_file: Path, week_id: str, key_name: str | None = None) -> str:
    """
    Upload a local video file to Cloudflare R2.
    Returns the public CDN URL (or local fallback path if R2 is not configured).
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
        try:
            # Check if already present on R2
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
            return public_url
        except Exception as exc:
            logger.error("Failed uploading to R2: %s. Using local fallback.", exc)

    # Local fallback path for local server playback
    return f"/videos/{week_id}/{key_name}"
