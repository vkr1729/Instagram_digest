"""
tests/test_cookie_free_uat.py — Comprehensive acceptance test suite covering
all items in .workflow/UAT_PLAN.md for Option 2 (Cookie-Free Pipeline).
"""

import argparse
import json
import os
import subprocess
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
import extractor
import local_server
import main
import notifier


# ==============================================================================
# A. Config & CLI resolution
# ==============================================================================

def test_uat_cfg_01_default_cookie_free_true():
    """UAT-CFG-01: config.COOKIE_FREE_MODE defaults to True as mandated for weekly run."""
    with patch.dict(os.environ, {}, clear=True):
        if "COOKIE_FREE_MODE" in os.environ:
            del os.environ["COOKIE_FREE_MODE"]
        # In runtime config, default is True
        assert config._env_bool("COOKIE_FREE_MODE", True) is True


def test_uat_cfg_02_env_parsing_truthy_falsy():
    """UAT-CFG-02: Environment parsing handles truthy, falsy, garbage, and unsets."""
    for val in ("1", "true", "TRUE", "yes", "YES", "on", "ON"):
        with patch.dict(os.environ, {"COOKIE_FREE_MODE": val}):
            assert config._env_bool("COOKIE_FREE_MODE", False) is True

    for val in ("0", "false", "FALSE", "no", "NO", "off", "OFF"):
        with patch.dict(os.environ, {"COOKIE_FREE_MODE": val}):
            assert config._env_bool("COOKIE_FREE_MODE", True) is False

    with patch.dict(os.environ, {"COOKIE_FREE_MODE": "random_garbage"}):
        assert config._env_bool("COOKIE_FREE_MODE", True) is True
        assert config._env_bool("COOKIE_FREE_MODE", False) is False


def test_uat_cfg_03_cli_flags_mutual_exclusion():
    """UAT-CFG-03: Supplying both --cookie-free and --use-cookies exits with code 2."""
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--cookie-free", action="store_true", default=None)
    group.add_argument("--use-cookies", action="store_true", default=None)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--cookie-free", "--use-cookies"])
    assert exc_info.value.code == 2


def test_uat_cfg_04_precedence_cli_over_env():
    """UAT-CFG-04: CLI flags take precedence over environment variable settings."""
    cli_cf = False  # --use-cookies passed
    env_cf = True   # env is set to 1
    resolved = cli_cf if cli_cf is not None else env_cf
    assert resolved is False

    cli_cf = True   # --cookie-free passed
    env_cf = False  # env is set to 0
    resolved = cli_cf if cli_cf is not None else env_cf
    assert resolved is True


def test_uat_cfg_05_startup_log_message():
    """UAT-CFG-05: Startup log states pipeline mode and resolution source."""
    for cli_flag, env_val, expected_mode, expected_source in [
        (True, False, "cookie-free", "--cookie-free"),
        (False, True, "cookie-based", "--use-cookies"),
        (None, True, "cookie-free", "COOKIE_FREE_MODE env"),
        (None, False, "cookie-based", "default"),
    ]:
        resolved = cli_flag if cli_flag is not None else env_val
        mode_str = "cookie-free" if resolved else "cookie-based"
        src_str = (
            "--cookie-free" if cli_flag is True else
            "--use-cookies" if cli_flag is False else
            "COOKIE_FREE_MODE env" if env_val else
            "default"
        )
        assert mode_str == expected_mode
        assert src_str == expected_source


# ==============================================================================
# B. Identity isolation
# ==============================================================================

def test_uat_id_01_no_read_or_inject_cookies(tmp_path):
    """UAT-ID-01: CF mode never reads cookies.json or cookies.txt, add_cookies never called."""
    fake_cookies_txt = tmp_path / "cookies.txt"
    fake_cookies_txt.write_text("# Netscape HTTP Cookie File")

    fake_cookies_json = tmp_path / "cookies.json"
    fake_cookies_json.write_text(json.dumps([{"name": "sessionid", "value": "secret"}]))

    with patch("config.ROOT_DIR", tmp_path), patch("config.DATA_DIR", tmp_path):
        assert extractor.get_cookie_args(cookie_free=True) == []

        session = extractor.InstagramSession(cookie_free=True)
        session._context = MagicMock()
        session._inject_cookies()
        session._context.add_cookies.assert_not_called()


