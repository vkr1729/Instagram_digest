"""
extractor.py — Ingests Instagram followed accounts and extracts weekly reels & metadata.
Uses Playwright for bot-resistant profile reel discovery + yt-dlp for single reel metadata & downloads.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

import config

logger = logging.getLogger("InstagramDigest.Extractor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def get_cookie_args() -> list[str]:
    """Determine best available cookie argument: cookies.txt or browser cookies."""
    cookies_txt = config.ROOT_DIR / "cookies.txt"
    if cookies_txt.exists():
        return ["--cookies", str(cookies_txt)]
    return ["--cookies-from-browser", "chrome"]


def get_blacklisted_creators() -> set[str]:
    """Retrieve set of blacklisted creator handles (lowercase)."""
    if hasattr(config, "BLACKLIST_FILE") and config.BLACKLIST_FILE.exists():
        try:
            data = json.loads(config.BLACKLIST_FILE.read_text(encoding="utf-8"))
            return set(h.lower().replace("@", "") for h in data.get("creators", []))
        except Exception:
            pass
    return set()


def load_sources() -> list[dict[str, Any]]:
    """Load tracked creators from sources.json, excluding blacklisted channels."""
    if not config.SOURCES_FILE.exists():
        return []
    try:
        sources = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
        blacklist = get_blacklisted_creators()
        if blacklist:
            return [s for s in sources if s.get("handle", "").lower().replace("@", "") not in blacklist]
        return sources
    except Exception as exc:
        logger.error("Error reading sources.json: %s", exc)
        return []


def save_sources(sources: list[dict[str, Any]]) -> None:
    """Save updated creators list to sources.json."""
    config.SOURCES_FILE.write_text(
        json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def categorize_creator(handle: str, name: str) -> str:
    """Intelligently map an Instagram creator into one of 6 thematic buckets:
    entertainment, finance, ai_tech, niche, health, food.
    """
    clean_h = handle.lstrip("@").lower().strip()

    # 1. First check if category is defined in sources.json
    if config.SOURCES_FILE.exists():
        try:
            curated = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
            for s in curated:
                if s.get("handle", "").lower().replace("@", "") == clean_h:
                    cat = s.get("category")
                    if cat:
                        return cat
        except Exception:
            pass

    text = f"{clean_h} {name}".lower()

    # Keyword sets for the 6 categories
    food_kw = {"food", "recipe", "recipes", "cook", "cooking", "chef", "kitchen", "protein", "calorie", "meal", "bake", "munchies", "masala"}
    health_kw = {"health", "doctor", "dr", "fitness", "fit", "liver", "diet", "nutrition", "gym", "workout", "body", "med", "medical", "wellness", "longevity", "muscle", "biohack", "clinic", "cardio", "cardiologist", "rehab", "therapy", "mobility", "psychotherapy"}
    finance_kw = {"finance", "money", "invest", "investing", "investor", "tax", "taxation", "ca", "nri", "wealth", "stock", "stocks", "market", "trading", "trader", "economy", "paisa", "credit", "cfp", "equity"}
    tech_kw = {"tech", "ai", "code", "coding", "developer", "software", "product", "data", "robot", "robotics", "crypto", "computer", "hardware", "phone", "cloud", "engineering", "deepmind", "openai", "perplexity", "sora", "chatgpt", "gemini", "nvidia", "mkbhd", "gadget", "gadgets"}
    entertainment_kw = {"movie", "movies", "cinema", "film", "review", "reviews", "comedy", "comedian", "standup", "actor", "acting", "humor", "satire", "sketch", "sketches", "joke", "jokes", "drama", "series", "entertainment", "reels", "boardgame", "tapes"}
    niche_kw = {"science", "physics", "math", "learn", "explainer", "why", "facts", "study", "analysis", "school", "author", "book", "books", "philosophy", "creative", "artist", "architectural", "design", "geo", "natgeo", "unplugged", "veritasium"}

    # Direct handle matching
    if any(k in clean_h for k in ["recipe", "cook", "food", "munchies", "masala"]):
        return "food"
    if any(k in clean_h for k in ["doc", "dr", "fit", "health", "diet", "cardiologist"]):
        return "health"
    if any(k in clean_h for k in ["finance", "ca", "tax", "invest", "money", "trader", "paisa"]):
        return "finance"
    if any(k in clean_h for k in ["ai", "tech", "code", "dev", "deepmind", "openai", "perplexity", "mkbhd"]):
        return "ai_tech"
    if any(k in clean_h for k in ["movie", "cinema", "comedy", "standup", "actor"]):
        return "entertainment"
    if any(k in clean_h for k in ["veritasium", "kurzgesagt", "3blue1brown", "cleoabram"]):
        return "niche"

    tokens = set(re.findall(r"\w+", text))
    if tokens & food_kw:
        return "food"
    if tokens & health_kw:
        return "health"
    if tokens & finance_kw:
        return "finance"
    if tokens & tech_kw:
        return "ai_tech"
    if tokens & entertainment_kw:
        return "entertainment"
    if tokens & niche_kw:
        return "niche"

    # Default fallback bucket
    return "entertainment"


def sync_following_accounts(force: bool = False) -> list[dict[str, Any]]:
    """
    Sync followed accounts from authenticated Chrome browser session.
    Filters out private/personal accounts and classifies into 4 thematic buckets.
    Caches results for 30 days unless force=True.
    Non-destructively updates sources.json.
    """
    logger.info("Checking Instagram followed accounts sync (force=%s)...", force)
    cache_file = config.FOLLOWING_CACHE_FILE

    if not force and cache_file.exists():
        try:
            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_at = cache_data.get("timestamp", 0)
            if time.time() - cached_at < 30 * 86400:
                logger.info("Using cached followed accounts (synced %s ago).",
                            timedelta(seconds=int(time.time() - cached_at)))
                return cache_data.get("accounts", [])
        except Exception as exc:
            logger.warning("Could not read following cache: %s", exc)

    # 1. Export fresh cookies from Chrome
    cookie_exporter_script = config.ROOT_DIR / "cookie_exporter.py"
    if cookie_exporter_script.exists():
        try:
            logger.info("Running cookie_exporter to refresh Chrome Instagram session...")
            subprocess.run(["/usr/bin/python3", str(cookie_exporter_script)], check=True, capture_output=True, timeout=15)
        except Exception as exc:
            logger.warning("Could not run cookie_exporter: %s", exc)

    discovered_accounts: list[dict[str, Any]] = []

    # 2. Query Instagram Following API with authenticated cookies
    cookies_json_path = config.DATA_DIR / "cookies.json"
    if cookies_json_path.exists():
        try:
            import requests
            cdata = json.loads(cookies_json_path.read_text(encoding="utf-8"))
            cookies_dict = cdata.get("cookies_dict", {})
            user_id = cookies_dict.get("ds_user_id", "175246825")
            csrftoken = cookies_dict.get("csrftoken", "")

            headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "X-CSRFToken": csrftoken,
                "X-IG-App-ID": "936619743392459",
                "Referer": "https://www.instagram.com/",
            }

            max_id = None
            page_count = 0
            while True:
                url = f"https://www.instagram.com/api/v1/friendships/{user_id}/following/?count=100"
                if max_id:
                    url += f"&max_id={max_id}"
                resp = requests.get(url, headers=headers, cookies=cookies_dict, timeout=15)
                if resp.status_code != 200:
                    logger.warning("Following API returned %d: %s", resp.status_code, resp.text[:100])
                    break
                data = resp.json()
                users = data.get("users", [])
                page_count += 1
                for u in users:
                    # Filter out private/personal accounts
                    if u.get("is_private", False):
                        continue
                    handle = u.get("username", "").strip().lower()
                    name = u.get("full_name", "").strip() or handle
                    if handle:
                        cat = categorize_creator(handle, name)
                        discovered_accounts.append({
                            "handle": handle,
                            "name": name,
                            "category": cat,
                            "enabled": True,
                        })
                max_id = data.get("next_max_id")
                if not max_id:
                    break
            logger.info("Retrieved %d public channels from user's Instagram following list across %d pages.",
                        len(discovered_accounts), page_count)
        except Exception as exc:
            logger.warning("Error querying Instagram Following API: %s", exc)

    # 3. Check for local Instagram data export file (following.json) fallback
    if not discovered_accounts:
        export_candidates = [
            config.DATA_DIR / "following.json",
            config.ROOT_DIR / "following.json",
        ]
        for export_file in export_candidates:
            if export_file.exists():
                logger.info("Found Instagram export file at %s. Parsing...", export_file)
                try:
                    raw = json.loads(export_file.read_text(encoding="utf-8"))
                    items = raw.get("relationships_following", raw) if isinstance(raw, dict) else raw
                    if isinstance(items, list):
                        for entry in items:
                            str_data = entry.get("string_list_data", [])
                            val = str_data[0].get("value") if str_data else entry.get("value")
                            if val:
                                h = val.strip().lower()
                                discovered_accounts.append({
                                    "handle": h,
                                    "name": val.strip(),
                                    "category": categorize_creator(h, val.strip()),
                                    "enabled": True
                                })
                    logger.info("Parsed %d accounts from data export.", len(discovered_accounts))
                except Exception as e:
                    logger.warning("Failed parsing export file %s: %s", export_file, e)

    # Merge discovered accounts non-destructively with existing sources.json
    current_sources = load_sources()
    current_by_handle = {s["handle"].lower(): s for s in current_sources if "handle" in s}
    blacklist = get_blacklisted_creators()

    for acc in discovered_accounts:
        handle = acc["handle"].lower()
        if handle in blacklist:
            continue
        if handle not in current_by_handle:
            current_sources.append(acc)
            current_by_handle[handle] = acc
        else:
            # Preserve user-customized category / enabled status if already set
            existing = current_by_handle[handle]
            if "category" not in existing or not existing["category"]:
                existing["category"] = acc["category"]

    save_sources(current_sources)

    cache_payload = {
        "timestamp": time.time(),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "accounts": current_sources
    }
    cache_file.write_text(json.dumps(cache_payload, indent=2), encoding="utf-8")
    logger.info("Following sync complete. Tracking %d active sources.", len(current_sources))
    return current_sources


def parse_view_count_text(text: str) -> int:
    """Parse Instagram view count strings like '4.5M', '850K', '1,200' into integers."""
    if not text:
        return 0
    clean = text.strip().upper().replace(",", "")
    m = re.search(r"([\d.]+)\s*([KM]?)", clean)
    if not m:
        return 0
    try:
        val = float(m.group(1))
        unit = m.group(2)
        if unit == "M":
            return int(val * 1_000_000)
        elif unit == "K":
            return int(val * 1_000)
        return int(val)
    except ValueError:
        return 0


class InstagramSession:
    """Reusable Playwright browser session for high-speed, rate-limit-resistant extraction."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> InstagramSession:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def start(self) -> None:
        if not self._playwright:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(user_agent=DEFAULT_USER_AGENT)

            # Inject authenticated cookies if available
            cookies_file = config.DATA_DIR / "cookies.json"
            if cookies_file.exists():
                try:
                    cdata = json.loads(cookies_file.read_text(encoding="utf-8"))
                    pw_cookies = cdata.get("cookies_playwright", [])
                    if pw_cookies:
                        self._context.add_cookies(pw_cookies)
                        logger.debug("Injected %d cookies into Playwright context.", len(pw_cookies))
                except Exception as exc:
                    logger.warning("Could not inject cookies: %s", exc)

            self._page = self._context.new_page()

    def close(self) -> None:
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

    def get_page(self):
        self.start()
        return self._page


