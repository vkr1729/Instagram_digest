"""
interactive_onboarding.py — Interactive Following Import for Instagram Digest.

Allows users to import their Instagram following list into sources.json:
  1. Additive only: Existing channels are NEVER overwritten or deleted.
  2. Clearly displays existing channels as already present.
  3. Interactive selection for new channels.
  4. Privacy-first: Playwright session uses a transient directory and deletes
     all cookies upon exit (zero persistent cookies stored on disk).
  5. Supports file fallback (Instagram data export JSON/ZIP or text file).

Usage:
  python interactive_onboarding.py
  python interactive_onboarding.py --file following.json
  python main.py --import-following
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import atomic_io
import config
import extractor

logger = logging.getLogger("InstagramDigest.Onboarding")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _infer_archetype_and_category(handle: str, name: str = "") -> tuple[str, str]:
    """Infer default category and anti-doomscroll archetype from handle/name heuristics.

    Mirrors extractor.categorize_creator keyword sets so CLI imports land in
    the same bucket the pipeline would assign (a 'health'/'food' creator must
    not import as entertainment).
    """
    text = f"{handle.lower()} {name.lower()}"
    words = set(re.findall(r'[a-zA-Z0-9]+', text))
    handle_lc = handle.lower()

    # Substring fallback on the raw handle (mirrors extractor.categorize_creator
    # direct-handle matching): compound handles like 'monitormunchies' never
    # split into matchable words, so check substrings before word tokens.
    if any(k in handle_lc for k in ("recipe", "cook", "food", "munchies", "masala", "kitchen", "chef")):
        return "educational", "food"
    if any(k in handle_lc for k in ("doctor", "cardio", "health", "clinic", "diet", "gym", "workout", "fitness")):
        return "educational", "health"

    tech_kw = {"tech", "ai", "code", "dev", "developer", "eng", "math", "data", "software", "python", "coding"}
    fin_kw = {"finance", "money", "invest", "investor", "stock", "wealth", "tax", "crypto", "econ", "trading"}
    health_kw = {"health", "fit", "fitness", "doc", "doctor", "med", "medical", "mind", "neuro", "psych", "dr",
                 "liver", "diet", "nutrition", "gym", "workout", "wellness", "cardio", "cardiologist", "clinic"}
    food_kw = {"food", "recipe", "recipes", "cook", "cooking", "chef", "kitchen", "protein", "calorie", "meal",
               "bake", "munchies", "masala"}
    niche_kw = {"science", "physics", "bio", "biology", "learn", "facts", "history", "space", "astronomy"}

    if words & tech_kw or any(k in text for k in ["technology", "developer", "programming"]):
        return "educational", "ai_tech"
    if words & fin_kw or any(k in text for k in ["finance", "investing"]):
        return "educational", "finance"
    if words & health_kw or any(k in text for k in ["healthcare"]):
        return "educational", "health"
    if words & food_kw:
        return "educational", "food"
    if words & niche_kw:
        return "educational", "niche"

    return "lifestyle", "entertainment"


def parse_following_export(file_path: str | Path) -> list[dict[str, str]]:
    """Parse following channels from Instagram data export JSON, ZIP, or text file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Export file not found: {path}")

    # Plain text file with handles
    if path.suffix.lower() in (".txt", ".csv"):
        lines = path.read_text(encoding="utf-8").splitlines()
        results = []
        for line in lines:
            h = line.strip().lstrip("@").lower()
            if h and not h.startswith("#") and extractor.clean_handle(h):
                results.append({"handle": h, "name": h})
        return results

    # ZIP archive
    if path.suffix.lower() == ".zip":
        import zipfile
        with zipfile.ZipFile(path, "r") as z:
            # Look for following.json inside archive
            candidates = [n for n in z.namelist() if "following.json" in n.lower()]
            if not candidates:
                raise ValueError("Could not find following.json inside ZIP archive.")
            with z.open(candidates[0]) as f:
                data = json.load(f)
                return _parse_json_following(data)

    # JSON file
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _parse_json_following(data)
    except Exception as exc:
        raise ValueError(f"Failed to parse JSON export: {exc}")


