import os
import sys
from pathlib import Path
import pytest

# Neutralize operator secrets immediately so module-level fixtures never inherit live PIN
os.environ["VIEWING_PIN"] = ""

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture(autouse=True, scope="session")
def _isolate_session_env():
    """Ensure session-scoped and module-scoped fixtures run without operator secrets."""
    prev = os.environ.get("VIEWING_PIN")
    os.environ["VIEWING_PIN"] = ""
    yield
    if prev is not None:
        os.environ["VIEWING_PIN"] = prev
    else:
        os.environ.pop("VIEWING_PIN", None)


@pytest.fixture(autouse=True)
def _isolate_test_environment(monkeypatch):
    """Ensure function-scoped test runs never inherit operator secrets."""
    monkeypatch.setenv("VIEWING_PIN", "")

