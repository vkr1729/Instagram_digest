#!/usr/bin/env python3
"""
scripts/check_media_urls.py — Bounded pre-deploy sample check of digest video URLs.

HEAD-requests a deterministic sample of video URLs from a digest file and
reports unreachable ones. Warn-only by design: a few expired CDN links are
normal (the pipeline's unplayable filter drops them at download time), but a
high failure rate signals credential rot or mass expiry worth investigating
before a deploy.

Usage:
    python scripts/check_media_urls.py [digest.json] [--sample 10] [--timeout 10]

Exit codes: 0 = sample healthy, 1 = usage/file error, 2 = failure rate above
--max-fail-ratio (default 0.5).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("InstagramDigest.CheckMediaUrls")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def load_video_urls(digest_path: Path) -> list[str]:
    """Extract playable video URLs from a digest file (tolerant of shapes)."""
    try:
        data = json.loads(digest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Cannot read digest file: {exc}") from exc
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Digest has no items list")
    urls: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("video_url", "r2_url", "url"):
            val = item.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                urls.append(val)
                break
    return urls


def deterministic_sample(urls: list[str], sample: int) -> list[str]:
    """Evenly spaced deterministic sample (stable across runs)."""
    if sample <= 0 or not urls:
        return []
    if len(urls) <= sample:
        return list(urls)
    step = len(urls) / sample
    return [urls[int(i * step)] for i in range(sample)]


def head_ok(url: str, timeout: int) -> bool:
    """True when a HEAD (fallback GET-range) request succeeds."""
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method,
                                         headers={"Range": "bytes=0-0",
                                                  "User-Agent": "InstagramDigest/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                if res.status in (200, 206):
                    return True
        except Exception:
            continue
    return False


def check_urls(urls: list[str], timeout: int) -> dict[str, Any]:
    """Check each URL; returns {ok, failed} handle lists."""
    ok, failed = [], []
    for url in urls:
        (ok if head_ok(url, timeout) else failed).append(url)
    return {"ok": ok, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sample-check digest video URLs")
    parser.add_argument("digest", nargs="?", default="data/digests/latest.json",
                        help="Digest JSON file (default: data/digests/latest.json)")
    parser.add_argument("--sample", type=int, default=10, help="URLs to probe (default: 10)")
    parser.add_argument("--timeout", type=int, default=10, help="Per-request seconds (default: 10)")
    parser.add_argument("--max-fail-ratio", type=float, default=0.5,
                        help="Exit 2 when failures exceed this ratio (default: 0.5)")
    args = parser.parse_args(argv)

    digest_path = Path(args.digest)
    if not digest_path.exists():
        # Resolve the "latest" alias against data/digests/.
        if args.digest == "data/digests/latest.json":
            cands = sorted(Path("data/digests").glob("*.json"))
            if cands:
                digest_path = cands[-1]
        if not digest_path.exists():
            logger.error("Digest file not found: %s", args.digest)
            return 1
    try:
        urls = load_video_urls(digest_path)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    if not urls:
        logger.error("No video URLs found in %s", digest_path)
        return 1

    sample = deterministic_sample(urls, max(1, args.sample))
    logger.info("Probing %d of %d video URLs from %s ...", len(sample), len(urls), digest_path)
    result = check_urls(sample, max(1, args.timeout))
    logger.info("Reachable: %d, failed: %d", len(result["ok"]), len(result["failed"]))
    for url in result["failed"]:
        logger.warning("UNREACHABLE: %s", url)
    ratio = len(result["failed"]) / max(1, len(sample))
    if ratio > args.max_fail_ratio:
        logger.error("Failure ratio %.2f exceeds %.2f", ratio, args.max_fail_ratio)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
