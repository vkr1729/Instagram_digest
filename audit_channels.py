"""
audit_channels.py — Monthly account <-> digest reconciliation for Instagram Digest.

Compares the Instagram account's following list (cached; never forces a
live scrape from the dashboard endpoint) against sources.json, and reports:
  1. followed-on-IG but missing from the digest  ("missing_from_digest")
  2. in the digest but NOT followed on IG        ("not_followed_on_ig")

Usage:
  python audit_channels.py --audit            # report only (default)
  python audit_channels.py --audit --json     # machine-readable report
  python audit_channels.py --import-missing   # also add group 1 to sources.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

import atomic_io
import config
import extractor

logger = logging.getLogger("InstagramDigest.AuditChannels")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _load_sources() -> list[dict[str, Any]]:
    try:
        data = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read sources.json: %s", exc)
        return []
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("handle")]


def _load_blacklist() -> set[str]:
    try:
        data = json.loads(config.BLACKLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(data, dict):
        return set()
    return {str(c).lower().replace("@", "") for c in data.get("creators", [])}


def _load_following() -> list[dict[str, Any]]:
    """Cached following only — a forced scrape belongs in the weekly pipeline,
    not in an on-demand audit. B21: reads FOLLOWING_CACHE_FILE directly so a
    "report-only" audit can never trigger a live Following-API scrape or
    rewrite sources.json when the cache ages past 30 days."""
    try:
        raw = json.loads(config.FOLLOWING_CACHE_FILE.read_text(encoding="utf-8"))
        accounts = raw.get("accounts", []) if isinstance(raw, dict) else []
    except Exception as exc:
        logger.warning("Could not load cached following: %s", exc)
        return []
    return [a for a in accounts if isinstance(a, dict) and a.get("handle")]


def _digest_weeks_back(weeks: int = 4) -> list[dict[str, Any]]:
    """Load recent digest archives for inactivity analysis (tolerant of gaps)."""
    archives: list[dict[str, Any]] = []
    if not config.DIGESTS_DIR.exists():
        return archives
    for path in sorted(config.DIGESTS_DIR.glob("*.json"), reverse=True)[:weeks]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            archives.append(data)
    return archives


def inactive_creators(weeks: int = 3) -> list[dict[str, Any]]:
    """Creators in sources.json with zero reels in the last `weeks` digests.

    Read-only. A creator missing from recent archives is either inactive,
    private/renamed, or outside the date window — all worth a prune review.
    Skips archives thinner than 100 items: 9-item probe/ad-hoc digests would
    otherwise flag the entire channel list as inactive.
    """
    sources = _load_sources()
    archives = _digest_weeks_back(max(weeks, 1))
    archives = [a for a in archives if len(a.get("items", [])) >= 100]
    if not archives:
        return []
    seen: set[str] = set()
    for archive in archives:
        for item in archive.get("items", []):
            if isinstance(item, dict) and item.get("creator_handle"):
                seen.add(str(item["creator_handle"]).lower().replace("@", ""))
    stale: list[dict[str, Any]] = []
    for s in sources:
        h = str(s.get("handle", "")).lower().replace("@", "")
        if h and h not in seen:
            stale.append({
                "handle": h,
                "name": str(s.get("name") or h),
                "category": str(s.get("category") or "entertainment"),
            })
    return sorted(stale, key=lambda e: e["handle"])


def audit() -> dict[str, Any]:
    """Build the reconciliation diff. Read-only; never mutates anything."""
    sources = _load_sources()
    blacklist = _load_blacklist()
    following = _load_following()

    digest_handles = {str(s["handle"]).lower().replace("@", "") for s in sources}
    enabled_handles = {
        str(s["handle"]).lower().replace("@", "")
        for s in sources if s.get("enabled", True)
    }
    following_handles = {str(a["handle"]).lower().replace("@", "") for a in following}
    following_names = {
        str(a["handle"]).lower().replace("@", ""): str(a.get("name") or a["handle"])
        for a in following
    }

    missing_from_digest = sorted(
        (
            {
                "handle": h,
                "name": following_names.get(h, h),
                "category": extractor.categorize_creator(h, following_names.get(h, h)),
            }
            for h in (following_handles - digest_handles - blacklist)
        ),
        key=lambda e: e["handle"],
    )

    not_followed_on_ig = sorted(
        (
            {
                "handle": h,
                "name": next(
                    (str(s.get("name") or h) for s in sources
                     if str(s.get("handle", "")).lower().replace("@", "") == h),
                    h,
                ),
            }
            for h in (enabled_handles - following_handles)
        ),
        key=lambda e: e["handle"],
    )

    return {
        "following_count": len(following_handles),
        "digest_count": len(digest_handles),
        "enabled_count": len(enabled_handles),
        "blacklisted_count": len(blacklist),
        "missing_from_digest": missing_from_digest,
        "not_followed_on_ig": not_followed_on_ig,
        "inactive_creators": inactive_creators(),
    }


def hygiene_report() -> dict[str, Any]:
    """Detect sources.json hygiene issues without mutating anything.

    Finds non-normalized handles (uppercase, leading @, stray whitespace),
    post-normalization duplicates, and handles failing extractor.clean_handle.
    """
    sources = _load_sources()
    non_normalized: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    duplicates: list[dict[str, Any]] = []
    for s in sources:
        raw = str(s.get("handle", ""))
        norm = raw.strip().lstrip("@").lower()
        if raw != norm:
            non_normalized.append({"handle": raw, "normalized": norm})
        if not norm:
            continue
        try:
            valid = bool(extractor.clean_handle(norm))
        except Exception:
            valid = False
        if not valid:
            invalid.append({"handle": raw})
            continue
        if norm in seen:
            duplicates.append({"handle": raw, "normalized": norm,
                               "first_seen_as": seen[norm]})
        else:
            seen[norm] = raw
    return {
        "total": len(sources),
        "non_normalized": sorted(non_normalized, key=lambda e: e["handle"]),
        "duplicates": sorted(duplicates, key=lambda e: e["handle"]),
        "invalid": sorted(invalid, key=lambda e: e["handle"]),
    }


def fix_hygiene(report: dict[str, Any] | None = None) -> int:
    """Normalize handles in place and drop post-normalization dupes (keep
    first). Invalid and empty handles are NEVER auto-deleted — preserved
    as-is and reported only. Returns number of entries changed/removed."""
    with atomic_io.sources_file_lock():
        sources = _load_sources()
        if not isinstance(report, dict):
            report = hygiene_report()
        invalid_raw = {e["handle"] for e in report.get("invalid", []) if isinstance(e, dict)}
        fixed: list[dict[str, Any]] = []
        seen: set[str] = set()
        changed = 0
        for s in sources:
            raw = str(s.get("handle", ""))
            norm = raw.strip().lstrip("@").lower()
            if not norm or raw in invalid_raw:
                fixed.append(s)
                continue
            if norm in seen:
                changed += 1
                continue
            seen.add(norm)
            if raw != norm:
                s["handle"] = norm
                changed += 1
            fixed.append(s)
        if changed:
            atomic_io.durable_write_json(config.SOURCES_FILE, fixed)
        return changed


def import_missing(report: dict[str, Any]) -> int:
    """Add missing_from_digest entries to sources.json (atomic). Returns count added."""
    missing = report.get("missing_from_digest", [])
    if not missing:
        return 0
    with atomic_io.sources_file_lock():
        sources = _load_sources()
        have = {str(s.get("handle", "")).lower().replace("@", "") for s in sources}
        added = 0
        for entry in missing:
            h = str(entry.get("handle", "")).lower().replace("@", "")
            if h and h not in have:
                sources.append({
                    "handle": h,
                    "name": entry.get("name") or h,
                    "category": entry.get("category") or "entertainment",
                    "enabled": True,
                })
                have.add(h)
                added += 1
        if added:
            atomic_io.durable_write_json(config.SOURCES_FILE, sources)
        return added


def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly account<->digest audit")
    parser.add_argument("--audit", action="store_true", help="Report only (default)")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--import-missing", action="store_true",
                        help="Add IG-followed-but-missing creators to sources.json")
    parser.add_argument("--inactive-weeks", type=int, default=3,
                        help="Weeks of digest history for inactivity detection (default: 3)")
    parser.add_argument("--hygiene", action="store_true",
                        help="Report sources.json handle hygiene (case/@ dupes, invalid)")
    parser.add_argument("--fix", action="store_true",
                        help="With --hygiene: normalize handles and drop dupes (never deletes invalid)")
    args = parser.parse_args()

    if args.fix and not args.hygiene:
        parser.error("--fix requires --hygiene")

    if args.hygiene:
        report = hygiene_report()
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"Channels              : {report['total']}")
            print(f"\n-- Non-normalized handles ({len(report['non_normalized'])}) --")
            for e in report["non_normalized"]:
                print(f"  {e['handle']!r} -> {e['normalized']!r}")
            print(f"\n-- Duplicates after normalization ({len(report['duplicates'])}) --")
            for e in report["duplicates"]:
                print(f"  {e['handle']!r} duplicates {e['first_seen_as']!r}")
            print(f"\n-- Invalid handles, report-only ({len(report['invalid'])}) --")
            for e in report["invalid"]:
                print(f"  {e['handle']!r}")
        if args.fix:
            changed = fix_hygiene(report)
            print(f"\nNormalized/deduped {changed} entries.")
        return 0

    if args.inactive_weeks != 3:
        report = audit()
        report["inactive_creators"] = inactive_creators(weeks=max(1, args.inactive_weeks))
    else:
        report = audit()
    if args.import_missing:
        added = import_missing(report)
        report["imported_count"] = added
        logger.info("Imported %d missing creators into sources.json.", added)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Following on IG : {report['following_count']}")
        print(f"In digest       : {report['digest_count']} "
              f"({report['enabled_count']} enabled, {report['blacklisted_count']} blacklisted)")
        print(f"\n-- Followed on IG but missing from digest ({len(report['missing_from_digest'])}) --")
        for e in report["missing_from_digest"]:
            print(f"  @{e['handle']}  ({e['category']}) — {e['name']}")
        print(f"\n-- In digest but NOT followed on IG ({len(report['not_followed_on_ig'])}) --")
        for e in report["not_followed_on_ig"]:
            print(f"  @{e['handle']} — {e['name']}")
        print(f"\n-- Inactive: zero reels in recent digests ({len(report['inactive_creators'])}) --")
        for e in report["inactive_creators"]:
            print(f"  @{e['handle']}  ({e['category']}) — {e['name']}")
        if args.import_missing:
            print(f"\nImported {report.get('imported_count', 0)} creators into sources.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
