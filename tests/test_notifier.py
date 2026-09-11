"""
test_notifier.py — Unit tests for the email notification service.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import config
import notifier


def test_is_email_configured(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "user@example.com")
    monkeypatch.setattr(config, "SMTP_PASS", "secret")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "recipient@example.com")
    assert notifier.is_email_configured() is True

    monkeypatch.setattr(config, "SMTP_USER", "")
    assert notifier.is_email_configured() is False


def test_build_email_message(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "owner@example.com")
    monkeypatch.setattr(config, "PAGES_BASE_URL", "https://example.com/digest")

    top_reels = [
        {
            "rank": 1,
            "rank_display": "#01",
            "creator_handle": "alice",
            "category": "ai_tech",
            "caption": "Breakthrough in AI models & systems <script>alert(1)</script>",
        }
    ]

    msg = notifier.build_email_message(week_id="2026-09-11", count=42, top_reels=top_reels)
    assert "2026-09-11" in msg["Subject"]
    assert "42 Reels" in msg["Subject"]
    assert msg["From"] == "Instagram Digest <bot@example.com>"
    assert msg["To"] == "owner@example.com"

    payloads = [part.get_payload(decode=True).decode("utf-8") for part in msg.get_payload()]
    text_body, html_body = payloads[0], payloads[1]

    assert "42 high-signal reels" in text_body
    assert "@alice" in text_body
    assert "<script>" not in html_body  # Ensure HTML escaping
    assert "&lt;script&gt;" in html_body
    assert "https://example.com/digest" in html_body


def test_send_digest_email_success(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "user@example.com")
    monkeypatch.setattr(config, "SMTP_PASS", "pass")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "dest@example.com")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_PORT", 587)

    mock_smtp_instance = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp_instance) as mock_smtp_cls:
        mock_smtp_instance.__enter__.return_value = mock_smtp_instance

        res = notifier.send_digest_email(week_id="2026-09-11", count=200)
        assert res is True
        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=25)
        mock_smtp_instance.starttls.assert_called_once()
        mock_smtp_instance.login.assert_called_once_with("user@example.com", "pass")
        mock_smtp_instance.send_message.assert_called_once()


def test_send_digest_email_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "")
    res = notifier.send_digest_email(week_id="2026-09-11", count=200)
    assert res is False


def test_send_digest_email_handles_exception(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "user@example.com")
    monkeypatch.setattr(config, "SMTP_PASS", "pass")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "dest@example.com")

    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("Connection refused")):
        res = notifier.send_digest_email(week_id="2026-09-11", count=200)
        assert res is False
