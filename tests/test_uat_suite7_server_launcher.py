"""
test_uat_suite7_server_launcher.py — Automated tests for Suite 7: Local Server & Desktop Launcher.
"""

from pathlib import Path
import pytest
import config
from local_server import LocalDigestHandler


def test_uat_7_3_desktop_launcher_file():
    """UAT-7.3: Desktop Launcher File validation."""
    desktop_file = Path.home() / ".local" / "share" / "applications" / "InstagramDigest.desktop"
    assert desktop_file.exists(), f"Desktop file not found at {desktop_file}"

    content = desktop_file.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in content
    assert "Name=Instagram Digest" in content
    assert "Exec=" in content
    assert "Icon=" in content
    assert "Terminal=false" in content

    # Check that icon exists
    icon_path = config.ROOT_DIR / "assets" / "icon.svg"
    assert icon_path.exists()
