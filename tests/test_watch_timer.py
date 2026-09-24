"""Watch-timer regression: the header pill must accumulate real playback
seconds per week content, restart on any content refresh, and never be
recreated per render.

Field bug: `Timer.publish(...).autoconnect()` was constructed inside the
view `body`. Pool progress ticks (every 0.5s) re-evaluate `body` faster
than the 1s timer interval, so `onReceive` resubscribed on every tick and
the timer never fired -> pill stuck at "0.0 hrs".
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "Sources" / "InstagramDigest" / "InstagramDigestApp.swift"
RULES = REPO / "Sources" / "InstagramDigest" / "Engine" / "WatchedRules.swift"
HEADER = REPO / "Sources" / "InstagramDigest" / "Views" / "Feed" / "HeaderBarView.swift"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_timer_publisher_is_stored_not_built_in_body():
    src = _read(APP)
    assert re.search(r"private let watchTimer = Timer\.publish\(every:", src), \
        "timer must be a stored property so re-renders keep one subscription"
    assert ".onReceive(watchTimer)" in src
    assert "onReceive(Timer.publish" not in src, \
        "inline Timer.publish in body resubscribes on every pool tick"


def test_tick_counts_wall_clock_while_playing():
    src = _read(APP)
    handler = src[src.index(".onReceive(watchTimer)"):][:600]
    assert "pool.isPlaying" in handler
    assert "watchSeconds += 1.0" in handler


def test_refresh_reset_uses_stable_fingerprint():
    rules = _read(RULES)
    assert "watchContentFingerprint" in rules
    assert "shouldResetWatchTime" in rules
    # Swift.Hasher is process-seeded: fingerprints must use a stable hash or
    # the timer wipes on every cold start.
    assert "Hasher()" not in rules
    app = _read(APP)
    assert "watchContentFingerprint(items:" in app
    assert "shouldResetWatchTime(storedFingerprint:" in app
    assert "watchContentFingerprint_" in app


def test_header_pill_contract():
    src = _read(HEADER)
    assert 'accessibilityIdentifier("WatchTimerPill")' in src
    assert "watchHoursText" in src
