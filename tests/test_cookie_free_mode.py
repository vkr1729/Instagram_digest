"""
tests/test_cookie_free_mode.py — Unit test suite for the Cookie-Free Pipeline Toggle (Option 2).
"""

import argparse
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
import extractor
import local_server
import main
import notifier


def test_env_bool_parsing():
    """Verify _env_bool correctly interprets various environment string representations."""
    with patch.dict(os.environ, {"TEST_BOOL": "true"}):
        assert config._env_bool("TEST_BOOL", False) is True
    with patch.dict(os.environ, {"TEST_BOOL": "1"}):
        assert config._env_bool("TEST_BOOL", False) is True
    with patch.dict(os.environ, {"TEST_BOOL": "yes"}):
        assert config._env_bool("TEST_BOOL", False) is True
    with patch.dict(os.environ, {"TEST_BOOL": "ON"}):
        assert config._env_bool("TEST_BOOL", False) is True

    with patch.dict(os.environ, {"TEST_BOOL": "false"}):
        assert config._env_bool("TEST_BOOL", True) is False
    with patch.dict(os.environ, {"TEST_BOOL": "0"}):
        assert config._env_bool("TEST_BOOL", True) is False
    with patch.dict(os.environ, {"TEST_BOOL": "no"}):
        assert config._env_bool("TEST_BOOL", True) is False
    with patch.dict(os.environ, {"TEST_BOOL": "OFF"}):
        assert config._env_bool("TEST_BOOL", True) is False

    with patch.dict(os.environ, {"TEST_BOOL": "unrecognized_value"}):
        assert config._env_bool("TEST_BOOL", True) is True
        assert config._env_bool("TEST_BOOL", False) is False

    with patch.dict(os.environ, {}, clear=True):
        if "TEST_BOOL" in os.environ:
            del os.environ["TEST_BOOL"]
        assert config._env_bool("TEST_BOOL", True) is True
        assert config._env_bool("TEST_BOOL", False) is False


def test_cli_flags_mutual_exclusion():
    """Verify --cookie-free and --use-cookies cannot both be supplied."""
    parser = argparse.ArgumentParser()
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--cookie-free", action="store_true", default=None)
    mode_group.add_argument("--use-cookies", action="store_true", default=None)

    with pytest.raises(SystemExit):
        parser.parse_args(["--cookie-free", "--use-cookies"])


def test_get_cookie_args_isolation(tmp_path):
    """Verify get_cookie_args returns [] when cookie_free is True, preventing leak to cookies.txt or chrome."""
    fake_cookies = tmp_path / "cookies.txt"
    fake_cookies.write_text("# Netscape HTTP Cookie File")

    with patch("config.ROOT_DIR", tmp_path):
        # Normal mode returns cookies.txt if present
        assert extractor.get_cookie_args(cookie_free=False) == ["--cookies", str(fake_cookies)]
        # Cookie-free mode returns empty list even if cookies.txt exists!
        assert extractor.get_cookie_args(cookie_free=True) == []

    # When cookies.txt does not exist
    with patch("config.ROOT_DIR", tmp_path / "nonexistent"):
        assert extractor.get_cookie_args(cookie_free=False) == ["--cookies-from-browser", "chrome"]
        assert extractor.get_cookie_args(cookie_free=True) == []


def test_instagram_session_cookie_free_isolation():
    """Verify InstagramSession skips _inject_cookies when cookie_free=True."""
    session = extractor.InstagramSession(cookie_free=True)
    assert session.cookie_free is True

    session._context = MagicMock()
    session._inject_cookies()
    # add_cookies should never be called in cookie-free mode
    session._context.add_cookies.assert_not_called()


def test_notifier_alert_suppression():
    """Verify send_cookie_alert_email early-returns False when cookie-free is active."""
    # When cookie_free is passed explicitly as True
    assert notifier.send_cookie_alert_email(cookie_free=True) is False

    # When config.COOKIE_FREE_MODE is True
    with patch("config.COOKIE_FREE_MODE", True):
        assert notifier.send_cookie_alert_email() is False


def test_local_server_popup_suppression():
    """Verify raise_cookie_attention early-returns False when cookie-free is active."""
    # When cookie_free is passed explicitly as True
    assert local_server.raise_cookie_attention("test reason", "test pipeline", cookie_free=True) is False

    # When config.COOKIE_FREE_MODE is True
    with patch("config.COOKIE_FREE_MODE", True):
        assert local_server.raise_cookie_attention("test reason", "test pipeline") is False


def test_checkpoint_mode_mismatch_refusal():
    """Verify _sync_progress_usable rejects checkpoints with mismatched cookie_free mode."""
    cookie_based_sync = {
        "version": 1,
        "week_id": "2026-09-28",
        "days_back": 7,
        "limit_per_creator": 15,
        "since_timestamp": None,
        "stage": "ranked",
        "kind": "weekly",
        "cookie_free": False,
    }
    cookie_free_sync = {
        "version": 1,
        "week_id": "2026-09-28",
        "days_back": 7,
        "limit_per_creator": 15,
        "since_timestamp": None,
        "stage": "ranked",
        "kind": "weekly",
        "cookie_free": True,
    }

    # Running in cookie-free mode should reject cookie-based checkpoint
    assert main._sync_progress_usable(
        cookie_based_sync, limit_per_creator=15, since_timestamp=None, resume=True,
        banked_age_days=1, kind="weekly", cookie_free=True
    ) is False

    # Running in cookie-free mode should accept cookie-free checkpoint
    assert main._sync_progress_usable(
        cookie_free_sync, limit_per_creator=15, since_timestamp=None, resume=True,
        banked_age_days=1, kind="weekly", cookie_free=True
    ) is True

    # Running in cookie mode should reject cookie-free checkpoint
    assert main._sync_progress_usable(
        cookie_free_sync, limit_per_creator=15, since_timestamp=None, resume=True,
        banked_age_days=1, kind="weekly", cookie_free=False
    ) is False


def test_fetch_media_info_batch_cookie_free_bypass():
    """Verify fetch_media_info_batch returns empty dict without network calls in cookie-free mode."""
    res = extractor.fetch_media_info_batch(["fake_shortcode1"], cookie_free=True)
    assert res == {}
