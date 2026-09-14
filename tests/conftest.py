import os
import sys
from pathlib import Path
import pytest

# Neutralize operator secrets immediately so module-level fixtures never inherit live PIN or SMTP
os.environ["VIEWING_PIN"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASS"] = ""
os.environ["NOTIFICATION_EMAIL"] = ""

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
import notifier


@pytest.fixture(autouse=True, scope="session")
def _isolate_session_env():
    """Ensure session-scoped and module-scoped fixtures run without operator secrets."""
    prev_pin = os.environ.get("VIEWING_PIN")
    prev_smtp_user = os.environ.get("SMTP_USER")
    prev_smtp_pass = os.environ.get("SMTP_PASS")
    prev_notif_email = os.environ.get("NOTIFICATION_EMAIL")

    os.environ["VIEWING_PIN"] = ""
    os.environ["SMTP_USER"] = ""
    os.environ["SMTP_PASS"] = ""
    os.environ["NOTIFICATION_EMAIL"] = ""

    config.SMTP_USER = ""
    config.SMTP_PASS = ""
    config.NOTIFICATION_EMAIL = ""

    yield

    if prev_pin is not None:
        os.environ["VIEWING_PIN"] = prev_pin
    else:
        os.environ.pop("VIEWING_PIN", None)
    if prev_smtp_user is not None:
        os.environ["SMTP_USER"] = prev_smtp_user
    else:
        os.environ.pop("SMTP_USER", None)
    if prev_smtp_pass is not None:
        os.environ["SMTP_PASS"] = prev_smtp_pass
    else:
        os.environ.pop("SMTP_PASS", None)
    if prev_notif_email is not None:
        os.environ["NOTIFICATION_EMAIL"] = prev_notif_email
    else:
        os.environ.pop("NOTIFICATION_EMAIL", None)


@pytest.fixture(autouse=True)
def _isolate_test_environment(monkeypatch, request):
    """Ensure function-scoped test runs never inherit operator secrets or send live emails."""
    monkeypatch.setenv("VIEWING_PIN", "")
    monkeypatch.setattr(config, "SMTP_USER", "")
    monkeypatch.setattr(config, "SMTP_PASS", "")
    monkeypatch.setattr(config, "NOTIFICATION_EMAIL", "")

    # For any test outside the dedicated email unit tests, mock notifier methods
    # to guarantee zero network traffic or external side effects.
    mod_name = request.module.__name__ if hasattr(request, "module") and request.module else ""
    if "test_notifier" not in mod_name and "test_failure_alerts" not in mod_name:
        monkeypatch.setattr(notifier, "send_digest_email", lambda *a, **kw: False)
        monkeypatch.setattr(notifier, "send_cookie_alert_email", lambda *a, **kw: False)
        monkeypatch.setattr(notifier, "send_failure_alert_email", lambda *a, **kw: False)

