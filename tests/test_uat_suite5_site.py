"""
test_uat_suite5_site.py — Automated tests for Suite 5: Variant 1A Static Site Compilation.
"""

from pathlib import Path
import pytest
import config
from site_builder import build_site


def test_uat_5_1_to_5_5_variant_1a_html_structure(tmp_path, monkeypatch):
    """
    Verifies UAT 5.1 through 5.5: Variant 1A Clean Reels Scrim output structure.
    """
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)

    items = [
        {
            "id": f"item_{i}",
            "creator_handle": f"creator_{i}",
            "rank": i + 1,
            "rank_display": f"#{i+1:02d}",
            "view_count": 100000,
            "category": "tech" if i % 2 == 0 else "health",
            "caption": f"Sample caption for video {i}",
            "thumbnail": f"https://example.com/thumb_{i}.jpg",
        }
        for i in range(5)
    ]

    r2_index, local_index = build_site({"run_date": "2026-09-06", "items": items})

    assert r2_index.exists()
    assert local_index.exists()

    html = r2_index.read_text(encoding="utf-8")

    # UAT-5.2: Zero in-video box clutter (no bulky floating boxes in center)
    assert "class=\"bottom-scrim\"" in html
    assert "class=\"floating-pill\"" not in html

    # UAT-5.3: Natural bottom scrim with creator handle and rank pill
    assert "@creator_0" in html
    assert "#01" in html

    # UAT-5.4: 5 Story Category Circles with Instagram gradient styling
    assert "data-category=\"all\"" in html
    assert "data-category=\"tech\"" in html
    assert "data-category=\"health\"" in html
    assert "data-category=\"explainer\"" in html
    assert "data-category=\"culture\"" in html

    # UAT-5.5: Next 2 video DOM preloading
    assert html.count("preload=\"auto\"") >= 3
