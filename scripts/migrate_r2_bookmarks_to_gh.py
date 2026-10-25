#!/usr/bin/env python3
"""
Migrate bookmarks from Cloudflare R2 into private GitHub repository:
- Downloads video MP4 files from Cloudflare R2 (bucket: bookmarks/)
- Uploads MP4 files to GitHub Release in vkreddy1729-ops/ig-bookmarks
- Uploads portrait thumbnails to repository thumbnails/ directory
- Generates and commits bookmarks.json to the repository
"""

import argparse
import base64
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config
import storage_r2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bookmark_migrator")

DEFAULT_REPO = "vkreddy1729-ops/ig-bookmarks"
DEFAULT_RELEASE_TAG = "v2026"


def run_gh_cmd(cmd: list[str], **kwargs):
    """Run a gh command under vkreddy1729-ops account by clearing ambient GH_TOKEN."""
    env = os.environ.copy()
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return subprocess.run(cmd, env=env, **kwargs)


def migrate_bookmarks(
    repo: str = DEFAULT_REPO,
    release_tag: str = DEFAULT_RELEASE_TAG,
    s3: Any = None,
    items: Optional[list[dict[str, Any]]] = None,
    output_path: Optional[Path] = None,
    push_to_gh: bool = True,
) -> int:
    """Migrate bookmarks from Cloudflare R2 to GitHub Releases + repo."""
    if items is None:
        if s3 is None:
            s3 = storage_r2.get_s3_client()
        if not s3:
            logger.error("Failed to initialize R2 client")
            return 1

        logger.info("Fetching bookmarks/manifest.json from Cloudflare R2...")
        try:
            obj = s3.get_object(Bucket=config.R2_BUCKET_NAME, Key="bookmarks/manifest.json")
            items = json.loads(obj["Body"].read().decode("utf-8"))
        except Exception as exc:
            logger.error("Failed reading bookmarks manifest: %s", exc)
            return 1

    logger.info("Found %d bookmarks to process", len(items))

    # Check existing release assets
    existing_assets: set[str] = set()
    try:
        res = run_gh_cmd(
            ["gh", "release", "view", release_tag, "--repo", repo, "--json", "assets"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        for a in data.get("assets", []):
            existing_assets.add(a["name"])
    except Exception as exc:
        logger.warning("Could not list release assets: %s", exc)

    logger.info("Found %d existing assets in release %s", len(existing_assets), release_tag)

    migrated_bookmarks: list[dict[str, Any]] = []
    failed_reels: list[str] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        for idx, item in enumerate(items, 1):
            reel_id = item.get("id")
            if not reel_id:
                continue

            mp4_filename = f"{reel_id}.mp4"
            r2_mp4_key = f"bookmarks/{mp4_filename}"

            logger.info("[%d/%d] Processing %s (@%s)...", idx, len(items), reel_id, item.get("creator_handle", ""))

            # Upload video asset if not already uploaded (AUDIT-017)
            if mp4_filename not in existing_assets:
                local_file = tmp_path / mp4_filename
                try:
                    if s3 is None:
                        s3 = storage_r2.get_s3_client()
                    if not s3:
                        raise RuntimeError("S3 client unavailable")
                    s3.download_file(config.R2_BUCKET_NAME, r2_mp4_key, str(local_file))
                    sz = local_file.stat().st_size
                    if sz <= 0 or (item.get("size_bytes") and sz != item["size_bytes"]):
                        logger.error("  Invalid size for %s: %d bytes (expected %s)", mp4_filename, sz, item.get("size_bytes"))
                        failed_reels.append(reel_id)
                    else:
                        logger.info("  Downloaded %s (%.2f MB)", mp4_filename, sz / (1024 * 1024))
                        run_gh_cmd(
                            ["gh", "release", "upload", release_tag, str(local_file), "--repo", repo, "--clobber"],
                            check=True,
                            capture_output=True,
                        )
                        logger.info("  Uploaded to release %s", release_tag)
                        existing_assets.add(mp4_filename)
                except Exception as exc:
                    logger.error("  Failed migrating asset %s: %s", mp4_filename, exc)
                    failed_reels.append(reel_id)
            else:
                logger.info("  Already present in release %s", release_tag)

            # Resolve and upload portrait thumbnail (AUDIT-018)
            thumb_portrait = REPO_ROOT / "site" / "thumbnails" / f"{reel_id}_portrait.jpg"
            thumb_standard = REPO_ROOT / "site" / "thumbnails" / f"{reel_id}.jpg"
            local_thumb = thumb_portrait if thumb_portrait.exists() else (thumb_standard if thumb_standard.exists() else None)
            thumbnail_url = None
            thumbnail_missing = False

            if local_thumb and local_thumb.exists():
                thumb_target_name = f"{reel_id}_portrait.jpg"
                try:
                    encoded_thumb = base64.b64encode(local_thumb.read_bytes()).decode("utf-8")
                    get_thumb_res = run_gh_cmd(
                        ["gh", "api", f"/repos/{repo}/contents/thumbnails/{thumb_target_name}"],
                        capture_output=True,
                        text=True,
                    )
                    put_thumb_params = [
                        "gh", "api", "-X", "PUT", f"/repos/{repo}/contents/thumbnails/{thumb_target_name}",
                        "-f", f"message=Upload thumbnail for {reel_id}",
                        "-f", f"content={encoded_thumb}",
                    ]
                    if get_thumb_res.returncode == 0:
                        try:
                            existing_thumb = json.loads(get_thumb_res.stdout)
                            put_thumb_params.extend(["-f", f"sha={existing_thumb['sha']}"])
                        except Exception:
                            pass
                    run_gh_cmd(put_thumb_params, check=True, capture_output=True)
                    thumbnail_url = f"https://raw.githubusercontent.com/{repo}/main/thumbnails/{thumb_target_name}"
                except Exception as thumb_exc:
                    logger.warning("  Failed uploading thumbnail for %s: %s", reel_id, thumb_exc)
                    thumbnail_missing = True
            else:
                logger.warning("  Local thumbnail absent for %s", reel_id)
                thumbnail_missing = True

            # Resolve bookmarked_at timestamp (AUDIT-014)
            bookmarked_at = item.get("bookmarked_at") or item.get("last_modified") or item.get("updated_at")
            if not bookmarked_at or not str(bookmarked_at).strip():
                bookmarked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                bookmarked_at = str(bookmarked_at).strip()

            # Build standardized bookmark record (AUDIT-014, AUDIT-027)
            bm: dict[str, Any] = {
                "id": reel_id,
                "creator_handle": item.get("creator_handle", ""),
                "caption": item.get("caption", ""),
                "category": item.get("category", "general"),
                "size_bytes": item.get("size_bytes", 0),
                "bookmarked_at": bookmarked_at,
                "canonical_url": f"https://www.instagram.com/reel/{reel_id}/",
            }
            if reel_id not in failed_reels:
                bm["video_asset_name"] = f"{reel_id}.mp4"
                bm["release_tag"] = release_tag

            if thumbnail_missing:
                bm["thumbnail_missing"] = True
            else:
                bm["thumbnail_url"] = thumbnail_url
            migrated_bookmarks.append(bm)

    # Sort newest bookmarked first
    migrated_bookmarks.sort(key=lambda x: x.get("bookmarked_at", ""), reverse=True)

    # Save to local file & commit to GitHub repo (AUDIT-051)
    bookmarks_json = json.dumps(migrated_bookmarks, indent=2)
    bookmarks_file = output_path or (REPO_ROOT / "data" / "bookmarks_migrated_2026.json")
    bookmarks_file.parent.mkdir(parents=True, exist_ok=True)
    bookmarks_file.write_text(bookmarks_json, encoding="utf-8")
    logger.info("Saved %d bookmarks to %s", len(migrated_bookmarks), bookmarks_file)

    # Commit bookmarks.json to repo root
    if push_to_gh:
        try:
            encoded = base64.b64encode(bookmarks_json.encode("utf-8")).decode("utf-8")
            get_res = run_gh_cmd(
                ["gh", "api", f"/repos/{repo}/contents/bookmarks.json"],
                capture_output=True,
                text=True,
            )
            params = [
                "gh", "api", "-X", "PUT", f"/repos/{repo}/contents/bookmarks.json",
                "-f", f"message=Update bookmarks.json ({len(migrated_bookmarks)} bookmarks)",
                "-f", f"content={encoded}",
            ]
            if get_res.returncode == 0:
                existing_file = json.loads(get_res.stdout)
                params.extend(["-f", f"sha={existing_file['sha']}"])

            run_gh_cmd(params, check=True, capture_output=True)
            logger.info("Successfully pushed bookmarks.json to %s (main branch)!", repo)
        except Exception as exc:
            logger.error("Failed pushing bookmarks.json: %s", exc)
            return 1

    logger.info("Migration complete: %d bookmarks backed up to %s!", len(migrated_bookmarks), repo)
    if failed_reels:
        logger.error("Migration finished with %d failures: %s", len(failed_reels), failed_reels)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="Migrate bookmarks from Cloudflare R2 to private GitHub repository.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Target GitHub repo (default: {DEFAULT_REPO})")
    parser.add_argument("--tag", default=DEFAULT_RELEASE_TAG, help=f"Target GitHub release tag (default: {DEFAULT_RELEASE_TAG})")
    args = parser.parse_args()

    return migrate_bookmarks(repo=args.repo, release_tag=args.tag)


if __name__ == "__main__":
    sys.exit(main())
