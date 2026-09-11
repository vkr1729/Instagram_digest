"""
test_dom_perf.py — Static regression tests for D3 DOM/memory-scaling fixes.

Asserts the shipped CSS/JS contains the containment, scoped compositing,
filter-detach, and dblclick scroll-guard markers.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_reel_card_containment_and_scoped_compositing():
    css = _read("partials/styles.css")
    assert ".reel-card {" in css
    assert "content-visibility: auto" in css
    assert "contain-intrinsic-size" in css
    assert "contain: layout style paint" in css
    # will-change must be scoped to the active card only.
    assert ".reel-card.is-active .reel-video" in css
    assert css.count("will-change") == 1


def test_active_class_toggled_on_playback_paths():
    js = _read("partials/player.js")
    assert js.count("card.classList.add('is-active')") >= 2  # play + prepare
    assert "remove('is-active')" in js


def test_filter_hide_detaches_video_src():
    js = _read("partials/player.js")
    hide_block = js[js.index("card.style.display = 'none'"):]
    hide_block = hide_block[:hide_block.index("}", hide_block.index("video.load()")) + 1]
    assert "video.pause()" in hide_block
    assert 'video.removeAttribute' in hide_block
    assert "video.load()" in hide_block


def test_dblclick_guarded_by_scroll_suppression():
    js = _read("partials/player.js")
    dbl = js[js.index("feed.addEventListener('dblclick'"):]
    dbl = dbl[: dbl.index("});") + 3]
    assert "isTouchSwiping" in dbl
    assert "lastScrollTime" in dbl
