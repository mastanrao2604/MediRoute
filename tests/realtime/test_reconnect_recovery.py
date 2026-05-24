"""Suite 9 — Reconnect recovery (DB truth over WS memory)."""
from __future__ import annotations

import pytest

from tests.helpers.mobile_sim import (
    assert_no_ghost_assignment,
    simulate_airplane_mode_recovery,
    simulate_app_foreground_reconcile,
)
from tests.helpers.ws_client import run_async, ws_reconnect_cycle


pytestmark = [pytest.mark.realtime, pytest.mark.critical]


def test_reconcile_on_reconnect(nurse_client, manifest):
    fx = manifest["lifecycle_fixtures"]
    payload = nurse_client.reconcile(trigger="ws_reconnect")
    assert "role" in payload
    assert fx["cancelled_shift_id"] in payload.get("clear_offer_shift_ids", [])


def test_airplane_mode_recovery(nurse_client):
    payload = simulate_airplane_mode_recovery(nurse_client)
    assert payload.get("role") == "nurse"


def test_app_foreground_reconcile(nurse_client):
    payload = simulate_app_foreground_reconcile(nurse_client)
    assert "pending_offers" in payload or payload.get("role") == "nurse"


def test_ws_reconnect_then_reconcile(base_url, nurse_token, manifest, nurse_client):
    run_async(ws_reconnect_cycle(base_url, manifest["nurse_id"], nurse_token, cycles=5))
    payload = nurse_client.reconcile(trigger="ws_reconnect_storm")
    assert_no_ghost_assignment(payload, manifest["lifecycle_fixtures"]["expired_shift_id"])


def test_delayed_reconnect_db_truth(nurse_client, manifest):
    """Long offline — reconcile still authoritative."""
    payload = nurse_client.reconcile(trigger="long_offline")
    for sid in (
        manifest["lifecycle_fixtures"]["expired_shift_id"],
        manifest["lifecycle_fixtures"]["cancelled_shift_id"],
    ):
        assert sid in payload.get("clear_offer_shift_ids", [])
