"""
test_pacing.py — Low-profile pacing contract after the Instagram automation warning.

Guards the slowdown mitigations and the rate-limit backoff: creator pacing,
serial enrichment, feed cooldowns, and overnight retry waits. Asserts the
named constants stay slow and that the pipelines actually consume them (so a
future edit cannot silently re-accelerate). No browser, network, or sleeping
is touched.
"""

from __future__ import annotations

from pathlib import Path

import config
import extractor
import main as main_module


def test_creator_pacing_is_slow():
    mu, sigma, floor = main_module.CREATOR_PAUSE
    assert mu >= 6.0 and floor >= 3.0
    assert main_module.CREATOR_BREAK_EVERY <= 10
    lo, hi = main_module.CREATOR_BREAK_SECS
    assert lo >= 150.0 and hi > lo


def test_enrichment_is_serial():
    assert main_module.ENRICH_WORKERS == 1
    mu, _sigma, floor = main_module.ENRICH_PAUSE
    assert mu >= 4.5 and floor >= 2.0


def test_feed_cooldowns_are_long():
    lo, hi = extractor.FEED_COOLDOWN_EVERY
    assert hi <= 14
    mu, _sigma, floor = extractor.FEED_COOLDOWN_SECS
    assert mu >= 30.0 and floor >= 15.0


def test_rate_limit_backoff_fits_overnight():
    waits = main_module.RATE_LIMIT_WAITS_MIN
    assert len(waits) >= 3 and all(w >= 15 for w in waits)
    assert sum(waits) <= 200  # worst case still fits an overnight window
    assert main_module.FEED_RETRY_WAIT_MIN >= 15


def test_cooling_down_resumes_like_extraction():
    assert "cooling_down" in main_module.RESUMABLE_SYNC_STAGES
    assert "cooling_down" not in main_module.RANKED_SYNC_STAGES  # no ranked list banked


def test_slowdown_is_wired_into_pipelines():
    main_src = Path(config.ROOT_DIR, "main.py").read_text(encoding="utf-8")
    assert "random.uniform(*CREATOR_BREAK_SECS)" in main_src
    assert "max_workers=ENRICH_WORKERS" in main_src
    assert "mu, sigma, floor = CREATOR_PAUSE" in main_src
    assert "mu, sigma, floor = ENRICH_PAUSE" in main_src
    assert "def _extract_with_backoff(handle, max_candidate_reels):" in main_src
    assert '"cooling_down"' in main_src
    assert "FEED_RETRY_WAIT_MIN * 60" in main_src
    ext_src = Path(config.ROOT_DIR, "extractor.py").read_text(encoding="utf-8")
    assert "random.randint(*FEED_COOLDOWN_EVERY)" in ext_src
    assert "mu, sigma, floor = FEED_COOLDOWN_SECS" in ext_src
