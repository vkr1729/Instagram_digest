"""Replay-from-end regression: a fully-watched reel must restart from zero
when revisited (bookmark/share return path), never strand on the last frame.

Covers both stacks with static markers (fast, deterministic, Linux-safe):
- iOS AVPlayerPool: end-detection helper + seek-to-zero restart on every
  play path (rotation promotions, play(), same-slot resume) + live clock
  updates while duration metadata resolves.
- PWA player.js: explicit currentTime reset when video.ended in playCardVideo
  (some WebKit builds do not auto-rewind play() after ended).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POOL = REPO / "Sources" / "InstagramDigest" / "Engine" / "AVPlayerPool.swift"
PLAYER_JS = REPO / "templates" / "partials" / "player.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_pool_has_end_detection_helper():
    src = _read(POOL)
    assert "func isAtEnd(currentTime:" in src
    assert "endRestartThreshold" in src


def test_pool_restart_helper_seeks_to_zero():
    src = _read(POOL)
    assert "playCurrentSlotRestartingIfNeeded" in src
    assert "player.seek(to: .zero" in src


def test_pool_play_paths_use_restart_helper():
    src = _read(POOL)
    # Every path that starts playback must go through the restart helper:
    # forward promotion, backward promotion, same-slot resume, play().
    assert src.count("playCurrentSlotRestartingIfNeeded()") >= 4
    assert "public func play() {\n        playCurrentSlotRestartingIfNeeded()" in src


def test_pool_time_observer_keeps_clock_live():
    src = _read(POOL)
    block = src[src.index("private func attachTimeObserver"):]
    cur = block.index("self.currentTime = cur")
    dur_guard = block.index("guard let duration")
    assert cur < dur_guard, "clock must publish before the duration guard"


def test_pwa_resets_ended_video_before_play():
    js = _read(PLAYER_JS)
    assert "if (video.ended)" in js
    reset = js[js.index("if (video.ended)"):]
    assert "video.currentTime = 0" in reset[:400]
    assert "endedHandled" in reset[:400]
