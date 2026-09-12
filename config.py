"""
config.py — Central configuration and paths for Instagram Digest v1.0.
"""

from __future__ import annotations

import os
from pathlib import Path

# Base paths
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
SITE_DIR = ROOT_DIR / "site"
TEMPLATES_DIR = ROOT_DIR / "templates"
VIDEOS_DIR = ROOT_DIR / "videos"
DIGESTS_DIR = DATA_DIR / "digests"

# Ensure runtime directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
SITE_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
DIGESTS_DIR.mkdir(parents=True, exist_ok=True)

# Native .env loader (zero external dependency required)
ENV_FILE = ROOT_DIR / ".env"


def check_env_file_permissions(path: Path | None = None) -> bool:
    """True when the env file is absent or not group/other-readable.

    R2/SMTP secrets live here; mode 0644 leaks them to every local user.
    """
    p = path or ENV_FILE
    try:
        if not p.exists():
            return True
        mode = p.stat().st_mode
    except OSError:
        return True
    return not (mode & 0o077)


if ENV_FILE.exists():
    if not check_env_file_permissions():
        import warnings
        warnings.warn(
            f"Insecure permissions on {ENV_FILE}; run `chmod 600 {ENV_FILE}` "
            "so R2/SMTP secrets are not readable by other local users.",
            RuntimeWarning,
            stacklevel=2,
        )
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v

# Cloudflare R2 Credentials
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "instagram-digest").strip()
R2_PUBLIC_DOMAIN = os.getenv("R2_PUBLIC_DOMAIN", "").strip().rstrip("/")
R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else ""

# GitHub Deployment
GH_PAGES_REPO = os.getenv("GH_PAGES_REPO", "https://github.com/vkr1729/Instagram_digest.git").strip()
PAGES_BASE_URL = os.getenv("PAGES_BASE_URL", "https://vkr1729.github.io/Instagram_digest").strip().rstrip("/")

# Notification / Email Configuration
SMTP_HOST = os.getenv("SMTP_HOST", os.getenv("SMTP_SERVER", "smtp.gmail.com")).strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", os.getenv("SMTP_USERNAME", "")).strip()
SMTP_PASS = os.getenv("SMTP_PASS", os.getenv("SMTP_PASSWORD", "")).strip().strip('"')
NOTIFICATION_EMAIL = os.getenv("NOTIFICATION_EMAIL", os.getenv("RECIPIENT_EMAIL", "")).strip()

# Pipeline & Retention Limits (Default: 1 week rolling archive)
RETENTION_WEEKS = int(os.getenv("RETENTION_WEEKS", "1"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", str(RETENTION_WEEKS * 7 + 1)))
TOP_DIGEST_COUNT = int(os.getenv("TOP_DIGEST_COUNT", "300"))
MAX_PER_CREATOR = int(os.getenv("MAX_PER_CREATOR", "4"))
# One-sided flood guard: no single category may exceed this share of the digest.
# (Replaces the old fixed 40/15/15/10/10/10 percentage targets.)
MAX_CATEGORY_SHARE = float(os.getenv("MAX_CATEGORY_SHARE", "0.50"))
R2_STORAGE_QUOTA_BYTES = 5 * 1024 * 1024 * 1024  # Strict 5 GB safety guard (out of 10 GB free)

# Playback & UI Defaults
DEFAULT_PLAYBACK_SPEED = float(os.getenv("DEFAULT_PLAYBACK_SPEED", "1.0"))
AUTO_ADVANCE_DELAY_SECONDS = 0.5
AVAILABLE_SPEEDS = [1.0, 1.25, 1.5, 1.75, 2.0]

# Category Buckets (id/label/emoji drive digest filter chips; ranking no longer
# uses fixed percentage targets — see MAX_CATEGORY_SHARE)
CATEGORIES = [
    {"id": "all", "label": f"All Top {TOP_DIGEST_COUNT}", "emoji": "🔥"},
    {"id": "entertainment", "label": "Entertainment", "emoji": "🎬"},
    {"id": "finance", "label": "Finance", "emoji": "💰"},
    {"id": "ai_tech", "label": "AI & Tech", "emoji": "💻"},
    {"id": "niche", "label": "Niche", "emoji": "🧠"},
    {"id": "health", "label": "Health", "emoji": "🏋️"},
    {"id": "food", "label": "Food & Recipes", "emoji": "🥗"},
]

# File Locations
SOURCES_FILE = ROOT_DIR / "sources.json"
FOLLOWING_CACHE_FILE = DATA_DIR / "following_cache.json"
DIGEST_BATCH_FILE = DATA_DIR / "top100_digest.json"
WEEKLY_ARCHIVE_FILE = DATA_DIR / "archive_history.json"
WATCHED_FILE = DATA_DIR / "watched.json"
BLACKLIST_FILE = DATA_DIR / "blacklist.json"
LAST_RUN_FILE = DATA_DIR / "last_run.json"
