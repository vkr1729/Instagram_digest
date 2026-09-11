"""
test_failure_alerts.py — Regression tests for the silent-pipeline-death and
credential-permission fixes (review findings #1, #2, #6, #7, #8).

Covers, without sending mail or touching Chrome:
- failure-alert message build + send paths (mocked SMTP),
- main._alert_sync_abort delegating to the notifier on every exit-2 site,
- owner-only (0600) cookie writes,
- .env permission check helper,
- run_weekly.sh failure-mail wiring (static marker).
"""

from __future__ import annotations

import inspect
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
import cookie_exporter
import main
import notifier

ROOT_DIR = Path(__file__).resolve().parent.parent


def _smtp_configured(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "user@example.com")
    monkeypatch.setattr(config, "SMTP_PASS", "pass")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "dest@example.com")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_PORT", 587)


def test_build_failure_alert_message_carries_context_and_code(monkeypatch):
    _smtp_configured(monkeypatch)
    msg = notifier.build_failure_alert_message(context="Weekly Friday sync", exit_code=3)
    assert "Weekly Friday sync" in msg["Subject"]
    assert "exit 3" in msg["Subject"]
    assert msg["To"] == "dest@example.com"
    bodies = [p.get_payload(decode=True).decode("utf-8") for p in msg.get_payload()]
    assert any("weekly_sync.log" in b for b in bodies)


def test_send_failure_alert_email_success(monkeypatch):
    _smtp_configured(monkeypatch)
    mock_smtp_instance = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp_instance) as mock_smtp_cls:
        mock_smtp_instance.__enter__.return_value = mock_smtp_instance
        assert notifier.send_failure_alert_email(context="ctx", exit_code=2) is True
        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=25)
        mock_smtp_instance.send_message.assert_called_once()


def test_send_failure_alert_email_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "")
    assert notifier.send_failure_alert_email(context="ctx", exit_code=2) is False


def test_send_failure_alert_email_handles_exception(monkeypatch):
    _smtp_configured(monkeypatch)
    with patch("smtplib.SMTP", side_effect=RuntimeError("down")):
        assert notifier.send_failure_alert_email(context="ctx", exit_code=2) is False


def test_sync_abort_alerts_on_all_exit2_sites(monkeypatch):
    # Every `return 2` inside run_full_sync must be preceded by an abort alert.
    src = inspect.getsource(main.run_full_sync)
    assert src.count("_alert_sync_abort(") >= 3
    assert src.count("return 2") <= src.count("_alert_sync_abort(")


def test_alert_sync_abort_delegates_and_never_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notifier, "send_failure_alert_email",
        lambda context, exit_code=1: calls.append((context, exit_code)) or True,
    )
    main._alert_sync_abort("Instagram session blocked", "boom")
    assert len(calls) == 1
    context, code = calls[0]
    assert "Instagram session blocked" in context
    assert code == 2

    # A broken notifier must not break the abort path itself.
    def _raise(context, exit_code=1):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(notifier, "send_failure_alert_email", _raise)
    main._alert_sync_abort("x", "y")  # must not raise


def test_cookie_writes_are_owner_only(tmp_path):
    target = tmp_path / "cookies.txt"
    cookie_exporter._secure_write_text(target, "secret-material")
    assert target.read_text(encoding="utf-8") == "secret-material"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_env_permission_check(tmp_path):
    assert config.check_env_file_permissions(tmp_path / "missing.env") is True
    loose = tmp_path / "loose.env"
    loose.write_text("K=V", encoding="utf-8")
    loose.chmod(0o644)
    assert config.check_env_file_permissions(loose) is False
    tight = tmp_path / "tight.env"
    tight.write_text("K=V", encoding="utf-8")
    tight.chmod(0o600)
    assert config.check_env_file_permissions(tight) is True


def test_run_weekly_mails_on_failure_and_preserves_exit():
    script = (ROOT_DIR / "run_weekly.sh").read_text(encoding="utf-8")
    assert "notifier.py --failure-alert" in script
    # Alert is sent from the failure branch before the original exit code propagates.
    assert script.index("--failure-alert") < script.index("exit $EXIT_CODE")
