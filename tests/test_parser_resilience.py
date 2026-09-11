"""
test_parser_resilience.py — Regression tests for P0-3 selector/block hardening.

Covers: shortcode href variants, flexible date parsing, soft-block snippet
detection, discovery selector fallback, and fail-closed empty grids.
No network.
"""

import extractor


def test_extract_shortcode_href_variants():
    assert extractor._extract_shortcode("/reel/AbC123_-x/") == "AbC123_-x"
    assert extractor._extract_shortcode("/someuser/reel/AbC123/?hl=en") == "AbC123"
    assert extractor._extract_shortcode("https://www.instagram.com/reel/AbC123") == "AbC123"
    assert extractor._extract_shortcode("/p/AbC123/") == ""
    assert extractor._extract_shortcode("") == ""
    assert extractor._extract_shortcode(None) == ""


def test_parse_date_flexible_formats():
    assert extractor._parse_date_flexible("September 6, 2026") > 0
    assert extractor._parse_date_flexible("Sep 6, 2026") > 0
    assert extractor._parse_date_flexible("2026-09-06") > 0
    assert extractor._parse_date_flexible("2026-09-06T12:00:00Z") > 0
    assert extractor._parse_date_flexible("September 6, 2026") == extractor._parse_date_flexible("2026-09-06")
    assert extractor._parse_date_flexible("not a date") == 0
    assert extractor._parse_date_flexible("") == 0


def test_soft_block_snippets_detected():
    assert extractor._page_html_indicates_block("<div>challenge_required</div>")
    assert extractor._page_html_indicates_block("We limit how often you can do certain things")
    assert extractor._page_html_indicates_block("TRY AGAIN LATER")
    assert not extractor._page_html_indicates_block("<div>normal reels grid</div>")
    assert not extractor._page_html_indicates_block("")


def test_assert_not_blocked_extended_markers():
    class Page:
        url = "https://www.instagram.com/checkpoint/blocked/"

    try:
        extractor._assert_not_blocked(Page(), "test")
    except extractor.InstagramBlocked:
        pass
    else:
        raise AssertionError("checkpoint URL must raise InstagramBlocked")


def _fake_anchor(href):
    class Anchor:
        def get_attribute(self, name):
            return href

        def inner_text(self):
            return "1.2M views"

        def inner_html(self):
            return ""

        def locator(self, sel):
            class Loc:
                def count(self):
                    return 0

            return Loc()

    return Anchor()


def _discovery_session(html, anchors_by_selector):
    class Locator:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class Page:
        url = "https://www.instagram.com/somehandle/reels/"

        def goto(self, *a, **k):
            pass

        def wait_for_selector(self, selector, timeout=None):
            if selector not in anchors_by_selector:
                raise TimeoutError(selector)

        def locator(self, selector):
            return Locator(anchors_by_selector.get(selector, []))

        def content(self):
            return html

    class Session:
        def get_page(self):
            return Page()

    return Session()


def test_discovery_falls_back_to_secondary_selector():
    sess = _discovery_session(
        "<div>grid</div>",
        {"a[href*='/reel']": [_fake_anchor("/reel/AAA111/")]},
    )
    found = extractor.discover_creator_reel_urls("somehandle", max_reels=5, session=sess)
    assert [r["id"] for r in found] == ["AAA111"]


def test_discovery_empty_grid_with_block_markers_raises():
    sess = _discovery_session("<div>try again later</div>", {})
    try:
        extractor.discover_creator_reel_urls("somehandle", max_reels=5, session=sess)
    except extractor.InstagramBlocked:
        pass
    else:
        raise AssertionError("soft-blocked empty grid must raise InstagramBlocked")


def test_discovery_empty_grid_without_markers_returns_empty():
    sess = _discovery_session("<div>no reels yet</div>", {})
    assert extractor.discover_creator_reel_urls("somehandle", max_reels=5, session=sess) == []
