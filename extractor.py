"""
extractor.py — Ingests Instagram followed accounts and extracts weekly reels & metadata.
Uses Playwright for bot-resistant profile reel discovery + yt-dlp for single reel metadata & downloads.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import sync_playwright

import config

logger = logging.getLogger("InstagramDigest.Extractor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_YTDLP_SEMAPHORE = threading.Semaphore(2)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Per-context rotation pool (Chrome engine only: a non-Chrome UA on a
# Chromium engine is itself a fingerprint mismatch). Keeps the automation
# signal from being a single static string while staying engine-plausible.
USER_AGENT_POOL = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)

VIEWPORT_POOL = ((1280, 800), (1366, 768), (1440, 900), (1536, 864), (1920, 1080))
LOCALE_POOL = ("en-US", "en-GB")
TIMEZONE_POOL = ("America/New_York", "Europe/London", "Asia/Kolkata")

# Safety cap for the Following-API pagination loop: a normal account never
# needs more than a handful of 100-user pages; the cap only stops a runaway
# loop (e.g. a cycling max_id served to a flagged session).
MAX_FOLLOWING_PAGES = 40


def _chrome_major_from_ua(ua: str) -> str:
    """Extract the Chrome major version from a UA string (default: 120)."""
    m = re.search(r"Chrome/(\d+)", ua or "")
    return m.group(1) if m else "120"


def _client_hint_headers(ua: str) -> dict[str, str]:
    """Sec-CH-UA client hints matching the given Chrome UA major version.

    Sending a Chrome/120+ UA without these hints is a mismatch signal:
    real Chrome always emits them. The brand list mirrors what Chrome sends
    (Chromium + Google Chrome + Not-A.Brand).
    """
    major = _chrome_major_from_ua(ua)
    return {
        "Sec-CH-UA": (
            f'"Chromium";v="{major}", "Google Chrome";v="{major}", '
            '"Not-A.Brand";v="99"'
        ),
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
    }

# Minimal webdriver-masking init script (hides the most trivial headless
# signals; not a full stealth framework, but removes the zero-effort tells).
# NOTE (known limit): the UA override below does not rewrite the
# Sec-CH-UA client-hint headers Playwright's bundled Chromium sends, so a
# pinned Chrome 130/131 UA can still mismatch the real engine version. The
# pool stays Chrome-only to avoid the worse mismatch of a non-Chrome UA on
# a Chromium engine.
def _stealth_script_for_locale(locale: str) -> str:
    """Build the masking snippet with languages matching the context locale."""
    base = (locale or "en-US").strip() or "en-US"
    short = base.split("-")[0]
    langs = [base] if base == short else [base, short]
    langs_js = "[" + ", ".join(f"'{l}'" for l in langs) + "]"
    return """() => {
  try {
    try {
      Object.defineProperty(navigator, 'webdriver', { get: () => false });
    } catch (e) {}
    try {
      if (!window.chrome) { window.chrome = { runtime: {} }; }
      if (!window.chrome.runtime) { window.chrome.runtime = {}; }
      if (!window.chrome.loadTimes) {
        window.chrome.loadTimes = function () {
          const t = Date.now() / 1000;
          return { requestTime: t, startLoadTime: t, commitLoadTime: t,
            finishDocumentLoadTime: t, finishLoadTime: t, firstPaintTime: t,
            layoutType: 'Blink', navigationType: 'Other',
            wasAlternateProtocolAvailable: false, wasFetchedViaSpdy: true,
            wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2' };
        };
      }
      if (!window.chrome.csi) {
        window.chrome.csi = function () {
          return { startE: Date.now(), onloadT: Date.now(), pageT: 120, tran: 15 };
        };
      }
    } catch (e) {}
    try {
      const _fakePlugins = {
        length: 3,
        item(i) { return this[i] || null; },
        namedItem(n) { return this[n] || null; },
        refresh() {},
        0: { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
        1: { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        2: { name: 'Native Client', filename: 'internal-nacl-plugin' },
      };
      Object.defineProperty(navigator, 'plugins', { get: () => _fakePlugins });
      if (navigator.plugins.length === 0) { throw new Error('plugins guard'); }
    } catch (e) {}
    try {
      const _cores = 4 + Math.floor(Math.random() * 5);
      Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => _cores });
      Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    } catch (e) {}
    try {
      const _spoofGL = function (orig) {
        return function (p) {
          if (p === 37445) return 'Google Inc. (Intel)';
          if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00005917) Direct3D11 vs_5_0 ps_5_0, D3D11)';
          return orig.call(this, p);
        };
      };
      if (window.WebGLRenderingContext) {
        WebGLRenderingContext.prototype.getParameter = _spoofGL(WebGLRenderingContext.prototype.getParameter);
      }
      if (window.WebGL2RenderingContext) {
        WebGL2RenderingContext.prototype.getParameter = _spoofGL(WebGL2RenderingContext.prototype.getParameter);
      }
    } catch (e) {}
    try {
      if (navigator.permissions && navigator.permissions.query) {
        const _permQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = function (params) {
          if (params && params.name === 'notifications') {
            return Promise.resolve({ state: 'default', onchange: null });
          }
          return _permQuery(params);
        };
      }
    } catch (e) {}
    try {
      Object.defineProperty(navigator, 'languages', { get: () => LANGS });
    } catch (e) {}
  } catch (e) {}
}""".replace("LANGS", langs_js)


STEALTH_INIT_SCRIPT = _stealth_script_for_locale("en-US")


# Feed-discovery pacing (low-profile after the automation warning): rest more
# often and longer between bursts of reel evaluations.
FEED_COOLDOWN_EVERY = (8, 14)  # evaluations between rest breaks
FEED_COOLDOWN_SECS = (35.0, 8.0, 20.0)  # mu, sigma, floor seconds per break


def human_pause(mu: float = 3.8, sigma: float = 1.1, floor: float = 1.5) -> float:
    """Sleep a Gaussian-distributed human-like pause with an occasional long tail.

    Uniform fixed-range jitter is a classifier feature; Gaussian + 10%
    long-tail pauses mimic real reading dwell time. Returns seconds slept.
    """
    pause = max(floor, random.gauss(mu, sigma))
    if random.random() < 0.10:
        pause += random.uniform(4.0, 9.0)
    time.sleep(pause)
    return pause


def _cookie_python() -> str:
    """Interpreter for cookie_exporter.py: the system one carries dbus/cryptography."""
    import sys
    return "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


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
        except Exception as exc:
            # Quarantine for forensics; fail open (muted creators reappear)
            # rather than failing closed, and never silently discard bytes.
            try:
                from datetime import timezone as _tz, datetime as _dt
                ts = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = config.BLACKLIST_FILE.with_name(
                    f"{config.BLACKLIST_FILE.name}.corrupt-{ts}")
                backup.write_bytes(config.BLACKLIST_FILE.read_bytes())
                logger.warning("Quarantined corrupt %s to %s: %s",
                               config.BLACKLIST_FILE, backup, exc)
            except Exception:
                logger.warning("Unreadable %s; treating blacklist as empty: %s",
                               config.BLACKLIST_FILE, exc)
    return set()


def load_sources() -> list[dict[str, Any]]:
    """Load tracked creators from sources.json, excluding blacklisted channels.

    A corrupt file is quarantined for forensics (never silently replaced with
    [] — the run would otherwise abort at "no active sources" with bytes lost).
    Non-list payloads and entries without a handle are rejected, since the
    ranker keys everything off creator_handle.
    """
    if not config.SOURCES_FILE.exists():
        return []
    try:
        raw = config.SOURCES_FILE.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Error reading sources.json: %s", exc)
        return []
    try:
        sources = json.loads(raw)
    except Exception as exc:
        logger.error("Error parsing sources.json: %s", exc)
        _quarantine_sources_file(exc)
        return []
    if not isinstance(sources, list):
        logger.error("sources.json is not a list (%s); quarantining.", type(sources).__name__)
        _quarantine_sources_file(ValueError("sources.json payload is not a list"))
        return []
    valid_sources: list[dict[str, Any]] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        if not clean_handle(s.get("handle")):
            logger.warning("Skipping sources.json entry without a handle: %r", s)
            continue
        valid_sources.append(s)
    blacklist = get_blacklisted_creators()
    if blacklist:
        return [s for s in valid_sources if s.get("handle", "").lower().replace("@", "") not in blacklist]
    return valid_sources


def _quarantine_sources_file(exc: Exception) -> None:
    """Preserve corrupt sources.json bytes alongside for forensics."""
    try:
        from datetime import datetime as _dt, timezone as _tz
        ts = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = config.SOURCES_FILE.with_name(f"{config.SOURCES_FILE.name}.corrupt-{ts}")
        backup.write_bytes(config.SOURCES_FILE.read_bytes())
        logger.warning("Quarantined corrupt %s to %s: %s", config.SOURCES_FILE, backup, exc)
    except Exception:
        logger.warning("Unreadable %s; treating sources as empty: %s", config.SOURCES_FILE, exc)


def save_sources(sources: list[dict[str, Any]]) -> None:
    """Save updated creators list to sources.json (crash-safe)."""
    import atomic_io
    atomic_io.durable_write_json(config.SOURCES_FILE, sources)


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
            subprocess.run([_cookie_python(), str(cookie_exporter_script)], check=True, capture_output=True, timeout=15)
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
            user_id = str(cookies_dict.get("ds_user_id") or "").strip()
            if not user_id.isdigit():
                raise RuntimeError("ds_user_id cookie missing; refusing to query another account's following list")
            csrftoken = cookies_dict.get("csrftoken", "")

            headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "X-CSRFToken": csrftoken,
                "X-IG-App-ID": "936619743392459",
                "Referer": "https://www.instagram.com/",
                **_client_hint_headers(DEFAULT_USER_AGENT),
            }

            max_id = None
            page_count = 0
            api_failures = 0
            while True:
                if page_count >= MAX_FOLLOWING_PAGES:
                    logger.warning(
                        "Following pagination hit safety cap (%d pages); stopping.",
                        MAX_FOLLOWING_PAGES,
                    )
                    break
                url = f"https://www.instagram.com/api/v1/friendships/{user_id}/following/?count=100"
                if max_id:
                    url += f"&max_id={max_id}"
                try:
                    resp = requests.get(url, headers=headers, cookies=cookies_dict, timeout=15)
                except Exception as req_exc:
                    api_failures += 1
                    if api_failures > 3:
                        logger.warning("Following API unreachable after 3 attempts: %s", req_exc)
                        break
                    backoff = min(60.0, 2.0 ** api_failures + random.uniform(0, 1))
                    logger.warning("Following API error (%s); backing off %.1fs.", req_exc, backoff)
                    time.sleep(backoff)
                    continue
                if resp.status_code != 200:
                    api_failures += 1
                    if api_failures > 3:
                        logger.warning("Following API returned %d: %s", resp.status_code, resp.text[:100])
                        break
                    backoff = min(60.0, 2.0 ** api_failures + random.uniform(0, 1))
                    logger.warning("Following API returned %d; backing off %.1fs.", resp.status_code, backoff)
                    time.sleep(backoff)
                    continue
                api_failures = 0
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
                if data.get("has_more") is False:
                    # API explicitly signals end of list; ignore any stale cursor.
                    break
                # Humanized inter-page pacing (was: no delay at all).
                time.sleep(max(0.8, random.gauss(1.4, 0.5)))
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
    import atomic_io
    atomic_io.durable_write_json(cache_file, cache_payload)
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


class InstagramBlocked(RuntimeError):
    """Instagram served a login/challenge wall instead of content."""


class CookieExpiredException(RuntimeError):
    """Instagram session cookies are missing or expired (redirected to login).

    Carries `partial`: reels already discovered before the session died, so
    callers can checkpoint them and resume instead of losing the run's work.
    """

    def __init__(self, message: str = "", partial: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.partial: list[dict[str, Any]] = list(partial or [])


_BLOCK_MARKERS = (
    "/accounts/login",
    "/challenge/",
    "/accounts/suspended",
    "/checkpoint/",
    "checkpoint_required",
    "rate_limit",
    "limited_action",
    # Out-of-band risky-contactpoint challenge served to low-trust accounts
    # ("email may not be secure"). Any redirect here must abort loudly via
    # InstagramBlocked, never spin as a silent scrape loop.
    "/update_risky_contactpoint",
    "risky_contactpoint",
    "verify_contactpoint",
    "/accounts/confirm",
    "checkpoint",
)

# Soft-block tells served with HTTP 200 (no redirect to catch).
_SOFT_BLOCK_SNIPPETS = (
    "challenge_required",
    "feedback_required",
    "login_required",
    "suspicious login attempt",
    "try again later",
    "unusual activity",
    "we limit how often",
    "temporarily blocked",
    "log in to continue",
)

# Regions whose text must never count as a block signal: JS bundles (which
# embed API error-code enums such as login_required as plain strings),
# styles, HTML comments, and paragraph-level user content (reel captions and
# comments, e.g. a recipe reading "try again later with less heat").
_NONBLOCK_CONTENT_RE = re.compile(
    r"<!--.*?-->"
    r"|<script\b.*?</script\s*>"
    r"|<style\b.*?</style\s*>"
    r"|<p\b.*?</p\s*>"
    r"|<figcaption\b.*?</figcaption\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _page_html_indicates_block(html: str) -> bool:
    text = _NONBLOCK_CONTENT_RE.sub(" ", html or "")
    return any(s in text.lower() for s in _SOFT_BLOCK_SNIPPETS)


def _assert_not_blocked(page, context: str) -> None:
    url = getattr(page, "url", "") or ""
    if any(m in url for m in _BLOCK_MARKERS):
        raise InstagramBlocked(f"{context}: redirected to {url}")


_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y", "%m/%d/%Y")


def _parse_date_flexible(date_str: str) -> int:
    """Parse reel dates across locales/formats; 0 when unparseable."""
    s = (date_str or "").strip()
    if not s:
        return 0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


# Ordered fallback chain: the entries must be disjoint. (A previous revision
# used "a[href*='/reel']" second, a pure superset of the first, so the
# fallback could never match anything new.)
_DISCOVERY_SELECTORS = ("a[href*='/reel/']", "a[href*='/reels/']")


def _extract_shortcode(href: str) -> str:
    """Pull the reel shortcode from href variants (handles /user/reel/X/,
    query strings, and missing trailing slash)."""
    m = re.search(r"reel/([A-Za-z0-9_-]+)", href or "")
    return m.group(1) if m else ""


_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")


def clean_handle(raw: Any) -> str:
    """Instagram usernames are 1-30 chars of [a-z0-9._]. Anything else (page
    text mistaken for a handle, '<img...>', '../..') is rejected as empty so it
    can never reach a filename, an R2 key, a D1 row or a DOM sink."""
    h = str(raw or "").strip().lower().lstrip("@")
    return h if _HANDLE_RE.fullmatch(h) else ""


class InstagramSession:
    """Reusable Playwright browser session for high-speed, rate-limit-resistant extraction."""

    RECYCLE_EVERY = 40

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._nav_count = 0
        # Pinned per authenticated session: rotating the timezone (or the
        # locale independently of navigator.languages) across contexts on a
        # single account is an account-linking anomaly, not camouflage.
        self._locale: str | None = None
        self._timezone_id: str | None = None

    def __enter__(self) -> InstagramSession:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _inject_cookies(self) -> None:
        if not self._context:
            return
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

    def _open_context(self) -> None:
        width, height = random.choice(VIEWPORT_POOL)
        if self._locale is None:
            self._locale = random.choice(LOCALE_POOL)
        if self._timezone_id is None:
            self._timezone_id = random.choice(TIMEZONE_POOL)
        self._context = self._browser.new_context(
            user_agent=random.choice(USER_AGENT_POOL),
            viewport={"width": width, "height": height},
            locale=self._locale,
            timezone_id=self._timezone_id,
            device_scale_factor=1,
        )
        try:
            self._context.add_init_script(_stealth_script_for_locale(self._locale))
        except Exception:
            pass
        self._inject_cookies()
        self._page = self._context.new_page()
        self._nav_count = 0

    def start(self) -> None:
        if not self._playwright:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._open_context()

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
            self._nav_count = 0

    def validate(self, url: str = "https://www.instagram.com/") -> bool:
        """Lightweight login check: True if the session looks authenticated.

        Loads one page and applies the same block markers as extraction.
        Never raises: any failure means "not usable", never "usable".
        """
        if not self._page:
            return False
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc:
            logger.warning("Session validation navigation failed: %s", exc)
            return False
        try:
            _assert_not_blocked(self._page, "session-validation")
        except InstagramBlocked:
            return False
        except Exception as exc:
            logger.warning("Session validation check failed: %s", exc)
            return False
        return True

    def get_page(self):
        self.start()
        if self._nav_count >= self.RECYCLE_EVERY:
            logger.info("Recycling Playwright context after %d navigations.", self._nav_count)
            try:
                if self._page:
                    self._page.close()
                if self._context:
                    self._context.close()
            except Exception:
                pass
            self._open_context()
        self._nav_count += 1
        return self._page

    def new_isolated_page(self):
        """Create a dedicated secondary tab inside the existing context for metadata inspections.

        Does not bump the context navigation recycle counter and keeps the caller's primary
        page untouched. The caller MUST close the returned page in a finally block.
        """
        self.start()
        if not self._context:
            self._open_context()
        page = self._context.new_page()
        page.set_default_navigation_timeout(20000)
        return page


def discover_creator_reel_urls(
    handle: str,
    max_reels: int = 10,
    session: InstagramSession | None = None,
    include_pinned: bool = False,
) -> list[dict[str, Any]]:
    """
    Use headless Playwright to load creator's reels tab and extract recent reel URLs + view counts.
    Immune to broken yt-dlp profile extractors and API 429 blocks.
    """
    clean_handle = handle.lstrip("@").strip().lower()
    target_url = f"https://www.instagram.com/{clean_handle}/reels/"
    reels_found: list[dict[str, Any]] = []

    logger.info("Discovering reels for @%s via Playwright (include_pinned=%s)...", clean_handle, include_pinned)
    local_session = None
    try:
        if session:
            page = session.get_page()
        else:
            local_session = InstagramSession()
            page = local_session.get_page()

        page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        _assert_not_blocked(page, f"@{clean_handle}")

        anchors = []
        for selector in _DISCOVERY_SELECTORS:
            try:
                page.wait_for_selector(selector, timeout=4000)
            except Exception:
                pass
            try:
                anchors = page.locator(selector).all()
            except Exception:
                anchors = []
            if anchors:
                break

        if not anchors:
            # Fail closed: an empty grid may be a soft-block served as 200.
            try:
                probe_html = page.content()
            except Exception:
                probe_html = ""
            if _page_html_indicates_block(probe_html):
                raise InstagramBlocked(f"@{clean_handle}: soft-block markers in empty grid")
            logger.warning("Empty reel grid for @%s with no block markers; treating as zero reels.", clean_handle)
            return reels_found

        for a in anchors:
            href = a.get_attribute("href") or ""
            shortcode = _extract_shortcode(href)
            if shortcode:
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

                if is_pinned and not include_pinned:
                    logger.info("Skipping pinned reel %s for @%s", href, clean_handle)
                    continue

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
                        "is_pinned": is_pinned,
                    })

            if len(reels_found) >= max_reels:
                break
    except InstagramBlocked:
        raise
    except Exception as exc:
        logger.warning("Playwright reel link discovery exception for @%s: %s", clean_handle, exc)
    finally:
        if local_session:
            local_session.close()

    logger.info("Discovered %d reels for @%s", len(reels_found), clean_handle)
    return reels_found


def _shortcode_to_media_id(shortcode: str) -> str:
    """Convert a reel shortcode to its numeric media id (same math as yt-dlp)."""
    table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    code = (shortcode or "")[:28]
    value = 0
    for ch in code:
        value = value * 64 + table.index(ch)
    return str(value)


def fetch_media_info_batch(
    shortcodes: list[str],
    pause_secs: float = 2.0,
    timeout: int = 15,
) -> dict[str, dict[str, Any]]:
    """Fetch full metadata for reel shortcodes via the media/{id}/info/ API.

    One cheap GET per reel (~2.7s incl. pacing) returning timestamp,
    like/comment counts, caption, duration, thumbnail, best video URL and
    play_count — everything the per-reel Playwright visit provides, without
    rendering a page. 429s are honored with Retry-After backoff; failures
    return no entry so callers fall back to per-reel extraction.
    """
    try:
        import requests as _rq
    except ImportError:
        return {}
    try:
        cdata = json.loads((config.DATA_DIR / "cookies.json").read_text(encoding="utf-8"))
        cd = cdata.get("cookies_dict", {})
        sessionid = cd.get("sessionid", "")
    except Exception:
        return {}
    if not sessionid:
        return {}
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "X-IG-App-ID": "936619743392459",
        "Referer": "https://www.instagram.com/",
        **_client_hint_headers(DEFAULT_USER_AGENT),
    }
    sess = _rq.Session()
    sess.cookies.set("sessionid", sessionid, domain=".instagram.com")
    out: dict[str, dict[str, Any]] = {}
    for sc in shortcodes:
        try:
            mid = _shortcode_to_media_id(sc)
        except (ValueError, TypeError):
            continue
        try:
            resp = sess.get(
                f"https://i.instagram.com/api/v1/media/{mid}/info/",
                headers=headers, timeout=timeout,
            )
        except Exception as exc:
            logger.debug("media-info request failed for %s: %s", sc, exc)
            continue
        if resp.status_code == 429:
            wait = 60.0
            try:
                wait = max(wait, float(resp.headers.get("Retry-After") or 0))
            except (TypeError, ValueError):
                pass
            logger.warning("media-info rate-limited (429); backing off %.0fs.", wait)
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            logger.debug("media-info HTTP %d for %s.", resp.status_code, sc)
            continue
        try:
            items = resp.json().get("items") or []
            item = items[0] if items else {}
        except Exception:
            continue
        if not isinstance(item, dict) or not item.get("taken_at"):
            continue
        user = item.get("user") or {}
        caption = item.get("caption") or {}
        versions = item.get("video_versions") or []
        best_url = ""
        best_area = 0
        for v in versions:
            try:
                area = int(v.get("width") or 0) * int(v.get("height") or 0)
            except (TypeError, ValueError):
                area = 0
            if v.get("url") and area >= best_area:
                best_area = area
                best_url = v["url"]
        thumbs = ((item.get("image_versions2") or {}).get("candidates")) or []
        thumb_url = ""
        for t in thumbs:
            if t.get("url"):
                thumb_url = t["url"]
                break
        views = item.get("view_count")
        if views is None:
            views = item.get("play_count") or 0
        out[sc] = {
            "timestamp": int(item.get("taken_at") or 0),
            "like_count": int(item.get("like_count") or 0),
            "comment_count": int(item.get("comment_count") or 0),
            "view_count": int(views or 0),
            "caption": str(caption.get("text") or ""),
            "duration": float(item.get("video_duration") or 0),
            "thumbnail": thumb_url,
            "video_cdn_url": best_url,
            "creator_handle": str(user.get("username") or ""),
            "creator_name": str(user.get("full_name") or user.get("username") or ""),
            "metrics_estimated": False,
        }
        time.sleep(pause_secs + random.uniform(0, 1.0))
    return out


def enrich_candidates_via_media_api(
    candidates: list[dict[str, Any]],
    cutoff_timestamp: int = 0,
) -> list[dict[str, Any]]:
    """Batch-enrich discovery candidates via media/{id}/info/ (no browser).

    Applies the date cutoff BEFORE ranking so stale reels never burn a
    Playwright visit: reels older than cutoff_timestamp (or dateless) are
    dropped here, where each check cost one cheap GET instead of a ~7s page
    load. Entries the API misses keep their discovery fields for the
    per-reel fallback path. Pinned reels bypass the cutoff.
    """
    if not candidates:
        return []
    shortcodes = [str(c.get("id") or "") for c in candidates if c.get("id")]
    info_map = fetch_media_info_batch(shortcodes)
    if not info_map:
        logger.warning("media-info batch returned nothing; keeping candidates for per-reel fallback.")
        return list(candidates)
    enriched: list[dict[str, Any]] = []
    dropped_stale = 0
    for cand in candidates:
        sc = str(cand.get("id") or "")
        info = info_map.get(sc)
        if not info:
            enriched.append(cand)
            continue
        merged = dict(cand)
        merged.update(info)
        merged["view_count"] = merged.get("view_count") or cand.get("view_count", 0)
        merged["thumbnail"] = merged.get("thumbnail") or cand.get("thumbnail", "")
        merged["video_cdn_url"] = merged.get("video_cdn_url") or cand.get("video_cdn_url", "")
        if not merged.get("creator_handle"):
            merged["creator_handle"] = cand.get("creator_handle", "")
        ts = merged.get("timestamp") or 0
        if not cand.get("is_pinned") and cutoff_timestamp and (not ts or ts < cutoff_timestamp):
            dropped_stale += 1
            continue
        enriched.append(merged)
    if dropped_stale:
        logger.info("media-info pre-filter: dropped %d stale reels before ranking.", dropped_stale)
    return enriched


def extract_single_reel_metadata(
    reel_info: dict[str, Any],
    session: InstagramSession | None = None,
    page: Any = None,
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
        if page is not None:
            pass  # Caller provided a dedicated isolated page
        elif session:
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
        blocked = _page_html_indicates_block(html)

        # Canonical-link fallback recovers the shortcode/handle when OG tags shift.
        try:
            canon = page.query_selector("link[rel='canonical']")
            canon_href = canon.get_attribute("href") if canon else ""
            canon_code = _extract_shortcode(canon_href or "")
            if canon_code and not shortcode:
                shortcode = canon_code
        except Exception:
            canon_code = ""

        og_title = page.query_selector('meta[property="og:title"]')
        og_desc = page.query_selector('meta[property="og:description"]')
        og_image = page.query_selector('meta[property="og:image"]')

        title_text = og_title.get_attribute("content") if og_title else ""
        desc_text = og_desc.get_attribute("content") if og_desc else ""
        thumb_url = og_image.get_attribute("content") if og_image else reel_info.get("thumbnail", "")

        if blocked and not (title_text or canon_code or '<time' in (html or '')):
            # No reel-validity signals (OG title, canonical shortcode, or a
            # <time> element): treat the markers as a genuine soft block.
            # Marker strings alone are not enough — they also occur in reel
            # captions ("try again later") and in JS bundle enums — so a page
            # carrying real reel metadata is never dropped on this signal.
            logger.warning("Soft-block markers in reel page %s; skipping without fallback.", reel_url)
            return None
        if blocked:
            logger.warning(
                "Soft-block markers present but reel metadata found on %s; continuing.",
                reel_url,
            )

        metrics_estimated = True
        like_count = 0
        comment_count = 0
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
                metrics_estimated = False
                clean_cap = cap.strip(" \"'")
                if clean_cap:
                    caption = clean_cap
                timestamp = _parse_date_flexible(date_str) or timestamp

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

        if timestamp > 0:
            return {
                "id": shortcode or reel_info["id"],
                "url": reel_url,
                "creator_handle": creator_handle,
                "caption": caption,
                "view_count": reel_info.get("view_count", 0),
                "like_count": like_count,
                "comment_count": comment_count,
                "duration": 0,
                "timestamp": timestamp,
                "thumbnail": thumb_url,
                "video_cdn_url": video_cdn_url,
                "metrics_estimated": metrics_estimated,
                "is_pinned": bool(reel_info.get("is_pinned", False)),
            }
    except Exception as exc:
        logger.debug("Playwright extraction failed on %s: %s; trying yt-dlp fallback...", reel_url, exc)
    finally:
        if local_session:
            local_session.close()

    # 2. Fallback to yt-dlp with cookies
    cookie_args = get_cookie_args()
    cmd = [
        "yt-dlp",
        *cookie_args,
        "--user-agent", DEFAULT_USER_AGENT,
        "--referer", "https://www.instagram.com/",
        "--dump-single-json",
        "--no-warnings",
        reel_url,
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            reel_id = str(data.get("id") or reel_info["id"])
            views = int(data.get("view_count") or data.get("play_count") or reel_info.get("view_count", 0))
            handle = clean_handle(data.get("channel") or data.get("uploader_id")) or creator_handle
            name = data.get("uploader") or handle

            return {
                "id": reel_id,
                "url": reel_url,
                "creator_handle": handle,
                "creator_name": name,
                "caption": (data.get("description") or data.get("title") or "").strip(),
                "view_count": views,
                "like_count": int(data.get("like_count") or 0),
                "comment_count": int(data.get("comment_count") or 0),
                "duration": data.get("duration") or 0,
                "timestamp": int(data.get("timestamp") or 0),
                "thumbnail": data.get("thumbnail") or reel_info.get("thumbnail", ""),
                "video_cdn_url": data.get("url", ""),
                "metrics_estimated": False,
                "is_pinned": bool(reel_info.get("is_pinned", False)),
            }
    except Exception as exc:
        logger.warning("yt-dlp fallback failed for %s: %s", reel_url, exc)

    # 3. Last-resort fallback to basic reel info
    return {
        "id": reel_info["id"],
        "url": reel_url,
        "creator_handle": creator_handle,
        "caption": reel_info.get("caption", ""),
        "view_count": reel_info.get("view_count", 0),
        "like_count": 0,
        "comment_count": 0,
        "duration": 0,
        "timestamp": reel_info.get("timestamp") or 0,
        "thumbnail": reel_info.get("thumbnail", ""),
        "video_cdn_url": reel_info.get("video_cdn_url", ""),
        "metrics_estimated": True,
        "is_pinned": bool(reel_info.get("is_pinned", False)),
    }


def extract_creator_reels(
    handle: str,
    max_reels: int = 10,
    days_back: int = 7,
    use_cookies: bool = True,
    fast_mode: bool = False,
    session: InstagramSession | None = None,
    include_pinned: bool = False,
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

    kwargs: dict[str, Any] = {}
    if session is not None:
        kwargs["session"] = session
    if include_pinned:
        kwargs["include_pinned"] = include_pinned
    try:
        reels_info = discover_creator_reel_urls(clean_handle, max_reels=max_reels, **kwargs)
    except TypeError:
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
                "caption": "",
                "view_count": info.get("view_count", 0),
                "like_count": 0,
                "comment_count": 0,
                "duration": 0,
                "timestamp": 0,
                "thumbnail": info.get("thumbnail", ""),
                "video_cdn_url": "",
                "metrics_estimated": True,
                "is_pinned": bool(info.get("is_pinned", False)),
            })
            continue

        if session is not None:
            meta = extract_single_reel_metadata(info, session=session)
        else:
            meta = extract_single_reel_metadata(info)
        if not meta:
            continue

        is_pinned = bool(info.get("is_pinned") or meta.get("is_pinned"))
        ts = meta.get("timestamp") or 0
        if is_pinned:
            meta["is_pinned"] = True
        elif not ts or ts < cutoff_timestamp:
            logger.info("Discarding reel %s: timestamp %s older than %d-day cutoff %s (or missing)",
                        meta.get("id"), ts, days_back, cutoff_timestamp)
            continue

        results.append(meta)

    return results


def _downloaded_mp4_is_playable(path: Path) -> bool:
    """Validate a downloaded file is a real playable MP4 (PY-P1-7).

    The 50 KB size gate alone passes truncated MP4s and HTML error pages,
    which then upload as video/mp4 and fail only in iOS playback. ffprobe
    must exit 0 with a positive duration; missing ffprobe fails open (the
    size gate still applies) so minimal CI images keep working. Never raises.
    """
    try:
        if not path.exists() or path.stat().st_size <= 50000:
            return False
    except OSError:
        return False
    if shutil.which("ffprobe") is None:
        return True
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if res.returncode != 0:
            return False
        fmt = json.loads(res.stdout or "{}").get("format", {})
        return float(fmt.get("duration") or 0) > 0
    except Exception:
        return False


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
        **_client_hint_headers(DEFAULT_USER_AGENT),
    }

    # 1. Resolve CDN URL if not provided (never reopen Playwright here:
    # the pipeline closes the browser session before downloads, so a missing
    # CDN URL falls straight through to the yt-dlp fallback below)
    cdn_target = video_cdn_url
    if not cdn_target and session is not None:
        meta = extract_single_reel_metadata({"id": "probe", "url": reel_url, "creator_handle": ""}, session=session)
        if meta and meta.get("video_cdn_url"):
            cdn_target = meta["video_cdn_url"]

    # 2. Direct streaming download from Instagram CDN (skip invalid blob: URIs)
    if cdn_target and not cdn_target.startswith("blob:"):
        for attempt in range(1, max_retries + 1):
            try:
                logger.info("Downloading reel stream (attempt %d/%d): %s...", attempt, max_retries, reel_url)
                with requests.get(cdn_target, headers=headers, stream=True, timeout=45) as r:
                    if r.status_code == 429:
                        # Rate-limited: honor Retry-After, else exponential
                        # backoff with jitter (PY-P2-4). No point hammering.
                        retry_after = 0.0
                        try:
                            retry_after = float(r.headers.get("Retry-After") or 0)
                        except (TypeError, ValueError):
                            retry_after = 0.0
                        wait = max(retry_after, (2.0 ** attempt) + random.uniform(0, 2.0))
                        logger.warning("CDN rate-limited (429); backing off %.1fs.", wait)
                        time.sleep(wait)
                        continue
                    if r.status_code in (403, 404, 410):
                        # Ban/gone: retrying in seconds never heals these.
                        logger.warning("Stream request returned status %d; skipping to yt-dlp fallback.", r.status_code)
                        break
                    if r.status_code == 200:
                        _ct = (r.headers.get("Content-Type") or "").lower()
                        if "text/html" in _ct:
                            logger.warning("CDN returned HTML; skipping to yt-dlp fallback.")
                            temp_path.unlink(missing_ok=True)
                        else:
                            _dl = 0
                            with open(temp_path, "wb") as f:
                                for chunk in r.iter_content(chunk_size=65536):
                                    if chunk:
                                        f.write(chunk)
                                        _dl += len(chunk)
                                        if _dl > 250 * 1024 * 1024:
                                            raise ValueError("CDN download exceeded 250MB cap")

                        if _downloaded_mp4_is_playable(temp_path):
                            temp_path.replace(output_path)
                            logger.info("Successfully downloaded %.2f MB to %s",
                                        output_path.stat().st_size / (1024 * 1024), output_path.name)
                            return True
                        else:
                            logger.warning("Downloaded stream failed integrity check (%d bytes), retrying...",
                                           temp_path.stat().st_size if temp_path.exists() else 0)
                            temp_path.unlink(missing_ok=True)
                    else:
                        logger.warning("Stream request returned status %d", r.status_code)
            except Exception as exc:
                logger.warning("Stream download exception on attempt %d: %s", attempt, exc)

            if attempt < max_retries:
                time.sleep((2.0 ** attempt) + random.uniform(0, 2.0))

    # 3. Fallback to yt-dlp (with concurrency throttle & automatic retry)
    logger.info("Direct stream unavailable or failed; falling back to yt-dlp for %s", reel_url)
    cookie_args = get_cookie_args() if use_cookies else []
    cmd = [
        "yt-dlp",
        *cookie_args,
        "--user-agent", DEFAULT_USER_AGENT,
        "--referer", "https://www.instagram.com/",
        "-f", "b[ext=mp4]/bv*[ext=mp4]+ba[ext=m4a]/b",
        "--no-warnings",
        "-o", str(temp_path),
        reel_url,
    ]

    for attempt in range(1, 3):
        try:
            with _YTDLP_SEMAPHORE:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0 and _downloaded_mp4_is_playable(temp_path):
                temp_path.replace(output_path)
                logger.info("Successfully downloaded %.2f MB via yt-dlp to %s",
                            output_path.stat().st_size / (1024 * 1024), output_path.name)
                return True
            else:
                temp_path.unlink(missing_ok=True)
                err_snippet = (res.stderr or "").strip()[-250:]
                logger.warning("yt-dlp attempt %d failed (code %d): %s", attempt, res.returncode, err_snippet)
        except Exception as exc:
            logger.warning("yt-dlp attempt %d exception: %s", attempt, exc)
        if attempt < 2:
            time.sleep(2.5 + random.random())

    if temp_path.exists():
        temp_path.unlink(missing_ok=True)
    return False


def extract_external_reels_from_feed(
    session: InstagramSession,
    target_count: int,
    existing_ids: set[str] | None = None,
    active_sources: list[dict[str, Any]] | None = None,
    max_evaluations: int | None = None,
    min_likes: int | None = None,
    min_comments: int | None = None,
    on_progress: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    """Crawl Instagram Reels discovery feed (instagram.com/reels/) with Playwright to discover
    high-signal reels from external creators to fill the remaining weekly quota.

    Filters:
      - Excludes followed channels, blacklist, and existing IDs.
      - Requires visible likes >= min_likes (default config.MIN_EXTERNAL_LIKES)
        OR (if hidden) comments >= min_comments (default MIN_EXTERNAL_COMMENTS).
      - Classifies topic into the 6 digest categories (ai_tech, finance, health, entertainment, niche, food).
      - Maximum 2 reels per external creator.
      - Raises CookieExpiredException if redirected to login.
      - on_progress (optional) receives a cumulative snapshot every 10 finds so
        callers can stream-checkpoint; a failing callback never breaks discovery.
    """
    if target_count <= 0:
        return []

    if min_likes is None:
        min_likes = config.MIN_EXTERNAL_LIKES
    if min_comments is None:
        min_comments = config.MIN_EXTERNAL_COMMENTS
    feed_eval_cap = config.MAX_FEED_EVALUATIONS
    if max_evaluations is None:
        max_evaluations = min(feed_eval_cap, max(120, target_count * 8))
    else:
        max_evaluations = min(feed_eval_cap, max_evaluations)

    existing = set(existing_ids or set())
    followed_handles = set(
        s.get("handle", "").lower().replace("@", "") for s in (active_sources or [])
    )
    blacklist = get_blacklisted_creators()
    creator_counts: dict[str, int] = {}
    external_candidates: list[dict[str, Any]] = []
    # Randomized anti-detection cooldown schedule (low-profile: more often,
    # longer rests after the automation warning).
    next_cooldown_at = random.randint(*FEED_COOLDOWN_EVERY)
    logger.info(
        "Opening Instagram Reels feed to discover up to %d external reels "
        "(bar: >=%d likes or >=%d comments when hidden; eval cap %d)...",
        target_count, min_likes, min_comments, max_evaluations,
    )

    page = session.get_page()

    try:
        page.goto("https://www.instagram.com/reels/", wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        logger.warning("Failed navigating to reels feed: %s", exc)
        return []

    # Check for authentication redirect
    current_url = getattr(page, "url", "") or ""
    if any(m in current_url for m in _BLOCK_MARKERS):
        raise CookieExpiredException(f"Instagram session expired: redirected to {current_url}")

    page.wait_for_timeout(3000)

    eval_count = 0
    while len(external_candidates) < target_count and eval_count < max_evaluations:
        eval_count += 1
        current_url = getattr(page, "url", "") or ""
        if any(m in current_url for m in _BLOCK_MARKERS):
            raise CookieExpiredException(
                f"Instagram session expired during feed scroll: {current_url}",
                partial=external_candidates,
            )

        try:
            data = page.evaluate("""() => {
                // 1. Reel ID from location or link
                let reelId = null;
                const pathMatch = window.location.pathname.match(/\\/reels?\\/([A-Za-z0-9_-]+)/);
                if (pathMatch) {
                    reelId = pathMatch[1];
                }
                if (!reelId) {
                    const reelLink = document.querySelector('a[href*="/reel/"], a[href*="/reels/"]');
                    if (reelLink) {
                        const m = reelLink.href.match(/\\/reels?\\/([A-Za-z0-9_-]+)/);
                        if (m) reelId = m[1];
                    }
                }

                // 2. Creator Handle
                let handle = '';
                const creatorLinks = Array.from(document.querySelectorAll('a[aria*=" reels"], a[role="link"]'));
                for (const a of creatorLinks) {
                    const aria = (a.getAttribute('aria-label') || '').replace(/ reels$/i, '').trim();
                    const txt = (a.innerText || '').trim();
                    const candidate = aria || txt;
                    if (candidate && !candidate.includes(' ') && candidate.length > 2) {
                        handle = candidate.toLowerCase().replace('@', '');
                        break;
                    }
                }
                if (!handle) {
                    const allLinks = Array.from(document.querySelectorAll('a'));
                    const reserved = ['explore', 'reel', 'reels', 'direct', 'stories', 'accounts', 'legal', 'about', 'p', 'tags', 'locations', 'tv'];
                    for (const a of allLinks) {
                        const href = a.getAttribute('href') || '';
                        const parts = href.split('/').filter(Boolean);
                        if (parts.length >= 1 && !reserved.includes(parts[0].toLowerCase())) {
                            handle = parts[0].toLowerCase().replace('@', '');
                            break;
                        }
                    }
                }

                // 3. Caption
                let caption = '';
                const textNodes = Array.from(document.querySelectorAll('h1, span, div[dir="auto"]'));
                for (const el of textNodes) {
                    const txt = (el.innerText || '').trim();
                    if (txt.length > 25 && !txt.includes('likes') && !txt.includes('comments') && !txt.includes('Follow')) {
                        caption = txt;
                        break;
                    }
                }

                // 4. Likes & Comments
                let rawLikes = '';
                let rawComments = '';
                const allButtonsAndSpans = Array.from(document.querySelectorAll('button, span, a'));
                for (const el of allButtonsAndSpans) {
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const txt = (el.innerText || '').trim();
                    if (aria.includes('like') || aria.includes('likes')) {
                        rawLikes = aria || txt;
                    }
                    if (aria.includes('comment') || aria.includes('comments')) {
                        rawComments = aria || txt;
                    }
                }

                // 5. Video CDN URL & Poster Thumbnail
                let videoSrc = '';
                const videoEl = document.querySelector('video');
                if (videoEl) {
                    videoSrc = videoEl.getAttribute('src') || '';
                    if (!videoSrc) {
                        const sourceEl = videoEl.querySelector('source');
                        if (sourceEl) videoSrc = sourceEl.getAttribute('src') || '';
                    }
                }
                let posterSrc = '';
                if (videoEl && videoEl.getAttribute('poster')) {
                    posterSrc = videoEl.getAttribute('poster');
                } else {
                    const imgEl = document.querySelector('img[src*="cdninstagram"]');
                    if (imgEl) posterSrc = imgEl.getAttribute('src') || '';
                }

                return {
                    reelId,
                    reelUrl: reelId ? `https://www.instagram.com/reel/${reelId}/` : '',
                    handle,
                    caption,
                    rawLikes,
                    rawComments,
                    videoSrc,
                    posterSrc
                };
            }""")
        except Exception as exc:
            logger.debug("Error evaluating reel DOM: %s", exc)
            data = None

        if data and data.get("reelId"):
            rid = data["reelId"]
            h = clean_handle(data.get("handle"))
            caption = data.get("caption", "")
            likes = parse_view_count_text(data.get("rawLikes", ""))
            comments = parse_view_count_text(data.get("rawComments", ""))
            video_cdn = data.get("videoSrc", "") or ""
            poster = data.get("posterSrc", "") or ""

            # If handle or metrics not fully parsed from DOM, enrich via isolated secondary tab
            if not h or (likes == 0 and comments == 0):
                isolated_tab = None
                try:
                    isolated_tab = session.new_isolated_page()
                    meta = (
                        extract_single_reel_metadata({"id": rid, "url": f"https://www.instagram.com/reel/{rid}/", "creator_handle": h}, page=isolated_tab)
                        if isolated_tab else
                        extract_single_reel_metadata({"id": rid, "url": f"https://www.instagram.com/reel/{rid}/", "creator_handle": h}, session=session)
                    )
                except Exception as meta_err:
                    logger.debug("Isolated metadata extraction failed on %s: %s", rid, meta_err)
                    meta = None
                finally:
                    if isolated_tab:
                        try:
                            isolated_tab.close()
                        except Exception:
                            pass
                if meta:
                    h = clean_handle(meta.get("creator_handle")) or h
                    caption = caption or meta.get("caption", "")
                    likes = max(likes, meta.get("like_count", 0))
                    comments = max(comments, meta.get("comment_count", 0))
                    video_cdn = meta.get("video_cdn_url", "") or video_cdn
                    poster = meta.get("thumbnail", "") or poster

            # Check duplication and blacklists
            if (
                rid not in existing
                and h
                and h not in followed_handles
                and h not in blacklist
                and creator_counts.get(h, 0) < 2
            ):
                # High-signal threshold: visible likes >= min_likes OR
                # (if hidden) comments >= min_comments.
                is_high_signal = (likes >= min_likes) or (likes == 0 and comments >= min_comments)
                if is_high_signal:
                    cat = categorize_creator(h, caption)
                    if cat:
                        estimated_views = max(likes * 6, comments * 60, 75000)
                        external_candidates.append({
                            "id": rid,
                            "url": f"https://www.instagram.com/reel/{rid}/",
                            "creator_handle": h,
                            "creator_name": h,
                            "caption": caption[:300],
                            "view_count": estimated_views,
                            "view_count_estimated": True,
                            "like_count": likes,
                            "comment_count": comments,
                            "duration": 30,
                            "timestamp": int(time.time()),
                            "timestamp_estimated": True,
                            "category": cat,
                            "thumbnail": poster,
                            "video_cdn_url": video_cdn,
                            "metrics_estimated": True,
                            "is_external": True,
                        })
                        existing.add(rid)
                        creator_counts[h] = creator_counts.get(h, 0) + 1
                        logger.info(
                            "Discovered external high-signal reel [%s] by @%s (%s | %d likes, %d comments) [%d/%d]",
                            rid, h, cat, likes, comments, len(external_candidates), target_count
                        )
                        if on_progress is not None and len(external_candidates) % 10 == 0:
                            try:
                                on_progress(list(external_candidates))
                            except Exception as cb_err:
                                logger.debug("Discovery progress callback failed: %s", cb_err)

        # Randomized resting pause to break robotic velocity and satisfy TOS pacing
        if eval_count >= next_cooldown_at:
            mu, sigma, floor = FEED_COOLDOWN_SECS
            cooldown = max(floor, random.gauss(mu, sigma))
            logger.info("Anti-detection cooldown: resting %.1fs after %d reel evaluations...",
                        cooldown, eval_count)
            time.sleep(cooldown)
            next_cooldown_at = eval_count + random.randint(*FEED_COOLDOWN_EVERY)

        # Scroll to next reel with humanized, non-repeating motion:
        # randomized trackpad-style wheel deltas (2-4 flicks of varying
        # distance) with an occasional full PageDown, plus small random
        # mouse moves before evaluation so the pointer trail is not static.
        # Fixed-choice PageDown presses were a trivial key-event signature.
        try:
            vw, vh = 1280, 800
            try:
                _vs = page.viewport_size or {}
                vw = int(_vs.get("width", 1280))
                vh = int(_vs.get("height", 800))
            except Exception:
                vw, vh = 1280, 800
            if not (200 <= vw <= 4000):
                vw = 1280
            if not (200 <= vh <= 4000):
                vh = 800
            for _ in range(random.randint(2, 4)):
                page.mouse.wheel(
                    random.randint(-40, 40),
                    random.randint(int(vh * 0.5), int(vh * 1.1)),
                )
                try:
                    page.wait_for_timeout(random.randint(120, 450))
                except Exception:
                    # Mock/minimal pages in unit tests may not implement timeouts.
                    pass
            if random.random() < 0.25:
                try:
                    page.keyboard.press("PageDown")
                except Exception:
                    pass
            for _ in range(random.randint(1, 3)):
                try:
                    page.mouse.move(
                        random.randint(0, max(vw - 1, 1)),
                        random.randint(0, max(vh - 1, 1)),
                        steps=random.randint(2, 6),
                    )
                except Exception:
                    pass
        except Exception:
            pass
        human_pause()

        # Ensure feed crawler page did not navigate away from /reels/
        feed_url = getattr(page, "url", "") or ""
        if "/reels/" not in feed_url:
            logger.warning("Feed page navigated away to %s; recovering to /reels/...", feed_url)
            try:
                page.goto("https://www.instagram.com/reels/", wait_until="domcontentloaded", timeout=25000)
            except Exception as rec_err:
                logger.warning("Failed reloading reels feed during recovery: %s", rec_err)

    logger.info("External Reels discovery finished: harvested %d high-signal external reels (evaluated %d).",
                len(external_candidates), eval_count)
    return external_candidates


_FOLLOW_BUTTON_TEXTS_JS = (
    "() => Array.from(document.querySelectorAll('button'))"
    ".map(b => (b.innerText || '').trim())"
)

_FOLLOW_CLICK_JS = (
    "() => {"
    " const btns = Array.from(document.querySelectorAll('button'));"
    " for (const b of btns) {"
    "  const t = (b.innerText || '').trim().toLowerCase();"
    "  if (t === 'follow' || t === 'follow back') { b.click(); return true; }"
    " }"
    " return false; }"
)


def _follow_button_texts(page) -> list[str]:
    """All button innerTexts on the profile (raw-DOM probe).

    Probe 2026-09-19: page.get_by_role misses the Following state in
    headless Chromium, so state detection must read every <button>'s
    innerText via evaluate instead of role queries.
    """
    try:
        texts = page.evaluate(_FOLLOW_BUTTON_TEXTS_JS)
    except Exception:
        return []
    if not isinstance(texts, list):
        return []
    return [str(t or "").strip() for t in texts]


def _follow_state_from_texts(texts: list[str]) -> str | None:
    """Map button texts to a follow state: following/requested/follow/follow_back."""
    for raw in texts:
        t = (raw or "").strip().lower()
        if not t:
            continue
        if "following" in t:
            return "following"
        if "requested" in t:
            return "requested"
    for raw in texts:
        t = (raw or "").strip().lower()
        if t == "follow back":
            return "follow_back"
        if t == "follow":
            return "follow"
    return None


def follow_creator(handle: str, timeout: int = 25) -> dict[str, Any]:
    """Follow one creator on the logged-in Instagram account (browser only).

    Uses the existing InstagramSession (cookie injection + stealth
    context). No API calls — they 429 under automation load. Steps:
    validate session, goto profile, detect already-following/requested
    via ALL-button innerText, click Follow/Follow Back via get_by_role
    with a raw-DOM evaluate fallback, wait, then re-check innerText.

    Returns {"ok": True, "state": "followed|requested|already"} or
    {"ok": False, "error": "not_found|blocked|..."}.
    """
    clean = clean_handle(handle)
    if not clean:
        return {"ok": False, "error": "invalid_handle"}
    try:
        timeout_ms = max(5, int(timeout)) * 1000
    except (TypeError, ValueError):
        timeout_ms = 25000

    session = InstagramSession()
    try:
        try:
            valid = session.validate()
        except Exception:
            valid = False
        if not valid:
            return {"ok": False, "error": "session_invalid"}

        try:
            page = session.get_page()
        except Exception as exc:
            logger.warning("Follow @%s: could not open page: %s", clean, exc)
            return {"ok": False, "error": "session_invalid"}

        try:
            page.goto(f"https://www.instagram.com/{clean}/",
                      wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            logger.warning("Follow @%s: profile navigation failed: %s", clean, exc)
            return {"ok": False, "error": "navigation_failed"}
        try:
            _assert_not_blocked(page, f"follow-@{clean}")
        except InstagramBlocked:
            return {"ok": False, "error": "blocked"}
        try:
            html = page.content()
        except Exception:
            html = ""
        if _page_html_indicates_block(html or ""):
            return {"ok": False, "error": "blocked"}
        if "sorry, this page isn't available" in (html or "").lower():
            return {"ok": False, "error": "not_found"}

        texts = _follow_button_texts(page)
        state = _follow_state_from_texts(texts)
        if state in ("following", "requested"):
            human_pause()
            return {"ok": True, "state": "already"}
        if state is None:
            # No follow-state button at all: missing profile vs. logged-out
            # wall. Block markers were already checked, so treat the most
            # likely case (nonexistent/renamed handle) as not_found.
            lowered = " ".join(t.lower() for t in texts)
            if "log in" in lowered or "sign up" in lowered:
                return {"ok": False, "error": "blocked"}
            return {"ok": False, "error": "not_found"}

        clicked = False
        try:
            locator = page.get_by_role("button", name=re.compile(r"^Follow( Back)?$", re.I))
            locator.first.click(timeout=5000)
            clicked = True
        except Exception:
            clicked = False
        if not clicked:
            try:
                clicked = bool(page.evaluate(_FOLLOW_CLICK_JS))
            except Exception as exc:
                logger.warning("Follow @%s: fallback click failed: %s", clean, exc)
                clicked = False
        if not clicked:
            return {"ok": False, "error": "click_failed"}

        try:
            page.wait_for_timeout(2500)
        except Exception:
            time.sleep(2.5)
        try:
            _assert_not_blocked(page, f"follow-@{clean}-after-click")
        except InstagramBlocked:
            return {"ok": False, "error": "blocked"}
        try:
            html = page.content()
        except Exception:
            html = ""
        if _page_html_indicates_block(html or ""):
            return {"ok": False, "error": "blocked"}

        texts = _follow_button_texts(page)
        state = _follow_state_from_texts(texts)
        human_pause()
        if state == "following":
            return {"ok": True, "state": "followed"}
        if state == "requested":
            return {"ok": True, "state": "requested"}
        if state in ("follow", "follow_back"):
            return {"ok": False, "error": "click_failed"}
        return {"ok": False, "error": "unknown_state"}
    finally:
        try:
            session.close()
        except Exception:
            pass
