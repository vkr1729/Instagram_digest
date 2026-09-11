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
import extractor
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


class _FakeSession:
    """Stand-in for InstagramSession recording validate/close/start calls."""

    def __init__(self, results):
        self._results = list(results)
        self.validations = 0
        self.closed = 0
        self.started = 0

    def validate(self):
        self.validations += 1
        outcome = self._results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed += 1

    def start(self):
        self.started += 1


def _mock_page(url="https://www.instagram.com/", fail_nav=False):
    page = MagicMock()
    page.url = url
    if fail_nav:
        page.goto.side_effect = RuntimeError("net down")
    return page


def test_session_validate_accepts_clean_session():
    sess = extractor.InstagramSession()
    sess._page = _mock_page()
    assert sess.validate() is True


def test_session_validate_rejects_login_wall():
    sess = extractor.InstagramSession()
    sess._page = _mock_page(url="https://www.instagram.com/accounts/login/?next=/")
    assert sess.validate() is False


def test_session_validate_never_raises():
    sess = extractor.InstagramSession()
    sess._page = _mock_page(fail_nav=True)
    assert sess.validate() is False
    sess._page = None
    assert sess.validate() is False


def test_ensure_valid_session_skips_refresh_when_healthy(monkeypatch):
    monkeypatch.setattr(cookie_exporter, "export_instagram_cookies",
                        lambda: (_ for _ in ()).throw(AssertionError("must not refresh")))
    sess = _FakeSession([True])
    assert main._ensure_valid_session(sess) is True
    assert sess.validations == 1
    assert sess.closed == 0


def test_ensure_valid_session_refreshes_once_and_heals(monkeypatch):
    exports = []
    monkeypatch.setattr(cookie_exporter, "export_instagram_cookies",
                        lambda: exports.append(1) or {"sessionid": "fresh"})
    sess = _FakeSession([False, True])
    assert main._ensure_valid_session(sess) is True
    assert exports == [1]
    assert (sess.validations, sess.closed, sess.started) == (2, 1, 1)


def test_ensure_valid_session_gives_up_after_one_retry(monkeypatch):
    exports = []
    monkeypatch.setattr(cookie_exporter, "export_instagram_cookies",
                        lambda: exports.append(1) or {})
    sess = _FakeSession([False, False])
    assert main._ensure_valid_session(sess) is False
    assert exports == [1]
    assert sess.validations == 2


def test_ensure_valid_session_survives_broken_helpers(monkeypatch):
    def _boom():
        raise RuntimeError("dbus down")
    monkeypatch.setattr(cookie_exporter, "export_instagram_cookies", _boom)
    assert main._ensure_valid_session(_FakeSession([False])) is False
    # A raising validate() still routes into the single refresh path.
    exports = []
    monkeypatch.setattr(cookie_exporter, "export_instagram_cookies",
                        lambda: exports.append(1) or {})
    assert main._ensure_valid_session(_FakeSession([RuntimeError("x"), True])) is True
    assert exports == [1]
