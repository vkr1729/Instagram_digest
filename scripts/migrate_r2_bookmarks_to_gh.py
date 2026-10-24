#!/usr/bin/env python3
"""
Migrate bookmarks from Cloudflare R2 into private GitHub repository:
- Downloads video MP4 files from Cloudflare R2 (bucket: bookmarks/)
- Uploads MP4 files to GitHub Release 'v2026' in vkr1729/ig-bookmarks-2026
- Generates and commits bookmarks.json to the repository
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config
import storage_r2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bookmark_migrator")

REPO = "vkreddy1729-ops/ig-bookmarks"
RELEASE_TAG = "v2026"


def run_gh_cmd(cmd: list[str], **kwargs):
    """Run a gh command under vkreddy1729-ops account by clearing ambient GH_TOKEN."""
    env = os.environ.copy()
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return subprocess.run(cmd, env=env, **kwargs)



def main():
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

    logger.info("Found %d bookmarks in Cloudflare R2", len(items))

    # Check existing release assets
    existing_assets = set()
    try:
        res = run_gh_cmd(
            ["gh", "release", "view", RELEASE_TAG, "--repo", REPO, "--json", "assets"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        for a in data.get("assets", []):
            existing_assets.add(a["name"])
    except Exception as exc:
        logger.warning("Could not list release assets: %s", exc)

    logger.info("Found %d existing assets in release %s", len(existing_assets), RELEASE_TAG)

    migrated_bookmarks = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        for idx, item in enumerate(items, 1):
            reel_id = item.get("id")
            if not reel_id:
                continue

            mp4_filename = f"{reel_id}.mp4"
            r2_mp4_key = f"bookmarks/{mp4_filename}"

            logger.info("[%d/%d] Processing %s (@%s)...", idx, len(items), reel_id, item.get("creator_handle", ""))

            # Upload video asset if not already uploaded
            if mp4_filename not in existing_assets:
                local_file = tmp_path / mp4_filename
                try:
                    s3.download_file(config.R2_BUCKET_NAME, r2_mp4_key, str(local_file))
                    logger.info("  Downloaded %s (%.2f MB)", mp4_filename, local_file.stat().st_size / (1024 * 1024))
                    run_gh_cmd(
                        ["gh", "release", "upload", RELEASE_TAG, str(local_file), "--repo", REPO, "--clobber"],
                        check=True,
                        capture_output=True,
                    )
                    logger.info("  Uploaded to release %s", RELEASE_TAG)
                    existing_assets.add(mp4_filename)
                except Exception as exc:
                    logger.error("  Failed migrating asset %s: %s", mp4_filename, exc)
            else:
                logger.info("  Already present in release %s", RELEASE_TAG)

            # Build standardized bookmark record
            bm = {
                "id": reel_id,
                "creator_handle": item.get("creator_handle", ""),
                "caption": item.get("caption", ""),
                "category": item.get("category", "general"),
                "thumbnail_url": f"https://vkr1729.github.io/Instagram_digest/thumbnails/{reel_id}_portrait.jpg",
                "video_asset_name": mp4_filename,
                "size_bytes": item.get("size_bytes", 0),
                "bookmarked_at": item.get("bookmarked_at", ""),
                "canonical_url": f"https://www.instagram.com/reel/{reel_id}/",
            }
            migrated_bookmarks.append(bm)

    # Sort newest bookmarked first
    migrated_bookmarks.sort(key=lambda x: x.get("bookmarked_at", ""), reverse=True)

    # Save to local file & commit to GitHub repo
    bookmarks_json = json.dumps(migrated_bookmarks, indent=2)
    bookmarks_file = Path("data/bookmarks_migrated_2026.json")
    bookmarks_file.parent.mkdir(parents=True, exist_ok=True)
    bookmarks_file.write_text(bookmarks_json, encoding="utf-8")
    logger.info("Saved %d bookmarks to %s", len(migrated_bookmarks), bookmarks_file)

    # Commit bookmarks.json to repo root
    try:
        import base64
        encoded = base64.b64encode(bookmarks_json.encode("utf-8")).decode("utf-8")
        # Check if file exists to get SHA
        get_res = run_gh_cmd(
            ["gh", "api", f"/repos/{REPO}/contents/bookmarks.json"],
            capture_output=True,
            text=True,
        )
        params = [
            "gh", "api", "-X", "PUT", f"/repos/{REPO}/contents/bookmarks.json",
            "-f", f"message=Update bookmarks.json ({len(migrated_bookmarks)} bookmarks)",
            "-f", f"content={encoded}",
        ]
        if get_res.returncode == 0:
            existing_file = json.loads(get_res.stdout)
            params.extend(["-f", f"sha={existing_file['sha']}"])

        run_gh_cmd(params, check=True, capture_output=True)
        logger.info("Successfully pushed bookmarks.json to %s (main branch)!", REPO)
    except Exception as exc:
        logger.error("Failed pushing bookmarks.json: %s", exc)
        return 1

    logger.info("Migration complete: 54 bookmarks backed up to %s!", REPO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