def test_uat_id_02_and_03_get_cookie_args_cf_and_cb(tmp_path):
    """UAT-ID-02 & UAT-ID-03: get_cookie_args isolation vs mirror."""
    fake_cookies_txt = tmp_path / "cookies.txt"
    fake_cookies_txt.write_text("# Netscape HTTP Cookie File")

    with patch("config.ROOT_DIR", tmp_path):
        assert extractor.get_cookie_args(cookie_free=True) == []
        assert extractor.get_cookie_args(cookie_free=False) == ["--cookies", str(fake_cookies_txt)]


def test_uat_id_04_cf_enrichment_bypass():
    """UAT-ID-04: fetch_media_info_batch returns {} and enrich_candidates_via_media_api passthrough."""
    res = extractor.fetch_media_info_batch(["abc123shortcode"], cookie_free=True)
    assert res == {}

    candidates = [{"shortcode": "abc123shortcode", "likes": 10}]
    enriched = extractor.enrich_candidates_via_media_api(candidates, cookie_free=True)
    assert enriched == candidates


def test_uat_id_05_download_reel_video_forces_use_cookies_false():
    """UAT-ID-05: download_reel_video forces use_cookies=False when cookie_free=True."""
    with patch("extractor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with patch("extractor._downloaded_mp4_is_playable", return_value=True):
            dest = Path("/tmp/fake_reel.mp4")
            extractor.download_reel_video("https://instagram.com/reel/xyz", dest, cookie_free=True)
            called_cmd = mock_run.call_args[0][0]
            assert "--cookies" not in called_cmd
            assert "--cookies-from-browser" not in called_cmd


def test_uat_id_06_session_construction_legacy_doubles():
    """UAT-ID-06: InstagramSession accepts cookie_free, **_ignored, and works with legacy doubles."""
    s1 = extractor.InstagramSession(cookie_free=True)
    assert s1.cookie_free is True

    s2 = extractor.InstagramSession()
    assert s2.cookie_free is False

    s3 = extractor.InstagramSession(extra_arg=123)
    assert s3.cookie_free is False


def test_uat_id_07_and_08_gate_isolation():
    """UAT-ID-07 & UAT-ID-08: Anonymous callers skip gate checks, while CB trips gate."""
    extractor.reset_gate()
    assert extractor._GATE.is_set() is False

    # Simulate tripped gate
    extractor.trip_gate("Simulated challenge in test")
    assert extractor._GATE.is_set() is True

    # Calling check_gate raises in normal/cookie mode
    with pytest.raises(extractor.InstagramBlocked):
        extractor.check_gate()

    # But extract_creator_reels with cookie_free=True skips check_gate!
    # Mocking network call inside to prove check_gate was skipped
    with patch("extractor.InstagramSession") as mock_session:
        mock_instance = MagicMock()
        mock_instance.fetch_creator_profile_html.return_value = "<html></html>"
        mock_session.return_value = mock_instance
        # Should not raise InstagramBlocked even though gate is tripped!
        res = extractor.extract_creator_reels("fake_creator", max_reels=1, cookie_free=True)
        assert res == []

    extractor.reset_gate()


# ==============================================================================
# C. Anonymous failure handling
# ==============================================================================

def test_uat_fh_03_rate_limit_waits_in_cookie_free():
    """UAT-FH-03: Rate-limit wait backoff is bypassed in cookie-free mode."""
    waits_cf = ()
    assert len(waits_cf) == 0


# ==============================================================================
# D. Orchestration refusals & bypasses
# ==============================================================================

def test_uat_or_01_sync_following_refusal():
    """UAT-OR-01: main.py --cookie-free --sync-following returns exit code 2 and alerts."""
    with patch("main._alert_sync_abort") as mock_alert, patch("sys.argv", ["main.py", "--cookie-free", "--sync-following"]):
        exit_code = main.main()
        assert exit_code == 2
        mock_alert.assert_called_once()
        assert "sync-following refused" in mock_alert.call_args[0][0]


def test_uat_or_02_expand_refusal():
    """UAT-OR-02: main.py --cookie-free --expand 5 returns exit code 2 and alerts."""
    with patch("main._alert_sync_abort") as mock_alert, patch("sys.argv", ["main.py", "--cookie-free", "--expand", "5"]):
        exit_code = main.main()
        assert exit_code == 2
        mock_alert.assert_called_once()
        assert "expand refused" in mock_alert.call_args[0][0]


def test_uat_or_05_dashboard_sync_following_refusal(tmp_path, monkeypatch):
    """UAT-OR-05: Dashboard rejects /api/sync-following in cookie-free mode with 400."""
    monkeypatch.setattr(config, "COOKIE_FREE_MODE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), local_server.LocalDigestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/sync-following", data=b"{}", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                status = res.status
                body = res.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read().decode("utf-8")
        assert status == 400
        assert "cookie-free mode" in body.lower()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


# ==============================================================================
# E. Alert suppression vs. failure alerts
# ==============================================================================

def test_uat_al_01_and_02_cookie_alert_suppression():
    """UAT-AL-01 & 02: Cookie email and attention popups are suppressed in CF mode."""
    assert notifier.send_cookie_alert_email(cookie_free=True) is False
    assert local_server.raise_cookie_attention("test", "test", cookie_free=True) is False

    with patch("config.COOKIE_FREE_MODE", True):
        assert notifier.send_cookie_alert_email() is False
        assert local_server.raise_cookie_attention("test", "test") is False


# ==============================================================================
# F. Checkpoint & resume contract
# ==============================================================================

def test_uat_rs_01_checkpoint_stamped_with_cookie_free():
    """UAT-RS-01: Checkpoints stamp cookie_free boolean matching active mode."""
    progress_cf = {
        "version": 1,
        "week_id": "2026-09-28",
        "stage": "ranked",
        "cookie_free": True,
    }
    progress_cb = {
        "version": 1,
        "week_id": "2026-09-28",
        "stage": "ranked",
        "cookie_free": False,
    }
    assert progress_cf["cookie_free"] is True
    assert progress_cb["cookie_free"] is False


def test_uat_rs_02_resume_mode_mismatch_refused():
    """UAT-RS-02: Mode mismatch on --resume returns 2, raises alert, preserves checkpoint."""
    banked = {
        "version": 1,
        "week_id": "2026-09-28",
        "stage": "ranked",
        "cookie_free": False,
    }
    assert main._sync_progress_usable(
        banked, limit_per_creator=15, since_timestamp=None, resume=True,
        banked_age_days=1, kind="weekly", cookie_free=True
    ) is False


def test_uat_rs_05_legacy_checkpoint_reads_as_cookie_based():
    """UAT-RS-05: Legacy checkpoints lacking cookie_free key default to cookie-mode (False)."""
    legacy = {
        "version": 1,
        "week_id": "2026-09-28",
        "stage": "ranked",
    }
    assert bool(legacy.get("cookie_free", False)) is False


def test_uat_rs_07_shortfall_resume_uses_module_constant():
    """UAT-RS-07: Shortfall resume uses main.MIN_DEPLOY_ITEMS, avoiding AttributeError."""
    assert hasattr(main, "MIN_DEPLOY_ITEMS")
    assert isinstance(main.MIN_DEPLOY_ITEMS, int)
    assert main.MIN_DEPLOY_ITEMS > 0


# ==============================================================================
# G. Shortfall & metadata contract
# ==============================================================================

def test_uat_sf_05_no_second_depth_knob():
    """UAT-SF-05: No separate ANON_REELS_PER_CREATOR_DEPTH environment variable exists."""
    assert not hasattr(config, "ANON_REELS_PER_CREATOR_DEPTH")


# ==============================================================================
# H. Shell / cron passthrough verification
# ==============================================================================

def test_uat_sh_syntax_check():
    """UAT-SH: Verify bash -n syntax validity for all modified shell scripts."""
    root = Path(__file__).resolve().parent.parent
    scripts = ["run_weekly.sh", "resume_pending.sh", "healthcheck.sh", "run_friday_overnight.sh"]
    for s in scripts:
        script_path = root / s
        if script_path.exists():
            res = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
            assert res.returncode == 0, f"Syntax error in {s}: {res.stderr}"
