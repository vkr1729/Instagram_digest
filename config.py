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
if ENV_FILE.exists():
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

# Pipeline & Retention Limits (Default: 1 week rolling archive)
RETENTION_WEEKS = int(os.getenv("RETENTION_WEEKS", "1"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", str(RETENTION_WEEKS * 7)))
TOP_DIGEST_COUNT = int(os.getenv("TOP_DIGEST_COUNT", "200"))
MAX_PER_CREATOR = int(os.getenv("MAX_PER_CREATOR", "4"))
R2_STORAGE_QUOTA_BYTES = 5 * 1024 * 1024 * 1024  # Strict 5 GB safety guard (out of 10 GB free)

# Playback & UI Defaults
DEFAULT_PLAYBACK_SPEED = float(os.getenv("DEFAULT_PLAYBACK_SPEED", "1.25"))
AUTO_ADVANCE_DELAY_SECONDS = 0.5
AVAILABLE_SPEEDS = [1.0, 1.25, 1.5, 1.75, 2.0]

# Category Buckets & Target Quotas (200 Total)
# 40% Entertainment, 15% Finance, 15% AI & Tech, 10% Niche, 10% Health, 10% Food
CATEGORIES = [
    {"id": "all", "label": "All Top 200", "emoji": "🔥"},
    {"id": "entertainment", "label": "Entertainment", "emoji": "🎬", "target_pct": 0.40, "target_count": 80},
    {"id": "finance", "label": "Finance", "emoji": "💰", "target_pct": 0.15, "target_count": 30},
    {"id": "ai_tech", "label": "AI & Tech", "emoji": "💻", "target_pct": 0.15, "target_count": 30},
    {"id": "niche", "label": "Niche", "emoji": "🧠", "target_pct": 0.10, "target_count": 20},
    {"id": "health", "label": "Health", "emoji": "🏋️", "target_pct": 0.10, "target_count": 20},
    {"id": "food", "label": "Food & Recipes", "emoji": "🥗", "target_pct": 0.10, "target_count": 20},
]

# File Locations
SOURCES_FILE = ROOT_DIR / "sources.json"
FOLLOWING_CACHE_FILE = DATA_DIR / "following_cache.json"
DIGEST_BATCH_FILE = DATA_DIR / "top100_digest.json"
WEEKLY_ARCHIVE_FILE = DATA_DIR / "archive_history.json"
WATCHED_FILE = DATA_DIR / "watched.json"
BLACKLIST_FILE = DATA_DIR / "blacklist.json"
