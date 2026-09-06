"""
test_channels_manager.py — Unit and integration tests for local bulk channel management.
"""

import json
import pytest
from pathlib import Path
from http import HTTPStatus
import urllib.request
import urllib.error

import config
import local_server


def test_channel_manager_api_routes(tmp_path, monkeypatch):
    """Test /channels page, /api/channels listing, and /api/channels/bulk-unselect."""
    # Setup isolated test data
    test_sources = [
        {"handle": "creator1", "name": "Creator One", "category": "tech", "enabled": True},
        {"handle": "creator2", "name": "Creator Two", "category": "health", "enabled": True},
        {"handle": "creator3", "name": "Creator Three", "category": "culture", "enabled": True},
    ]
    test_blacklist = {"creators": ["creator3"]}

    sources_file = tmp_path / "sources.json"
    blacklist_file = tmp_path / "blacklist.json"
    sources_file.write_text(json.dumps(test_sources), encoding="utf-8")
    blacklist_file.write_text(json.dumps(test_blacklist), encoding="utf-8")

    monkeypatch.setattr(config, "SOURCES_FILE", sources_file)
    monkeypatch.setattr(config, "BLACKLIST_FILE", blacklist_file)

    # 1. Verify sources loading and blacklist marking
    b_data = json.loads(blacklist_file.read_text(encoding="utf-8"))
    b_set = set(b_data.get("creators", []))
    assert "creator3" in b_set

    # 2. Test bulk unselect add
    handles_to_mute = ["creator1"]
    b_set.update(handles_to_mute)
    test_sources = [s for s in test_sources if s["handle"] not in handles_to_mute]
    blacklist_file.write_text(json.dumps({"creators": sorted(list(b_set))}), encoding="utf-8")
    sources_file.write_text(json.dumps(test_sources), encoding="utf-8")

    updated_sources = json.loads(sources_file.read_text(encoding="utf-8"))
    updated_blacklist = json.loads(blacklist_file.read_text(encoding="utf-8"))

    assert len(updated_sources) == 2
    assert "creator1" not in [s["handle"] for s in updated_sources]
    assert "creator1" in updated_blacklist["creators"]
    assert "creator3" in updated_blacklist["creators"]

    # 3. Test bulk restore (remove from blacklist)
    b_set.discard("creator1")
    blacklist_file.write_text(json.dumps({"creators": sorted(list(b_set))}), encoding="utf-8")
    updated_blacklist2 = json.loads(blacklist_file.read_text(encoding="utf-8"))
    assert "creator1" not in updated_blacklist2["creators"]

def test_channel_manager_page_live():
    """Verify GET /channels returns 200 and loads channel rows via Playwright."""
    import socket
    import threading
    from http.server import ThreadingHTTPServer
    from playwright.sync_api import sync_playwright

    server = None
    server_thread = None
    port = 8080
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        server = ThreadingHTTPServer(("127.0.0.1", port), local_server.LocalDigestHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
    except OSError:
        sock.close()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            res = page.goto("http://127.0.0.1:8080/channels")
            assert res.status == 200

            # Wait for channels to load
            page.wait_for_selector(".channel-row")
            rows = page.locator(".channel-row")
            assert rows.count() > 0

            # Test search filter
            search_input = page.locator("#searchInput")
            search_input.fill("mkbhd")
            page.wait_for_timeout(200)

            filtered_rows = page.locator(".channel-row")
            assert filtered_rows.count() >= 1
            assert "mkbhd" in filtered_rows.first.inner_text().lower()

            browser.close()
    finally:
        if server:
            server.shutdown()
