"""
site_builder.py — Compiles static HTML5/CSS/JS viewer and deploys to GitHub Pages (gh-pages branch).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config

logger = logging.getLogger("InstagramDigest.SiteBuilder")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def build_site(
    digest_data: dict[str, Any] | None = None,
    r2_uploaded_urls: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """
    Renders Variant 1A static site:
    - site/index.html (with Cloudflare R2 streaming URLs for GitHub Pages)
    - site/local_index.html (with local disk video URLs for local dashboard)
    """
    if not digest_data:
        if config.DIGEST_BATCH_FILE.exists():
            digest_data = json.loads(config.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
        else:
            digest_data = {"run_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "items": []}

    raw_items = digest_data.get("items", [])
    week_id = digest_data.get("run_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("viewer.html")

    # Build R2 items (for GitHub Pages deployment)
    r2_items = []
    local_items = []
    url_map = r2_uploaded_urls or {}

    for item in raw_items:
        r2_item = dict(item)
        local_item = dict(item)

        # Video URL resolution
        video_filename = f"{item.get('rank', 1):02d}_{item.get('creator_handle')}_{item.get('id')}.mp4"
        r2_url = url_map.get(item["id"]) or f"{config.R2_PUBLIC_DOMAIN}/videos/{week_id}/{video_filename}"
        local_url = f"/videos/{week_id}/{video_filename}"

        r2_item["video_url"] = r2_url
        r2_item["r2_url"] = r2_url
        r2_item["local_url"] = local_url

        local_item["video_url"] = local_url
        local_item["r2_url"] = r2_url
        local_item["local_url"] = local_url

        item["r2_url"] = r2_url
        item["local_url"] = local_url
        item["video_url"] = r2_url

        r2_items.append(r2_item)
        local_items.append(local_item)

    # Discover available weekly batches
    archive_dir = config.SITE_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    available_week_ids = set()
    if hasattr(config, "DIGESTS_DIR") and config.DIGESTS_DIR.exists():
        for f in config.DIGESTS_DIR.glob("*.json"):
            available_week_ids.add(f.stem)
    available_week_ids.add(week_id)

    # Limit available weeks to RETENTION_WEEKS
    max_weeks = max(1, config.RETENTION_WEEKS)
    sorted_weeks = sorted(list(available_week_ids), reverse=True)[:max_weeks]
    latest_week = sorted_weeks[0] if sorted_weeks else week_id

    # Add share_url and thumbnail to items
    share_dir = config.SITE_DIR / "share"
    share_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir = config.SITE_DIR / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    week_video_dir = config.VIDEOS_DIR / week_id

    for item in r2_items:
        reel_id = item.get("id", "")
        item["share_url"] = f"{config.PAGES_BASE_URL}/share/{reel_id}.html"
        thumb_file = thumb_dir / f"{reel_id}.jpg"
        if not thumb_file.exists() and week_video_dir.exists() and shutil.which("ffmpeg"):
            matches = list(week_video_dir.glob(f"*_{reel_id}.mp4"))
            if matches:
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", "00:00:01", "-i", str(matches[0]), "-vframes", "1", "-q:v", "2", str(thumb_file)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
                    )
                except Exception:
                    pass
        if thumb_file.exists():
            item["thumbnail"] = f"{config.PAGES_BASE_URL}/thumbnails/{reel_id}.jpg"
        elif not item.get("thumbnail"):
            item["thumbnail"] = f"{config.PAGES_BASE_URL}/apple-touch-icon.png"

    for item in local_items:
        reel_id = item.get("id", "")
        item["share_url"] = f"/share/{reel_id}.html"
        thumb_file = thumb_dir / f"{reel_id}.jpg"
        if thumb_file.exists():
            item["thumbnail"] = f"/thumbnails/{reel_id}.jpg"
        elif not item.get("thumbnail"):
            item["thumbnail"] = "/apple-touch-icon.png"

    def build_weeks_metadata(is_local: bool) -> list[dict[str, Any]]:
        meta_list = []
        for idx, w in enumerate(sorted_weeks):
            is_curr = (w == latest_week)
            if is_curr:
                label = "Current"
            elif idx == 1:
                label = "Prev"
            else:
                label = f"Prev {idx}"

            if is_local:
                url = "local_index.html" if is_curr else f"archive/local_{w}.html"
            else:
                url = "index.html" if is_curr else f"archive/{w}.html"

            meta_list.append({
                "week_id": w,
                "label": label,
                "is_current": is_curr,
                "url": url,
            })
        return meta_list

    weeks_r2 = build_weeks_metadata(is_local=False)
    weeks_local = build_weeks_metadata(is_local=True)

    # 1. Render site/index.html (GitHub Pages version)
    rendered_r2 = template.render(
        items=r2_items,
        week_id=week_id,
        available_weeks=weeks_r2,
        is_local=False,
        default_speed=config.DEFAULT_PLAYBACK_SPEED,
        pages_base_url=config.PAGES_BASE_URL,
    )
    r2_index_path = config.SITE_DIR / "index.html"
    r2_index_path.write_text(rendered_r2, encoding="utf-8")

    # 2. Render site/local_index.html (Local dashboard version)
    rendered_local = template.render(
        items=local_items,
        week_id=week_id,
        available_weeks=weeks_local,
        is_local=True,
        default_speed=config.DEFAULT_PLAYBACK_SPEED,
        pages_base_url="http://localhost:8080",
    )
    local_index_path = config.SITE_DIR / "local_index.html"
    local_index_path.write_text(rendered_local, encoding="utf-8")

    # 3. Generate Standalone WhatsApp & Social Open Graph Share Pages
    for item in r2_items:
        reel_id = item.get("id", "")
        if not reel_id:
            continue
        thumb = item.get("thumbnail") or f"{config.PAGES_BASE_URL}/apple-touch-icon.png"
        caption = (item.get("caption") or "Watch on Instagram Digest").replace('"', '&quot;').replace('<', '&lt;')
        handle = item.get("creator_handle", "")
        rank_dsp = item.get("rank_display", "#01")
        video_url = item.get("r2_url", "")
        share_page_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Reel by @{handle} • {rank_dsp}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">

  <!-- WhatsApp & Social Open Graph Rich Card Metadata -->
  <meta property="og:site_name" content="Instagram Digest">
  <meta property="og:type" content="video.other">
  <meta property="og:title" content="Reel by @{handle} ({rank_dsp})">
  <meta property="og:description" content="{caption[:220]}">
  <meta property="og:url" content="{config.PAGES_BASE_URL}/share/{reel_id}.html">
  <meta property="og:image" content="{thumb}">
  <meta property="og:image:secure_url" content="{thumb}">
  <meta property="og:image:type" content="image/jpeg">
  <meta property="og:image:width" content="720">
  <meta property="og:image:height" content="1280">

  <!-- Video Stream for Browser Players -->
  <meta property="og:video" content="{video_url}">
  <meta property="og:video:url" content="{video_url}">
  <meta property="og:video:secure_url" content="{video_url}">
  <meta property="og:video:type" content="video/mp4">
  <meta property="og:video:width" content="720">
  <meta property="og:video:height" content="1280">

  <!-- Twitter / X Summary Card -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Reel by @{handle} ({rank_dsp})">
  <meta name="twitter:description" content="{caption[:220]}">
  <meta name="twitter:image" content="{thumb}">
</head>
<body style="background:#000;color:#fff;margin:0;padding:0;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif;">
  <div style="width:100%;max-width:440px;margin:auto;display:flex;flex-direction:column;align-items:center;padding:16px;box-sizing:border-box;">
    <div style="display:flex;align-items:center;justify-content:space-between;width:100%;margin-bottom:12px;">
      <span style="font-weight:700;font-size:16px;">@{handle}</span>
      <span style="background:rgba(255,255,255,0.15);padding:3px 8px;border-radius:12px;font-size:12px;font-weight:600;">{rank_dsp}</span>
    </div>
    <div style="position:relative;width:100%;aspect-ratio:9/16;background:#111;border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,0.8);">
      <video src="{video_url}" poster="{thumb}" controls playsinline autoplay loop style="width:100%;height:100%;object-fit:cover;display:block;"></video>
    </div>
    <p style="margin:14px 0 8px;font-size:13px;color:#d1d5db;line-height:1.4;text-align:left;width:100%;">{caption[:180]}</p>
    <a href="../index.html?reel={reel_id}" style="margin-top:10px;width:100%;background:#f09433;color:#000;font-weight:700;font-size:14px;padding:12px 0;border-radius:24px;text-align:center;text-decoration:none;display:block;box-shadow:0 4px 15px rgba(240,148,51,0.4);">Open in Instagram Digest App &rarr;</a>
  </div>
</body>
</html>"""
        (share_dir / f"{reel_id}.html").write_text(share_page_content, encoding="utf-8")

    # 4. Also render as archive copies
    (archive_dir / f"{week_id}.html").write_text(rendered_r2, encoding="utf-8")
    (archive_dir / f"local_{week_id}.html").write_text(rendered_local, encoding="utf-8")

    # 4. Write data.json API payload
    (config.SITE_DIR / "data.json").write_text(
        json.dumps(digest_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 5. Write .nojekyll for GitHub Pages
    (config.SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")

    # 6. Copy PWA and Apple Touch Icon assets
    assets_dir = config.ROOT_DIR / "assets"
    for icon_name in ["apple-touch-icon.png", "icon-192.png", "icon-512.png", "icon.svg"]:
        src_icon = assets_dir / icon_name
        if src_icon.exists():
            shutil.copy2(src_icon, config.SITE_DIR / icon_name)

    # 7. Write Web App Manifest for iOS/Android Add to Home Screen
    manifest_data = {
        "name": "Instagram Digest",
        "short_name": "Digest",
        "description": "Weekly curated Instagram Reels digest",
        "start_url": "./",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": [
            {
                "src": "apple-touch-icon.png",
                "sizes": "180x180 360x360",
                "type": "image/png"
            },
            {
                "src": "icon-192.png",
                "sizes": "192x192",
                "type": "image/png"
            },
            {
                "src": "icon-512.png",
                "sizes": "512x512",
                "type": "image/png"
            }
        ]
    }
    (config.SITE_DIR / "manifest.webmanifest").write_text(
        json.dumps(manifest_data, indent=2), encoding="utf-8"
    )

    logger.info("Successfully compiled static site at %s and %s (%d available weeks)",
                r2_index_path, local_index_path, len(sorted_weeks))
    return r2_index_path, local_index_path


def deploy_to_gh_pages(site_dir: Path = config.SITE_DIR, repo_url: str = config.GH_PAGES_REPO) -> bool:
    """Deploy site_dir contents to orphan gh-pages branch with force push."""
    logger.info("Deploying Instagram Digest to GitHub Pages (%s)...", repo_url)

    if not shutil.which("git"):
        logger.error("Git is not found; skipping GitHub Pages deployment.")
        return False

    temp_git_dir = site_dir / ".git"
    try:
        if temp_git_dir.exists():
            shutil.rmtree(temp_git_dir, ignore_errors=True)

        subprocess.run(["git", "init"], cwd=site_dir, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "InstagramDigest Bot"], cwd=site_dir, check=True)
        subprocess.run(["git", "config", "user.email", "digest@bot.local"], cwd=site_dir, check=True)
        subprocess.run(["git", "checkout", "-b", "gh-pages"], cwd=site_dir, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "add", "."], cwd=site_dir, check=True)

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        subprocess.run(
            ["git", "commit", "-m", f"Instagram Digest sync: {now_str}"],
            cwd=site_dir, check=True, stdout=subprocess.DEVNULL
        )
        subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=site_dir, check=True)

        logger.info("Pushing to origin gh-pages (force)...")
        res = subprocess.run(["git", "push", "-f", "origin", "gh-pages"], cwd=site_dir, capture_output=True, text=True)
        if res.returncode == 0:
            logger.info("Successfully deployed to GitHub Pages! Live at https://vkr1729.github.io/Instagram_digest/")
            return True
        else:
            logger.warning("Failed pushing to gh-pages: %s", res.stderr)
            return False
    except Exception as exc:
        logger.exception("Error during GitHub Pages deployment: %s", exc)
        return False
    finally:
        if temp_git_dir.exists():
            shutil.rmtree(temp_git_dir, ignore_errors=True)
