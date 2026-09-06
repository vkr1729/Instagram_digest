"""
test_site_builder.py — Unit tests for static site compilation and template rendering.
"""

from pathlib import Path
import config
from site_builder import build_site


def test_build_site_renders_index_and_local_index(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)

    sample_digest = {
        "run_date": "2026-09-06",
        "items": [
            {
                "id": "reel_01",
                "creator_handle": "mkbhd",
                "creator_name": "Marques Brownlee",
                "category": "ai_tech",
                "rank": 1,
                "rank_display": "#01",
                "view_count": 500000,
                "caption": "Reviewing folding phones",
                "thumbnail": "https://example.com/thumb1.jpg",
                "video_url": "https://example.com/video1.mp4",
            },
            {
                "id": "reel_02",
                "creator_handle": "hubermanlab",
                "creator_name": "Dr. Andrew Huberman",
                "category": "health",
                "rank": 2,
                "rank_display": "#02",
                "view_count": 250000,
                "caption": "Morning light exposure protocols",
                "thumbnail": "https://example.com/thumb2.jpg",
                "video_url": "https://example.com/video2.mp4",
            }
        ]
    }

    r2_url_map = {
        "reel_01": "https://pub-r2.dev/videos/2026-09-06/01_mkbhd_reel_01.mp4"
    }

    r2_index, local_index = build_site(sample_digest, r2_uploaded_urls=r2_url_map)

    assert r2_index.exists()
    assert local_index.exists()
    assert (tmp_path / "data.json").exists()
    assert (tmp_path / ".nojekyll").exists()

    r2_html = r2_index.read_text(encoding="utf-8")
    local_html = local_index.read_text(encoding="utf-8")

    # Verify R2 URL is used in r2_index
    assert "https://pub-r2.dev/videos/2026-09-06/01_mkbhd_reel_01.mp4" in r2_html

    # Verify local video path is used in local_index
    assert "/videos/2026-09-06/01_mkbhd_reel_01.mp4" in local_html

    # Verify Variant 1A features in both
    for html in (r2_html, local_html):
        assert "Instagram" in html
        assert "1.5x" in html
        assert "data-category=\"ai_tech\"" in html
        assert "data-category=\"health\"" in html
        assert "Top 200" in html
        assert "AI &amp; Tech" in html or "AI & Tech" in html
        assert "Health" in html
        assert "Entertainment" in html
        assert "preload=\"auto\"" in html
        assert "You're All Caught Up!" in html
        assert 'id="jumpBtn"' in html
        assert 'id="jumpModal"' in html

    # Verify unselect button and channels link are local-only
    assert 'class="unselect-channel-btn"' in local_html
    assert 'href="/channels"' in local_html
    assert 'class="unselect-channel-btn"' not in r2_html
    assert 'href="/channels"' not in r2_html
