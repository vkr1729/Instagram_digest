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


def test_build_email_message_with_breakdown(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "owner@example.com")

    msg = notifier.build_email_message(week_id="2026-09-11", count=250, followed_count=180, external_count=70)
    payloads = [part.get_payload(decode=True).decode("utf-8") for part in msg.get_payload()]
    text_body, html_body = payloads[0], payloads[1]

    assert "Followed Channels: 180" in text_body
    assert "External Discovery: 70" in text_body
    assert "180" in html_body
    assert "70" in html_body
    assert "External" in html_body


def test_cookie_alert_email(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(config, "SMTP_PASS", "pass")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "owner@example.com")

    msg = notifier.build_cookie_alert_message("http://localhost:8080/retrigger")
    assert "Action Required" in msg["Subject"]
    payloads = [part.get_payload(decode=True).decode("utf-8") for part in msg.get_payload()]
    assert "http://localhost:8080/retrigger" in payloads[0]
    assert "http://localhost:8080/retrigger" in payloads[1]

    mock_smtp_instance = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        mock_smtp_instance.__enter__.return_value = mock_smtp_instance
        res = notifier.send_cookie_alert_email("http://localhost:8080/retrigger")
        assert res is True


def test_health_report_message_structure(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "owner@example.com")
    report = {
        "week_id": "2026-09-19", "healthy": True, "exit_code": 0,
        "run_summary": "sync ok", "digest_count": 250, "digest_target": 250,
        "digest_ok": True, "r2_gb": "3.19", "quota_gb": "8", "r2_ok": True,
        "pages_status": "HTTP 200", "pages_ok": True,
        "session_status": "sessionid present", "session_ok": True,
        "outbox_pending": 0, "resume_pending": "none", "notes": [],
    }
    msg = notifier.build_health_report_message(report)
    assert "2026-09-19" in msg["Subject"]
    payloads = [part.get_payload(decode=True).decode("utf-8") for part in msg.get_payload()]
    assert "250 / 250" in payloads[1]
    assert "All checks green" in payloads[1]


def test_health_report_unhealthy_subject(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "owner@example.com")
    report = {"week_id": "2026-09-19", "healthy": False, "digest_count": 100,
              "digest_target": 250, "notes": ["shortfall"]}
    msg = notifier.build_health_report_message(report)
    assert msg["Subject"].startswith("⚠️")


def test_collect_health_report_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(config, "PAGES_BASE_URL", "http://127.0.0.1:9/")
    report = notifier.collect_health_report(week_id="2026-09-19", exit_code=1)
    assert report["healthy"] is False
    assert report["digest_ok"] is False
    assert report["pages_ok"] is False


def test_send_health_report_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "")
    assert notifier.send_health_report_email({}) is False


def test_send_health_report_success(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "user@example.com")
    monkeypatch.setattr(config, "SMTP_PASS", "secret")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "owner@example.com")
    mock_smtp_instance = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        mock_smtp_instance.__enter__.return_value = mock_smtp_instance
        assert notifier.send_health_report_email({"week_id": "w"}) is True


def test_send_digest_email_forwards_recommended_and_target(monkeypatch):
    """P0-1 wiring: the weekly send path must pass recommendations + target."""
    import inspect
    params = inspect.signature(notifier.send_digest_email).parameters
    assert "recommended" in params and "target" in params
    monkeypatch.setattr(config, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(config, "SMTP_PASS", "pass")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "owner@example.com")
    seen = {}

    def _spy(**kwargs):
        seen.update(kwargs)
        import email.message
        m = email.message.Message()
        m["Subject"] = "x"
        return m

    monkeypatch.setattr(notifier, "build_email_message", _spy)
    mock_smtp_instance = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        mock_smtp_instance.__enter__.return_value = mock_smtp_instance
        recs = [{"handle": "a", "name": "A", "reason": "r"}]
        assert notifier.send_digest_email(
            "2026-09-20", count=10, recommended=recs, target=250) is True
    assert seen["recommended"] == recs and seen["target"] == 250