def discover_creator_reel_urls(
    handle: str,
    max_reels: int = 10,
    session: InstagramSession | None = None,
) -> list[dict[str, Any]]:
    """
    Use headless Playwright to load creator's reels tab and extract recent reel URLs + view counts.
    Immune to broken yt-dlp profile extractors and API 429 blocks.
    """
    clean_handle = handle.lstrip("@").strip().lower()
    target_url = f"https://www.instagram.com/{clean_handle}/reels/"
    reels_found: list[dict[str, Any]] = []

    logger.info("Discovering reels for @%s via Playwright...", clean_handle)
    local_session = None
    try:
        if session:
            page = session.get_page()
        else:
            local_session = InstagramSession()
            page = local_session.get_page()

        page.goto(target_url, wait_until="domcontentloaded", timeout=25000)

        try:
            page.wait_for_selector("a[href*='/reel/']", timeout=5000)
        except Exception:
            pass

        anchors = page.locator("a[href*='/reel/']").all()
        for a in anchors:
            href = a.get_attribute("href") or ""
            m = re.search(r"/(?:[a-zA-Z0-9._]+/)?reel/([a-zA-Z0-9_-]+)/?", href)
            if m:
                # Check for pinned reel indicators (Instagram pin icon / aria-labels)
                is_pinned = False
                try:
                    if a.locator('svg[aria-label*="Pin" i], svg[aria-label*="pin" i]').count() > 0:
                        is_pinned = True
                    else:
                        a_html = a.inner_html()
                        if re.search(r'aria-label=[\'"][^\'"]*pin[^\'"]*[\'"]', a_html, re.I):
                            is_pinned = True
                except Exception:
                    pass

                if is_pinned:
                    logger.info("Skipping pinned reel %s for @%s", href, clean_handle)
                    continue

                shortcode = m.group(1)
                full_url = f"https://www.instagram.com/reel/{shortcode}/"
                views_text = a.inner_text().strip()
                view_count = parse_view_count_text(views_text)

                # Extract thumbnail image if present in inner HTML
                html = a.inner_html()
                img_match = re.search(r'url\(["\']?(https://[^"\')]+)["\']?\)', html)
                thumb_url = img_match.group(1) if img_match else ""

                if not any(r["id"] == shortcode for r in reels_found):
                    reels_found.append({
                        "id": shortcode,
                        "url": full_url,
                        "creator_handle": clean_handle,
                        "view_count": view_count,
                        "thumbnail": thumb_url,
                    })

            if len(reels_found) >= max_reels:
                break
    except Exception as exc:
        logger.warning("Playwright reel link discovery exception for @%s: %s", clean_handle, exc)
    finally:
        if local_session:
            local_session.close()

    logger.info("Discovered %d reels for @%s", len(reels_found), clean_handle)
    return reels_found


