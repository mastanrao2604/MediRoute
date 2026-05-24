"""Suite 14 — Operational stress tests."""
from __future__ import annotations

import time
import uuid

import pytest

from tests.helpers.config import STRESS_CREATE_COUNT, STRESS_RECONNECT_COUNT
from tests.helpers.ws_client import run_async, ws_reconnect_cycle


pytestmark = [pytest.mark.stress, pytest.mark.critical, pytest.mark.timeout(600)]


def test_create_cancel_repost_cycles(recruiter_client):
    cycles = min(STRESS_CREATE_COUNT, 100)
    for i in range(cycles):
        out = recruiter_client.create_shift(
            hospital_name=f"Cycle {i}",
            idempotency_key=str(uuid.uuid4()),
        )
        sid = out["shift"]["id"]
        recruiter_client.cancel_shift(sid)
        time.sleep(0.3)
    listing = recruiter_client.list_shifts()
    cancelled = [s for s in listing["shifts"] if s.get("status") == "cancelled"]
    assert len(cancelled) >= cycles - 2


def test_reconnect_stress_cycles(base_url, nurse_token, manifest):
    cycles = min(STRESS_RECONNECT_COUNT, 100)
    run_async(ws_reconnect_cycle(base_url, manifest["nurse_id"], nurse_token, cycles=cycles))


def test_reconcile_stress(nurse_client):
    for i in range(50):
        payload = nurse_client.reconcile(trigger=f"stress_{i}")
        assert payload.get("role") == "nurse"


def test_dashboard_stress_refresh(recruiter_client):
    for _ in range(50):
        data = recruiter_client.list_shifts()
        assert isinstance(data["shifts"], list)


def test_dashboard_during_active_dispatch(recruiter_client):
    """GET /shifts/ must stay responsive while dispatch waves are running."""
    import threading

    errors: list[str] = []
    stop = threading.Event()

    def poll_dashboard():
        while not stop.is_set():
            try:
                data = recruiter_client.list_shifts()
                assert isinstance(data["shifts"], list)
            except Exception as exc:
                errors.append(str(exc))
            time.sleep(0.05)

    poller = threading.Thread(target=poll_dashboard, daemon=True)
    poller.start()
    try:
        cycles = min(STRESS_CREATE_COUNT, 30)
        for i in range(cycles):
            out = recruiter_client.create_shift(
                hospital_name=f"Dispatch load {i}",
                idempotency_key=str(uuid.uuid4()),
                urgency="emergency",
            )
            sid = out["shift"]["id"]
            recruiter_client.reconcile(trigger=f"stress_dispatch_{i}")
            listing = recruiter_client.list_shifts()
            assert any(s.get("id") == sid for s in listing["shifts"])
            recruiter_client.cancel_shift(sid)
    finally:
        stop.set()
        poller.join(timeout=10)
    assert not errors, f"dashboard timeouts during dispatch: {errors[:5]}"
