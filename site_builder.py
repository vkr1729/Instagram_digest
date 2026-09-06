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

    sorted_weeks = sorted(list(available_week_ids), reverse=True)
    latest_week = sorted_weeks[0] if sorted_weeks else week_id

    def build_weeks_metadata(is_local: bool) -> list[dict[str, Any]]:
        meta_list = []
        for w in sorted_weeks:
            try:
                dt = datetime.strptime(w, "%Y-%m-%d")
                label = dt.strftime("Week of %b %d, %Y")
            except ValueError:
                label = f"Week {w}"

            is_curr = (w == latest_week)
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
    )
    r2_index_path = config.SITE_DIR / "index.html"
    r2_index_path.write_text(rendered_r2, encoding="utf-8")

    # 2. Render site/local_index.html (Local dashboard version)
    rendered_local = template.render(
        items=local_items,
        week_id=week_id,
        available_weeks=weeks_local,
        is_local=True,
    )
    local_index_path = config.SITE_DIR / "local_index.html"
    local_index_path.write_text(rendered_local, encoding="utf-8")

    # 3. Also render as archive copies
    (archive_dir / f"{week_id}.html").write_text(rendered_r2, encoding="utf-8")
    (archive_dir / f"local_{week_id}.html").write_text(rendered_local, encoding="utf-8")

    # 4. Write data.json API payload
    (config.SITE_DIR / "data.json").write_text(
        json.dumps(digest_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 5. Write .nojekyll for GitHub Pages
    (config.SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")

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
