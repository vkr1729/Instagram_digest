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
        monkeypatch.setattr(notifier, "send_health_report_email", lambda *a, **kw: False)


@pytest.fixture(autouse=True)
def _isolate_state_paths(tmp_path, monkeypatch):
    """No test may touch the live data/ dir: a planted expand checkpoint makes
    resume_pending.sh run --expand --deploy at the next login."""
    import main
    import local_server
    import extractor
    data = tmp_path / "state"
    (data / "digests").mkdir(parents=True)
    (data / "videos").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DIGESTS_DIR", data / "digests")
    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", data / "top100_digest.json")
    monkeypatch.setattr(config, "LAST_RUN_FILE", data / "last_run.json")
    monkeypatch.setattr(main, "SEEN_IDS_FILE", data / "seen_reel_ids.json")
    monkeypatch.setattr(local_server, "COOKIE_ATTENTION_FILE", data / "cookie_attention.json")
    # Rec #5: the account-safety gate is process-global — never leak a
    # tripped gate from one test into the next.
    extractor.reset_gate()


def _live_data_snapshot():
    """File names under the real ROOT_DIR/data at session start (tripwire baseline)."""
    live = ROOT_DIR / "data"
    try:
        return ({p.name for p in live.glob("expand_checkpoint_*")}
                | {p.name for p in live.glob("sync_progress_*")}
                | {(live / "digests" / p.name).as_posix()
                   for p in (live / "digests").glob("*.json")} if live.exists() else set())
    except Exception:
        return set()


_LIVE_BASELINE = _live_data_snapshot()


def pytest_sessionfinish(session, exitstatus):
    """Tripwire: fail loudly if any test planted state in the live data/ dir."""
    live = ROOT_DIR / "data"
    now_sync = {p.name for p in live.glob("expand_checkpoint_*")} if live.exists() else set()
    now_prog = {p.name for p in live.glob("sync_progress_*")} if live.exists() else set()
    now_dig = set()
    if (live / "digests").exists():
        now_dig = {(live / "digests" / p.name).as_posix()
                   for p in (live / "digests").glob("*.json")}
    new = (now_sync | now_prog | now_dig) - _LIVE_BASELINE
    if new:
        session.exitstatus = 1
        print(f"\nFAIL: tests wrote live state into data/: {sorted(new)}")