def _parse_json_following(data: Any) -> list[dict[str, str]]:
    """Parse raw JSON following structures from Instagram exports."""
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add_handle(handle: str, name: str = ""):
        h = extractor.clean_handle(handle.strip().lstrip("@").lower())
        if h and h not in seen:
            seen.add(h)
            results.append({"handle": h, "name": name or h})

    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                _add_handle(item)
            elif isinstance(item, dict):
                # Format: {"string_list_data": [{"value": "username"}]}
                if "string_list_data" in item:
                    for sub in item["string_list_data"]:
                        if isinstance(sub, dict) and sub.get("value"):
                            _add_handle(sub["value"], item.get("title", ""))
                elif item.get("handle"):
                    _add_handle(item["handle"], item.get("name", ""))
                elif item.get("username"):
                    _add_handle(item["username"], item.get("name", ""))

    elif isinstance(data, dict):
        # Format: {"relationships_following": [...]}
        rel = data.get("relationships_following") or data.get("following") or []
        if isinstance(rel, list):
            return _parse_json_following(rel)

    return results


def scrape_following_with_playwright(headless: bool = False, timeout_sec: int = 180) -> list[dict[str, str]]:
    """
    Launch temporary browser session for user to log into Instagram and pull following list.
    Guarantees ZERO persistent cookie storage by using an isolated temporary directory.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright is not installed. Install with: pip install playwright && playwright install chromium")
        return []

    collected_handles: list[dict[str, str]] = []
    seen_handles: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="ig_onboarding_") as temp_user_dir:
        logger.info("Starting isolated Playwright browser (zero persistent cookie storage)...")
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=temp_user_dir,
                headless=headless,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = browser.new_page()

            # Intercept GraphQL / API responses for following
            def handle_response(response):
                try:
                    url = response.url
                    if "/api/v1/friendships/" in url and "/following/" in url:
                        json_body = response.json()
                        users = json_body.get("users", [])
                        for u in users:
                            username = u.get("username")
                            full_name = u.get("full_name") or username
                            if username and username.lower() not in seen_handles:
                                clean = extractor.clean_handle(username.lower())
                                if clean:
                                    seen_handles.add(clean)
                                    collected_handles.append({"handle": clean, "name": full_name})
                except Exception:
                    pass

            page.on("response", handle_response)

            logger.info("Navigating to Instagram...")
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded")

            if not headless:
                print("\n" + "=" * 65)
                print("  INSTAGRAM ONBOARDING LOGIN")
                print("  Please log into your Instagram account in the opened browser.")
                print("  Once logged in, navigate to your profile -> Following.")
                print("  We will automatically capture the followed channels.")
                print("=" * 65 + "\n")

            # Wait for user to log in or timeout
            start_time = time.time()
            logged_in = False

            while time.time() - start_time < timeout_sec:
                time.sleep(2)
                # Check if logged in by looking for common profile/navigation cues
                try:
                    if page.locator("svg[aria-label='Home']").count() > 0 or \
                       page.locator("svg[aria-label='Direct']").count() > 0 or \
                       page.locator("a[href*='/direct/']").count() > 0:
                        logged_in = True
                        break
                except Exception:
                    pass

            if not logged_in and not seen_handles:
                logger.warning("Login timeout reached or login cancelled.")
                browser.close()
                return []

            logger.info("Login confirmed! Navigating to your following list...")
            # Allow user to scroll or let modal load
            for _ in range(15):
                time.sleep(1)
                if len(collected_handles) >= 50:
                    break

            browser.close()

    logger.info("Captured %d channels from Instagram session.", len(collected_handles))
    return collected_handles


def reconcile_following(following_list: list[dict[str, str]] | None) -> dict[str, Any]:
    """
    Compare following list against existing sources.json.
    Categorizes channels into:
      - already_present: Already configured in sources.json
      - missing: New channels available for additive import

    Never raises on malformed rows: a None list or a row without a usable
    handle is skipped (the dashboard passes cached JSON that may contain
    legacy shapes).
    """
    sources_file = config.SOURCES_FILE
    existing_sources: list[dict[str, Any]] = []
    if sources_file.exists():
        try:
            loaded = json.loads(sources_file.read_text(encoding="utf-8"))
            existing_sources = loaded if isinstance(loaded, list) else []
        except Exception as exc:
            logger.warning("Could not read sources.json: %s", exc)

    existing_map = {
        str(s.get("handle", "")).lower().lstrip("@"): s
        for s in existing_sources if isinstance(s, dict) and s.get("handle")
    }

    already_present: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for item in following_list or []:
        if not isinstance(item, dict):
            continue
        raw_handle = item.get("handle")
        if not isinstance(raw_handle, str) or not raw_handle.strip():
            continue
        h = extractor.clean_handle(raw_handle.lower().lstrip("@"))
        if not h:
            continue

        name = item.get("name") or h
        if h in existing_map:
            exist = existing_map[h]
            already_present.append({
                "handle": h,
                "name": exist.get("name") or name,
                "category": exist.get("category", "entertainment"),
                "archetype": exist.get("archetype", "educational"),
                "enabled": exist.get("enabled", True),
                "already_present": True,
            })
        else:
            arch, cat = _infer_archetype_and_category(h, name)
            missing.append({
                "handle": h,
                "name": name,
                "category": cat,
                "archetype": arch,
                "enabled": True,
                "already_present": False,
            })

    return {
        "total_extracted": len(following_list or []),
        "existing_sources_count": len(existing_sources),
        "already_present": already_present,
        "missing": missing,
        "already_present_count": len(already_present),
        "missing_count": len(missing),
    }


def merge_imported_channels(selected_handles: list[str] | None, candidates: list[dict[str, Any]] | None) -> int:
    """
    Add selected new channels to sources.json additively.
    NEVER overwrites or deletes existing channels.
    Non-string handles and malformed candidate rows are skipped, never crash.
    """
    selected_clean = [
        h.lower().lstrip("@") for h in (selected_handles or [])
        if isinstance(h, str) and extractor.clean_handle(h.lower().lstrip("@"))
    ]
    if not selected_clean:
        return 0

    candidates_map = {
        str(c["handle"]).lower().lstrip("@"): c for c in (candidates or [])
        if isinstance(c, dict) and isinstance(c.get("handle"), str) and c.get("handle")
    }

    with atomic_io.sources_file_lock():
        sources: list[dict[str, Any]] = []
        if config.SOURCES_FILE.exists():
            try:
                sources = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
            except Exception:
                sources = []

        if not isinstance(sources, list):
            sources = []

        existing_set = {str(s.get("handle", "")).lower().lstrip("@") for s in sources if isinstance(s, dict)}

        added_count = 0
        for h in selected_clean:
            if h in candidates_map and h not in existing_set:
                item = candidates_map[h]
                if not isinstance(item, dict):
                    continue
                sources.append({
                    "handle": h,
                    "name": item.get("name") or h,
                    "category": item.get("category") or "niche",
                    "archetype": item.get("archetype"),
                    "enabled": True,
                })
                existing_set.add(h)
                added_count += 1

        if added_count > 0:
            atomic_io.durable_write_json(config.SOURCES_FILE, sources)
            logger.info("Successfully merged %d new channels into sources.json (Total channels: %d).", added_count, len(sources))

        return added_count


def run_interactive_cli(source_file: str | None = None, select_all: bool = False) -> int:
    """Run interactive terminal wizard for channel onboarding."""
    print("\n" + "=" * 65)
    print("  INSTAGRAM DIGEST — ADDITIVE CHANNEL ONBOARDING")
    print("=" * 65)

    following_items: list[dict[str, str]] = []

    if source_file:
        print(f"Loading following list from file: {source_file}...")
        try:
            following_items = parse_following_export(source_file)
        except Exception as exc:
            print(f"Error loading export file: {exc}")
            return 1
    else:
        print("\nChoose an import source:")
        print("  1. Interactive Instagram Login (Opens browser, zero cookies kept)")
        print("  2. Load from Instagram Export file (JSON / ZIP)")
        print("  3. Load from text file of usernames (.txt)")
        print("  4. Cancel\n")

        choice = input("Enter choice (1-4) [default: 1]: ").strip() or "1"

        if choice == "1":
            following_items = scrape_following_with_playwright(headless=False)
        elif choice in ("2", "3"):
            path_str = input("Enter path to file: ").strip()
            if not path_str:
                print("No file provided. Aborting.")
                return 1
            try:
                following_items = parse_following_export(path_str)
            except Exception as exc:
                print(f"Error reading file: {exc}")
                return 1
        else:
            print("Import cancelled.")
            return 0

    if not following_items:
        print("No channels found to import.")
        return 0

    # Reconcile against sources.json
    report = reconcile_following(following_items)

    print("\n" + "-" * 65)
    print(f"  Summary: {report['total_extracted']} channels processed")
    print(f"  • {report['already_present_count']} channels are ALREADY in your sources.json (Preserved)")
    print(f"  • {report['missing_count']} NEW channels found available for import")
    print("-" * 65 + "\n")

    if report["already_present"]:
        print("Existing Channels Already Configured:")
        for ch in report["already_present"][:10]:
            print(f"  ✓ @{ch['handle']:<20} ({ch['category']}) — [ALREADY PRESENT]")
        if len(report["already_present"]) > 10:
            print(f"  ... and {len(report['already_present']) - 10} more existing channels.")

    if not report["missing"]:
        print("\nAll followed channels are already present in sources.json! Nothing to add.")
        return 0

    print(f"\nNew Channels Available to Import ({len(report['missing'])}):")
    for idx, ch in enumerate(report["missing"], 1):
        arch_label = "📚 Edu" if ch["archetype"] == "educational" else "🎬 Life"
        print(f"  [{idx:02d}] @{ch['handle']:<22} {arch_label} ({ch['category']}) — {ch['name']}")

    if select_all:
        selected_handles = [c["handle"] for c in report["missing"]]
    else:
        print("\nSelection Options:")
        print("  'all'      : Import all new channels")
        print("  '1,2,5'    : Import specific numbers")
        print("  'cancel'   : Do not import any\n")
        user_input = input("Select channels to import [default: all]: ").strip().lower() or "all"

        if user_input in ("cancel", "q", "quit", "no"):
            print("Import cancelled. No changes made.")
            return 0
        elif user_input == "all":
            selected_handles = [c["handle"] for c in report["missing"]]
        else:
            selected_indices = set()
            for part in user_input.replace(" ", "").split(","):
                if part.isdigit():
                    selected_indices.add(int(part))
            selected_handles = [
                report["missing"][i - 1]["handle"]
                for i in selected_indices
                if 1 <= i <= len(report["missing"])
            ]

    if not selected_handles:
        print("No new channels selected. No changes made.")
        return 0

    added = merge_imported_channels(selected_handles, report["missing"])
    print(f"\n🎉 Successfully added {added} new channels to sources.json!")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Instagram Digest Additive Following Onboarding")
    parser.add_argument("--file", type=str, help="Path to export file (JSON, ZIP, or TXT)")
    parser.add_argument("--all", action="store_true", help="Automatically select all missing channels")
    args = parser.parse_args()

    return run_interactive_cli(source_file=args.file, select_all=args.all)


if __name__ == "__main__":
    sys.exit(main())
