"""
test_xss_hardening.py — Regression tests for P0-6 stored-XSS hardening.

Covers: feed share/unselect handlers must not interpolate values into JS
string literals; share pages must html.escape caption/handle/URLs.
"""

import config
from site_builder import build_site


def _evil_digest():
    return {
        "run_date": "2026-09-06",
        "items": [
            {
                "id": "abc123",
                "creator_handle": "a'b\"<img src=x onerror=alert(1)>",
                "creator_name": "Evil",
                "category": "ai_tech",
                "rank": 1,
                "rank_display": "#01",
                "view_count": 100000,
                "caption": "Fish & Chips <script>alert('xss')</script> \"quoted\"",
                "thumbnail": "https://example.com/t.jpg",
                "video_url": "https://example.com/v.mp4",
            }
        ],
    }


def test_feed_handlers_use_dataset_not_js_string_interpolation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    digest = _evil_digest()
    url_map = {"abc123": "https://pub-r2.dev/videos/2026-09-06/01_evil_abc123.mp4"}
    _, local_index = build_site(digest, r2_uploaded_urls=url_map)
    html = local_index.read_text(encoding="utf-8")

    # No attacker value may appear inside a JS single-quoted onclick string.
    assert "shareReelWhatsApp('" not in html
    assert "unselectCreator('" not in html
    # Handlers must consume values via dataset instead.
    assert "this.dataset.id" in html
    assert "this.dataset.handle" in html
    # Raw attack payload must not survive anywhere in the bundle.
    assert "<script>alert" not in html
    assert "<img src=x" not in html


def test_share_page_escapes_caption_handle_and_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_DIR", tmp_path)
    digest = _evil_digest()
    url_map = {"abc123": "https://pub-r2.dev/videos/2026-09-06/01_evil_abc123.mp4"}
    build_site(digest, r2_uploaded_urls=url_map)

    html = (tmp_path / "share" / "abc123.html").read_text(encoding="utf-8")

    # & must be escaped first; < > " ' must all be escaped.
    assert "Fish &amp; Chips" in html
    assert "&lt;script&gt;" in html
    assert "<script>alert" not in html
    assert "<img src=x" not in html
    # Attacker handle must not break out of title/meta/body contexts.
    assert "a&#x27;b" in html or "a&#39;b" in html
