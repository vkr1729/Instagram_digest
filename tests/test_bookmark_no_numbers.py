"""test_bookmark_no_numbers.py — Bookmarks are a personal timeline, not rankings.

Regression tests:
  1. _run_reconcile restores each late-uploaded reel to its true rank from
     the R2 key prefix (never appends at max_rank+1, which produced the
     #251-271 tail out of the #29-49 slots).
  2. Bookmark grids show no rank badges (iOS grid + player HUD, PWA grid +
     overlay) and sort newest-first.
  3. Bookmark thumbnails prefer the permanent R2 portrait with a Pages
     portrait fallback for current-week saves.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
SOURCES = Path(__file__).resolve().parent.parent / "Sources" / "InstagramDigest"


def _js():
    return (TEMPLATES / "partials" / "player.js").read_text(encoding="utf-8")


def test_reconcile_restores_true_rank_from_r2_key():
    import main
    import inspect
    src = inspect.getsource(main._run_reconcile)
    assert "true_rank" in src
    assert "items.sort" in src
    assert "base + 1 + merged" not in src


def test_pwa_bookmark_grid_has_no_rank_badge():
    js = _js()
    grid_fn = js[js.index("overlayList.forEach((r, i) => {"):js.index("function filterBookmarksGrid")]
    assert "rankBadge" not in grid_fn
    assert "grid-card-rank" not in grid_fn
    assert "r.rank" not in grid_fn


def test_pwa_bookmark_overlay_has_no_rank_label():
    js = _js()
    overlay_fn = js[js.index("function paintOverlay()"):js.index("function stepOverlay")]
    assert "rankLabel" not in overlay_fn


def test_pwa_bookmark_thumbnail_prefers_r2_with_pages_fallback():
    js = _js()
    assert "r.thumbnail_url || `${_basePath}/thumbnails/${r.id}_portrait.jpg`" in js


def test_ios_bookmark_grid_has_no_rank_badge():
    src = (SOURCES / "Views" / "Modals" / "BookmarksSheet.swift").read_text(encoding="utf-8")
    grid_section = src[:src.index("BookmarkPlayerOverlay(")]
    assert "#%02d" not in grid_section


def test_ios_bookmark_player_has_no_rank_badge():
    src = (SOURCES / "Views" / "Modals" / "BookmarksSheet.swift").read_text(encoding="utf-8")
    player_section = src[src.index("BookmarkPlayerOverlay("):]
    assert "#%02d" not in player_section


def test_bookmarks_still_sort_newest_first():
    src = (SOURCES / "Views" / "Modals" / "BookmarksSheet.swift").read_text(encoding="utf-8")
    assert "sort: \\BookmarkItem.bookmarkedAt, order: .reverse" in src
    js = _js()
    assert ".slice().reverse()" in js
