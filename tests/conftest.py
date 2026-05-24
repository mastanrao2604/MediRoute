"""Shared pytest fixtures for MediRoute operational regression."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "mediroute-backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from tests.helpers.config import (  # noqa: E402
    DEFAULT_BASE_URL,
    MANIFEST_PATH,
    TEST_SECRET,
)
from tests.helpers.api_client import MediRouteClient  # noqa: E402
from tests.helpers.cleanup import release_nurse_blockers  # noqa: E402

# Test stack must use isolated DB — set before any app import in tests
os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", TEST_SECRET)
os.environ.setdefault("SMS_PROVIDER", "log")
os.environ.setdefault("OTP_FORCE_DEV", "1")


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        pytest.fail(
            f"Seed manifest missing at {MANIFEST_PATH}. Run scripts/reset-test-db.ps1 first."
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _wait_for_stack(base_url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=3.0)
            if r.status_code == 200 and r.json().get("status") == "healthy":
                return
            last_err = f"health status {r.status_code}"
        except Exception as exc:
            last_err = str(exc)
        time.sleep(0.5)
    pytest.fail(f"Test stack not healthy at {base_url} within {timeout}s: {last_err}")


@pytest.fixture(scope="session")
def manifest() -> dict:
    return _load_manifest()


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.getenv("TEST_BASE_URL", DEFAULT_BASE_URL)
    _wait_for_stack(url)
    return url


@pytest.fixture(scope="session")
def recruiter_token(manifest) -> str:
    from app.utils.security import create_access_token

    return create_access_token({"user_id": manifest["recruiter_id"]})


@pytest.fixture(scope="session")
def nurse_token(manifest) -> str:
    from app.utils.security import create_access_token

    return create_access_token({"user_id": manifest["nurse_id"]})


@pytest.fixture
def recruiter_client(base_url, recruiter_token) -> MediRouteClient:
    client = MediRouteClient(base_url, recruiter_token)
    yield client
    client.close()


@pytest.fixture
def nurse_client(base_url, nurse_token) -> MediRouteClient:
    client = MediRouteClient(base_url, nurse_token)
    yield client
    client.close()


@pytest.fixture
def nurse_online(nurse_client) -> MediRouteClient:
    nurse_client.set_nurse_online()
    return nurse_client


@pytest.fixture(autouse=True)
def release_nurse_blocking_state(nurse_client, recruiter_client, manifest):
    """Clear checked-in / confirmed assignments so dispatch tests stay independent."""
    release_nurse_blockers(nurse_client, recruiter_client, manifest["nurse_id"])
    yield
    release_nurse_blockers(nurse_client, recruiter_client, manifest["nurse_id"])


def pytest_collection_modifyitems(items):
    """Run stress suite last so dispatch fatigue does not break lifecycle tests."""
    stress = [i for i in items if i.get_closest_marker("stress")]
    rest = [i for i in items if i not in stress]
    items[:] = rest + stress