def extract_single_reel_metadata(
    reel_info: dict[str, Any],
    session: InstagramSession | None = None,
) -> dict[str, Any] | None:
    """
    Extract full metadata and direct CDN progressive MP4 stream for an individual reel.
    Uses Playwright for bot-resistant OpenGraph parsing, falling back to yt-dlp.
    """
    reel_url = reel_info["url"]
    creator_handle = reel_info["creator_handle"]
    shortcode = reel_info.get("id", "")

    # 1. Attempt high-speed Playwright extraction (bypasses broken yt-dlp & login walls)
    local_session = None
    try:
        if session:
            page = session.get_page()
        else:
            local_session = InstagramSession()
            page = local_session.get_page()

        page.goto(reel_url, wait_until="domcontentloaded", timeout=18000)

        # Dismiss any occasional login or cookie dialog by pressing Escape
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        html = page.content()

        og_title = page.query_selector('meta[property="og:title"]')
        og_desc = page.query_selector('meta[property="og:description"]')
        og_image = page.query_selector('meta[property="og:image"]')

        title_text = og_title.get_attribute("content") if og_title else ""
        desc_text = og_desc.get_attribute("content") if og_desc else ""
        thumb_url = og_image.get_attribute("content") if og_image else reel_info.get("thumbnail", "")

        like_count = int(reel_info.get("view_count", 10000) * 0.08)
        comment_count = int(reel_info.get("view_count", 10000) * 0.005)
        caption = title_text
        timestamp = 0

        # 1. Primary: Extract exact ISO timestamp from <time datetime="..."> in DOM
        try:
            time_elem = page.query_selector("time[datetime]")
            if time_elem:
                dt_attr = time_elem.get_attribute("datetime")
                if dt_attr:
                    clean_dt = dt_attr.replace("Z", "+00:00")
                    timestamp = int(datetime.fromisoformat(clean_dt).timestamp())
        except Exception:
            pass

        # 2. Secondary: Parse date from og:description
        if not timestamp and desc_text:
            m = re.search(r"([\d.,]+[KMkm]?)\s+likes,\s+([\d.,]+[KMkm]?)\s+comments\s+-\s+([^\s]+)\s+on\s+([^:]+):\s*(.*)", desc_text)
            if m:
                l_str, c_str, user, date_str, cap = m.groups()
                like_count = parse_view_count_text(l_str)
                comment_count = parse_view_count_text(c_str)
                clean_cap = cap.strip(" \"'")
                if clean_cap:
                    caption = clean_cap
                try:
                    dt = datetime.strptime(date_str.strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
                    timestamp = int(dt.timestamp())
                except ValueError:
                    pass

        # 3. Tertiary: Check ld+json uploadDate
        if not timestamp:
            try:
                ld_scripts = page.query_selector_all('script[type="application/ld+json"]')
                for s in ld_scripts:
                    s_content = s.inner_text()
                    if "uploadDate" in s_content:
                        ld_data = json.loads(s_content)
                        if isinstance(ld_data, dict) and "uploadDate" in ld_data:
                            dt = datetime.fromisoformat(ld_data["uploadDate"].replace("Z", "+00:00"))
                            timestamp = int(dt.timestamp())
                            break
            except Exception:
                pass

        # Find direct progressive MP4 stream in HTML
        candidates = [
            part.replace(r"\/", "/").replace(r"\u0026", "&")
            for part in html.split('"')
            if ".mp4" in part and "scontent" in part and "BaseURL" not in part and len(part) > 120
        ]
        video_cdn_url = candidates[0] if candidates else ""

        return {
            "id": shortcode or reel_info["id"],
            "url": reel_url,
            "creator_handle": creator_handle,
            "caption": caption or f"Reel by @{creator_handle}",
            "view_count": reel_info.get("view_count") or like_count * 10,
            "like_count": like_count,
            "comment_count": comment_count,
            "duration": 30,
            "timestamp": timestamp,
            "thumbnail": thumb_url,
            "video_cdn_url": video_cdn_url,
        }
    except Exception as exc:
        logger.debug("Playwright extraction failed on %s: %s; trying yt-dlp fallback...", reel_url, exc)
    finally:
        if local_session:
            local_session.close()

    # 2. Fallback to yt-dlp
    cmd = [
        "yt-dlp",
        "--user-agent", DEFAULT_USER_AGENT,
        "--referer", "https://www.instagram.com/",
        "--dump-single-json",
        "--no-warnings",
        "--no-check-certificates",
        reel_url,
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            reel_id = str(data.get("id") or reel_info["id"])
            views = int(data.get("view_count") or data.get("play_count") or reel_info.get("view_count", 0))

            return {
                "id": reel_id,
                "url": reel_url,
                "creator_handle": creator_handle,
                "caption": (data.get("description") or data.get("title") or "").strip(),
                "view_count": views,
                "like_count": int(data.get("like_count") or 0),
                "comment_count": int(data.get("comment_count") or 0),
                "duration": data.get("duration") or 0,
                "timestamp": int(data.get("timestamp") or 0),
                "thumbnail": data.get("thumbnail") or reel_info.get("thumbnail", ""),
                "video_cdn_url": data.get("url", ""),
            }
    except Exception as exc:
        logger.warning("yt-dlp fallback failed for %s: %s", reel_url, exc)

    # 3. Last-resort fallback to basic reel info
    return {
        "id": reel_info["id"],
        "url": reel_url,
        "creator_handle": creator_handle,
        "caption": f"Reel by @{creator_handle}",
        "view_count": reel_info.get("view_count", 10000),
        "like_count": int(reel_info.get("view_count", 10000) * 0.08),
        "comment_count": int(reel_info.get("view_count", 10000) * 0.005),
        "duration": 30,
        "timestamp": reel_info.get("timestamp") or 0,
        "thumbnail": reel_info.get("thumbnail", ""),
        "video_cdn_url": "",
    }


def extract_creator_reels(
    handle: str,
    max_reels: int = 10,
    days_back: int = 7,
    use_cookies: bool = True,
    fast_mode: bool = False,
    session: InstagramSession | None = None,
) -> list[dict[str, Any]]:
    """
    Extract recent reels and metrics for a creator:
    1. Discovers recent reels via Playwright.
    2. Fetches metadata and CDN streams (or fast_mode for dry-runs).
    3. Filters to posts within days_back window.
    """
    clean_handle = handle.lstrip("@").strip()
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days_back)
    cutoff_timestamp = int(cutoff_dt.timestamp())

    if session is not None:
        reels_info = discover_creator_reel_urls(clean_handle, max_reels=max_reels, session=session)
    else:
        reels_info = discover_creator_reel_urls(clean_handle, max_reels=max_reels)
    if not reels_info:
        return []

    results: list[dict[str, Any]] = []
    for info in reels_info:
        if fast_mode:
            results.append({
                "id": info["id"],
                "url": info["url"],
                "creator_handle": clean_handle,
                "caption": f"Reel by @{clean_handle}",
                "view_count": info.get("view_count", 10000),
                "like_count": int(info.get("view_count", 10000) * 0.08),
                "comment_count": int(info.get("view_count", 10000) * 0.005),
                "duration": 30,
                "timestamp": int(time.time()),
                "thumbnail": info.get("thumbnail", ""),
                "video_cdn_url": "",
            })
            continue

        if session is not None:
            meta = extract_single_reel_metadata(info, session=session)
        else:
            meta = extract_single_reel_metadata(info)
        if not meta:
            continue

        ts = meta.get("timestamp") or 0
        if not ts or ts < cutoff_timestamp:
            logger.info("Discarding reel %s: timestamp %s older than %d-day cutoff %s (or missing)",
                        meta.get("id"), ts, days_back, cutoff_timestamp)
            continue

        results.append(meta)

    return results


def download_reel_video(
    reel_url: str,
    output_path: Path,
    video_cdn_url: str | None = None,
    session: InstagramSession | None = None,
    use_cookies: bool = True,
    max_retries: int = 3,
) -> bool:
    """
    Download a single reel video to output_path.
    1. If video_cdn_url is provided, stream-downloads directly with requests (fast & resilient).
    2. Otherwise uses Playwright to extract video_cdn_url and stream-download.
    3. Falls back to yt-dlp if direct stream fails.
    """
    import requests

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp.mp4")

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": "https://www.instagram.com/",
        "Accept": "*/*",
    }

    # 1. Resolve CDN URL if not provided
    cdn_target = video_cdn_url
    if not cdn_target:
        meta = extract_single_reel_metadata({"id": "probe", "url": reel_url, "creator_handle": ""}, session=session)
        if meta and meta.get("video_cdn_url"):
            cdn_target = meta["video_cdn_url"]

    # 2. Direct streaming download from Instagram CDN
    if cdn_target:
        for attempt in range(1, max_retries + 1):
            try:
                logger.info("Downloading reel stream (attempt %d/%d): %s...", attempt, max_retries, reel_url)
                with requests.get(cdn_target, headers=headers, stream=True, timeout=45) as r:
                    if r.status_code == 200:
                        with open(temp_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)

                        if temp_path.exists() and temp_path.stat().st_size > 50000:
                            temp_path.replace(output_path)
                            logger.info("Successfully downloaded %.2f MB to %s",
                                        output_path.stat().st_size / (1024 * 1024), output_path.name)
                            return True
                        else:
                            logger.warning("Downloaded stream too small (%d bytes), retrying...",
                                           temp_path.stat().st_size if temp_path.exists() else 0)
                    else:
                        logger.warning("Stream request returned status %d", r.status_code)
            except Exception as exc:
                logger.warning("Stream download exception on attempt %d: %s", attempt, exc)

            time.sleep(1.0 * attempt)

    # 3. Fallback to yt-dlp
    logger.info("Direct stream failed; falling back to yt-dlp for %s", reel_url)
    cmd = [
        "yt-dlp",
        "--user-agent", DEFAULT_USER_AGENT,
        "--referer", "https://www.instagram.com/",
        "-f", "b[ext=mp4]/bv*[ext=mp4]+ba[ext=m4a]/b",
        "--no-warnings",
        "-o", str(temp_path),
        reel_url,
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if res.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 50000:
            temp_path.replace(output_path)
            return True
    except Exception as exc:
        logger.error("yt-dlp fallback failed: %s", exc)

    if temp_path.exists():
        temp_path.unlink(missing_ok=True)
    return False
