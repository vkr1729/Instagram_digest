#!/usr/bin/env python3
"""
scripts/check_local_media.py — Bounded integrity spot-check of downloaded MP4s.

Samples video files under videos/ (newest week first) and reuses
extractor._downloaded_mp4_is_playable (50 KB gate + ffprobe duration) to
catch truncated or HTML-masquerading files before they waste R2 quota or
stall iOS playback. Report-only; never deletes anything.

Usage:
    python scripts/check_local_media.py [--dir videos] [--sample 20]

Exit codes: 0 = sample healthy, 1 = usage error / nothing to check,
2 = failure ratio above --max-fail-ratio (default 0.2).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import extractor

logger = logging.getLogger("InstagramDigest.CheckLocalMedia")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def collect_media_files(root: Path) -> list[Path]:
    """MP4s under root, newest week directories first (stable order)."""
    if not root.exists():
        return []
    files = [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() == ".mp4"]
    files.sort(key=lambda p: (p.parent.name, p.name), reverse=True)
    return files


def deterministic_sample(files: list[Path], sample: int) -> list[Path]:
    """Evenly spaced deterministic sample (stable across runs)."""
    if sample <= 0 or not files:
        return []
    if len(files) <= sample:
        return list(files)
    step = len(files) / sample
    return [files[int(i * step)] for i in range(sample)]


def check_files(files: list[Path]) -> dict[str, Any]:
    """Validate each file; returns {ok, bad} path lists."""
    ok, bad = [], []
    for path in files:
        try:
            valid = extractor._downloaded_mp4_is_playable(path)
        except Exception:
            valid = False
        (ok if valid else bad).append(path)
    return {"ok": ok, "bad": bad}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spot-check local MP4 integrity")
    parser.add_argument("--dir", default="videos", help="Media root (default: videos)")
    parser.add_argument("--sample", type=int, default=20, help="Files to probe (default: 20)")
    parser.add_argument("--max-fail-ratio", type=float, default=0.2,
                        help="Exit 2 when failures exceed this ratio (default: 0.2)")
    args = parser.parse_args(argv)

    files = collect_media_files(Path(args.dir))
    if not files:
        logger.error("No MP4 files under %s", args.dir)
        return 1
    sample = deterministic_sample(files, max(1, min(int(args.sample), 200)))
    logger.info("Probing %d of %d local MP4s under %s ...", len(sample), len(files), args.dir)
    result = check_files(sample)
    logger.info("Playable: %d, suspect: %d", len(result["ok"]), len(result["bad"]))
    for path in result["bad"]:
        logger.warning("SUSPECT: %s", path)
    ratio = len(result["bad"]) / max(1, len(sample))
    max_fail = min(1.0, max(0.0, float(args.max_fail_ratio)))
    if ratio > max_fail:
        logger.error("Failure ratio %.2f exceeds %.2f", ratio, max_fail)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
