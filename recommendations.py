"""
recommendations.py — AI-powered creator discovery using headless Antigravity (agy -p).
Discovers similar Instagram creators per category via deep web search.

Request 12 per category, keep 10: the headroom backfills slots freed by
exclusions (channels, do-not-recommend, 5x-ignored) so each refresh still
lands ~10 fresh faces per category.

A persistent feedback file (data/recommendation_feedback.json) records per
creator exposures plus explicit do-not-recommend handles. It steers the AI
prompt (channels + DNR + history) and enforces two rules:
  * DNR handles are never suggested again.
  * Handles suggested 5 times without being added stop being suggested;
    their slots go to new creators.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import atomic_io
import config

logger = logging.getLogger("InstagramDigest.Recommendations")

RECOMMENDED_FILE: Path | None = None
QUARANTINE_FILE: Path | None = None
FEEDBACK_FILE: Path | None = None

#: Suggest-then-ignore limit: a creator recommended this many times without
#: being added stops being recommended; the slot goes to someone new.
MAX_EXPOSURES = 5
#: AI returns extras so exclusions backfill instead of shrinking the set.
REQUEST_PER_CATEGORY = 12
KEEP_PER_CATEGORY = 10
#: Cap per feedback list inside the AI prompt (token bound).
PROMPT_LIST_CAP = 30

_FEEDBACK_LOCK = threading.Lock()


def get_recommended_file() -> Path:
    if RECOMMENDED_FILE is not None:
        return RECOMMENDED_FILE
    return config.DATA_DIR / "recommended_creators.json"


def get_quarantine_file() -> Path:
    if QUARANTINE_FILE is not None:
        return QUARANTINE_FILE
    return config.DATA_DIR / "recommended_creators.quarantine.json"


def get_feedback_file() -> Path:
    if FEEDBACK_FILE is not None:
        return FEEDBACK_FILE
    return config.DATA_DIR / "recommendation_feedback.json"


def normalize_rec_handle(handle: Any) -> str:
    """Normalize a creator handle for feedback/exclusion comparisons."""
    return str(handle or "").strip().lstrip("@").lower()


def _default_feedback() -> dict[str, Any]:
    return {"version": 1, "exposures": {}, "do_not_recommend": []}


def load_feedback() -> dict[str, Any]:
    """Load recommendation feedback (exposures + do-not-recommend). Tolerant:
    missing/corrupt files yield defaults, corrupt files are quarantined."""
    fb_file = get_feedback_file()
    if not fb_file.exists():
        return _default_feedback()
    try:
        data = json.loads(fb_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("feedback root must be an object")
        exposures = data.get("exposures")
        clean_exp: dict[str, int] = {}
        if isinstance(exposures, dict):
            for k, v in exposures.items():
                try:
                    clean_exp[str(k)] = max(0, int(v))
                except (TypeError, ValueError):
                    continue
        dnr = data.get("do_not_recommend")
        return {
            "version": 1,
            "exposures": clean_exp,
            "do_not_recommend": list(dnr) if isinstance(dnr, list) else [],
        }
    except Exception as exc:
        logger.warning("Error reading recommendation feedback (quarantining): %s", exc)
        try:
            qname = fb_file.with_name(f"{fb_file.name}.corrupt-{int(time.time())}")
            fb_file.rename(qname)
        except Exception:
            pass
        return _default_feedback()


def save_feedback(feedback: dict[str, Any]) -> None:
    """Persist feedback atomically."""
    payload = {
        "version": 1,
        "exposures": dict(feedback.get("exposures") or {}),
        "do_not_recommend": sorted({normalize_rec_handle(h) for h in (feedback.get("do_not_recommend") or []) if normalize_rec_handle(h)}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_io.durable_write_json(get_feedback_file(), payload)


def record_exposures(handles: list[str]) -> dict[str, int]:
    """Count one recommendation event per handle; returns the updated map."""
    with _FEEDBACK_LOCK:
        fb = load_feedback()
        exposures = {str(k): int(v) for k, v in fb.get("exposures", {}).items()}
        for h in handles:
            nh = normalize_rec_handle(h)
            if nh:
                exposures[nh] = exposures.get(nh, 0) + 1
        fb["exposures"] = exposures
        save_feedback(fb)
        return exposures


def add_do_not_recommend(handle: str) -> bool:
    """Persist an explicit rejection. Returns True if it changed anything."""
    nh = normalize_rec_handle(handle)
    if not nh:
        return False
    with _FEEDBACK_LOCK:
        fb = load_feedback()
        dnr = {normalize_rec_handle(h) for h in fb.get("do_not_recommend", [])}
        if nh in dnr:
            return False
        dnr.add(nh)
        fb["do_not_recommend"] = sorted(dnr)
        save_feedback(fb)
        return True


def clear_do_not_recommend(handle: str) -> bool:
    """Drop a handle from the rejection list (e.g. user added it anyway)."""
    nh = normalize_rec_handle(handle)
    if not nh:
        return False
    with _FEEDBACK_LOCK:
        fb = load_feedback()
        dnr = {normalize_rec_handle(h) for h in fb.get("do_not_recommend", [])}
        if nh not in dnr:
            return False
        dnr.discard(nh)
        fb["do_not_recommend"] = sorted(dnr)
        save_feedback(fb)
        return True


def prune_recommended_cache(handles: set[str]) -> int:
    """Remove handles from the persisted recommendation set (DNR takes effect
    immediately, before the next refresh). Returns removed count."""
    unwanted = {normalize_rec_handle(h) for h in handles if normalize_rec_handle(h)}
    if not unwanted:
        return 0
    rec_file = get_recommended_file()
    if not rec_file.exists():
        return 0
    try:
        raw = json.loads(rec_file.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if isinstance(raw, dict):
        creators = raw.get("creators", [])
    elif isinstance(raw, list):
        creators, raw = raw, None
    else:
        return 0
    kept = [c for c in creators
            if normalize_rec_handle(c.get("handle") if isinstance(c, dict) else None) not in unwanted]
    removed = len(creators) - len(kept)
    if removed and raw is not None:
        raw["creators"] = kept
        raw["total_count"] = len(kept)
        try:
            atomic_io.durable_write_json(rec_file, raw)
        except Exception as exc:
            logger.warning("Could not prune recommended cache: %s", exc)
            return 0
    return removed


def apply_exclusions(
    recs: list[dict[str, Any]],
    seen: set[str],
    hard_exclude: set[str],
) -> list[dict[str, Any]]:
    """Post-discovery filter: drop strays the scout returned despite the
    exclusion set (sanitize normally catches these; mocks/odds slips land
    here) so they never occupy a slot meant for a new creator."""
    fresh: list[dict[str, Any]] = []
    for r in recs:
        h = normalize_rec_handle(r.get("handle") if isinstance(r, dict) else None)
        if h and h not in seen and h not in hard_exclude:
            fresh.append(r)
    return fresh


def build_steering_context(
    do_not_recommend: set[str] | list[str],
    previously_suggested: set[str] | list[str],
    added_examples: set[str] | list[str],
    cap: int = PROMPT_LIST_CAP,
) -> str:
    """Compact prompt addendum steering the scout: hard rejects, positive
    examples (added past suggestions), and anti-repeat history."""
    parts: list[str] = []
    dnr = sorted({normalize_rec_handle(h) for h in do_not_recommend if normalize_rec_handle(h)})[:cap]
    if dnr:
        parts.append(
            "The user explicitly rejected these creators — NEVER suggest them "
            "or closely similar accounts: " + ", ".join(f"@{h}" for h in dnr) + "."
        )
    added = sorted({normalize_rec_handle(h) for h in added_examples if normalize_rec_handle(h)})[:cap]
    if added:
        parts.append(
            "The user chose to follow these past suggestions — prefer more "
            "creators like them: " + ", ".join(f"@{h}" for h in added) + "."
        )
    prev = sorted({normalize_rec_handle(h) for h in previously_suggested if normalize_rec_handle(h)})[:cap]
    if prev:
        parts.append(
            "Already suggested in the past — do NOT repeat them, find NEW "
            "creators: " + ", ".join(f"@{h}" for h in prev) + "."
        )
    return " ".join(parts)

SCHEMA_DEF = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "handle": {"type": "string"},
            "name": {"type": "string"},
            "category": {"type": "string"},
            "reason": {"type": "string"},
            "follower_scale": {"type": "string"}
        },
        "required": ["handle", "name", "category", "reason"],
        "additionalProperties": False
    }
}

VALID_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def _find_agy_binary() -> str | None:
    """Resolve full path to agy binary."""
    agy_path = shutil.which("agy")
    if agy_path:
        return agy_path
    local_agy = Path.home() / ".local" / "bin" / "agy"
    if local_agy.exists() and os.access(local_agy, os.X_OK):
        return str(local_agy)
    return None


def check_agy_auth(timeout: int = 15) -> bool:
    """Run a quick non-interactive smoke prompt to verify agy is runnable and authenticated."""
    agy_bin = _find_agy_binary()
    if not agy_bin:
        logger.warning("Antigravity CLI (agy) binary not found on PATH or ~/.local/bin.")
        return False
    try:
        cmd = [agy_bin, "-p", "Respond with exact text PONG", "--print-timeout", f"{timeout}s"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if res.returncode == 0 and "PONG" in res.stdout:
            return True
        logger.warning("agy auth preflight failed (code %d): %s", res.returncode, res.stderr.strip()[:200])
        return False
    except Exception as exc:
        logger.warning("agy auth preflight exception: %s", exc)
        return False


def sanitize_and_validate_recommendations(
    raw_text: str,
    category: str,
    existing_handles: set[str],
    limit: int = KEEP_PER_CATEGORY,
) -> list[dict[str, Any]]:
    """Defensively parse and sanitize JSON output from agy -p."""
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
        text = text.strip()

    try:
        data = json.loads(text)
    except Exception as exc:
        # Fallback regex extraction of JSON array
        m = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                logger.warning("Failed parsing JSON from category %s: %s", category, exc)
                return []
        else:
            logger.warning("No JSON array found in category %s: %s", category, exc)
            return []

    # Unwrap agy CLI json envelope if present: {"conversation_id": ..., "response": "..."}
    if isinstance(data, dict) and "response" in data:
        resp_text = str(data["response"]).strip()
        if resp_text.startswith("```"):
            resp_text = re.sub(r"^```[a-zA-Z]*\n", "", resp_text)
            resp_text = re.sub(r"\n```$", "", resp_text)
            resp_text = resp_text.strip()
        try:
            data = json.loads(resp_text)
        except Exception:
            m = re.search(r"\[\s*\{.*\}\s*\]", resp_text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    pass
    elif isinstance(data, dict) and "creators" in data and isinstance(data["creators"], list):
        data = data["creators"]

    if not isinstance(data, list):
        logger.warning("Expected JSON array for category %s, got %s", category, type(data).__name__)
        return []

    valid: list[dict[str, Any]] = []
    seen_handles: set[str] = set()

    for item in data:
        if not isinstance(item, dict):
            continue
        raw_handle = str(item.get("handle") or "").strip().lstrip("@").lower()
        if not raw_handle or not VALID_HANDLE_PATTERN.match(raw_handle):
            continue
        if raw_handle in existing_handles or raw_handle in seen_handles:
            continue

        name = str(item.get("name") or raw_handle).strip()
        reason = str(item.get("reason") or f"High quality {category} creator").strip()
        follower_scale = str(item.get("follower_scale") or "").strip()

        valid.append({
            "handle": raw_handle,
            "name": name,
            "category": category,
            "reason": reason,
            "follower_scale": follower_scale,
            "recommended_at": datetime.now(timezone.utc).isoformat(),
        })
        seen_handles.add(raw_handle)
        if len(valid) >= limit:
            break

    return valid


def discover_category_creators(
    category: str,
    sample_creators: list[str],
    existing_handles: set[str],
    timeout_secs: int = 600,
    steering: str = "",
) -> list[dict[str, Any]]:
    """Invoke agy -p for a single category with a 600-second timeout.

    `existing_handles` is the hard exclusion set (channels + DNR +
    5x-ignored + anti-repeat); `steering` carries the why (rejects,
    positives, history) so the scout finds NEW creators instead.
    """
    agy_bin = _find_agy_binary()
    if not agy_bin:
        return []

    sample_str = ", ".join(f"@{c}" for c in sample_creators[:8])
    prompt = (
        f"You are a talent scout for high-signal Instagram content. "
        f"In the category '{category}', the user follows creators: {sample_str}. "
        f"Do a deep web search and identify {REQUEST_PER_CATEGORY} similar HIGH QUALITY, active Instagram creators in '{category}' "
        f"whose content style and depth matches or exceeds these creators. "
        f"Return ONLY a valid JSON array of {REQUEST_PER_CATEGORY} creators with keys: handle, name, category, reason (why they are recommended based on the user's tastes), "
        f"and follower_scale (e.g. '250K followers')."
    )
    if steering:
        prompt += f" Additional guidance from the user's past choices: {steering}"

    cmd = [
        agy_bin,
        "-p", prompt,
        "--output-format", "json",
        "--print-timeout", f"{timeout_secs}s",
    ]

    logger.info("Discovering recommended creators for category '%s' (timeout: %ds)...", category, timeout_secs)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_secs + 15)
        if res.returncode != 0:
            logger.warning("agy -p error for category %s (code %d): %s", category, res.returncode, res.stderr.strip()[:250])
            return []
        return sanitize_and_validate_recommendations(res.stdout, category, existing_handles)
    except subprocess.TimeoutExpired:
        logger.warning("agy -p timed out after %ds for category %s", timeout_secs, category)
        return []
    except Exception as exc:
        logger.warning("agy -p invocation failed for category %s: %s", category, exc)
        return []


def load_recommended_creators() -> list[dict[str, Any]]:
    """Load previously cached recommended creators."""
    rec_file = get_recommended_file()
    if not rec_file.exists():
        return []
    try:
        data = json.loads(rec_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("creators", [])
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.warning("Error reading recommended_creators.json: %s", exc)
    return []


def refresh_recommendations(force: bool = False, timeout_per_category: int = 600) -> list[dict[str, Any]]:
    """Refresh recommendations across all categories using agy -p."""
    logger.info("Starting AI creator recommendations refresh (force=%s)...", force)

    rec_file = get_recommended_file()
    # Check cache if not force: if cache exists and was updated within last 6 days, reuse it.
    # NOTE: rec_file lives under config.DATA_DIR, which the test-suite repoints
    # at tmp_path per-test — so tests with no cache file proceed to discovery
    # (and must stub check_agy_auth/discover_category_creators).
    if not force and rec_file.exists():
        try:
            cached_data = json.loads(rec_file.read_text(encoding="utf-8"))
            if isinstance(cached_data, dict):
                updated_at_str = cached_data.get("updated_at")
                cached_creators = cached_data.get("creators", [])
                if updated_at_str and len(cached_creators) >= 10:
                    updated_at = datetime.fromisoformat(updated_at_str)
                    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
                    if age_seconds < 6 * 86400:
                        logger.info("Using cached recommended creators (%d creators, age %.1f hours)",
                                    len(cached_creators), age_seconds / 3600.0)
                        return cached_creators
        except Exception as read_err:
            logger.debug("Error checking existing recommendations cache: %s", read_err)

    # 1. Load active sources and categorize
    sources: list[dict[str, Any]] = []
    if config.SOURCES_FILE.exists():
        try:
            sources = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
        except Exception:
            sources = []

    existing_handles = {s.get("handle", "").lower().lstrip("@") for s in sources if s.get("handle")}

    # 1b. Feedback exclusions + steering: do-not-recommend is a hard never;
    # creators suggested MAX_EXPOSURES times without being added retire and
    # free their slots for new faces. History steers the scout prompt.
    feedback = load_feedback()
    exposures: dict[str, int] = {str(k): int(v) for k, v in (feedback.get("exposures") or {}).items()}
    dnr_handles = {normalize_rec_handle(h) for h in (feedback.get("do_not_recommend") or []) if normalize_rec_handle(h)}
    retired_handles = {h for h, c in exposures.items() if c >= MAX_EXPOSURES and h not in existing_handles}
    hard_exclude = set(existing_handles) | dnr_handles | retired_handles
    if retired_handles:
        logger.info("Retiring %d over-exposed creators (>=%d suggestions, never added).",
                    len(retired_handles), MAX_EXPOSURES)
    previously_suggested = (set(exposures) | {
        normalize_rec_handle(c.get("handle")) for c in load_recommended_creators() if isinstance(c, dict)
    }) - set(existing_handles) - dnr_handles
    added_examples = (set(exposures) | previously_suggested) & set(existing_handles)
    steering_text = build_steering_context(dnr_handles, previously_suggested, added_examples)

    by_category: dict[str, list[str]] = {}
    for s in sources:
        cat = s.get("category", "entertainment")
        h = s.get("handle", "").lower().lstrip("@")
        if h:
            by_category.setdefault(cat, []).append(h)

    # Ensure all 6 target categories exist
    target_categories = ["ai_tech", "finance", "health", "food", "entertainment", "niche"]
    for cat in target_categories:
        by_category.setdefault(cat, [])

    # 2. Auth Preflight
    if not check_agy_auth(timeout=15):
        logger.warning("agy auth check failed; preserving existing recommendations cache.")
        cached = load_recommended_creators()
        if cached:
            try:
                atomic_io.durable_write_json(rec_file, {
                    "version": 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "recommendations_stale": True,
                    "creators": cached,
                })
            except Exception:
                pass
        return cached

    # 3. Discover category by category (parallel: 2 workers halves the
    # ~24min serial wall-clock; each agy call is network-bound on the LLM
    # side, so threads don't contend locally).
    new_recommendations: list[dict[str, Any]] = []
    successful_categories = 0
    all_seen = set(existing_handles)

    import threading
    from concurrent.futures import ThreadPoolExecutor

    _rec_lock = threading.Lock()

    def _discover_one(cat: str) -> tuple[str, list[dict[str, Any]]]:
        samples = by_category.get(cat, [])
        return cat, discover_category_creators(
            cat, samples, set(all_seen) | hard_exclude,
            timeout_secs=timeout_per_category, steering=steering_text,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(_discover_one, target_categories))

    for cat, recs in results:
        if recs:
            successful_categories += 1
            with _rec_lock:
                fresh = apply_exclusions(recs, all_seen, hard_exclude)
                for r in fresh:
                    new_recommendations.append(r)
                    all_seen.add(r["handle"])
            logger.info("Discovered %d recommendations for category '%s'", len(fresh), cat)
        else:
            logger.warning("Category '%s' produced zero valid recommendations.", cat)

    # 4. Gate on at least 3 successful categories
    if successful_categories >= 3 and new_recommendations:
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "recommendations_stale": False,
            "successful_categories": successful_categories,
            "total_count": len(new_recommendations),
            "creators": new_recommendations,
        }
        atomic_io.durable_write_json(rec_file, payload)
        # One exposure per served creator: the 5th unanswered suggestion
        # retires the handle (see hard_exclude above).
        record_exposures([r["handle"] for r in new_recommendations])
        logger.info("Successfully refreshed %d recommended creators across %d categories.",
                    len(new_recommendations), successful_categories)
        return new_recommendations
    else:
        logger.warning("Only %d categories succeeded (minimum 3 required); quarantining and preserving prior cache.",
                       successful_categories)
        if new_recommendations:
            try:
                quarantine_file = get_quarantine_file()
                atomic_io.durable_write_json(quarantine_file, {
                    "quarantined_at": datetime.now(timezone.utc).isoformat(),
                    "successful_categories": successful_categories,
                    "partial_results": new_recommendations,
                })
            except Exception:
                pass
        cached = load_recommended_creators()
        if cached and rec_file.exists():
            try:
                raw = json.loads(rec_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw["recommendations_stale"] = True
                    atomic_io.durable_write_json(rec_file, raw)
            except Exception:
                pass
        return cached


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="AI Similar Creator Recommendations via Antigravity")
    parser.add_argument("--refresh", action="store_true", help="Force refresh recommendations across categories")
    parser.add_argument("--timeout", type=int, default=600, help="Per-category agy timeout in seconds (default 600)")
    args = parser.parse_args()

    results = refresh_recommendations(force=args.refresh, timeout_per_category=args.timeout)
    print(f"Total active recommendations: {len(results)}")
