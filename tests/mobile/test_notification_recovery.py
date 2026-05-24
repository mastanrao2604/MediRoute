"""Suite 12 — Notification / FCM recovery via reconcile (no FCM mock)."""
from __future__ import annotations

import pytest

from tests.helpers.mobile_sim import simulate_reconcile_recovery, simulate_stale_offer_ui


pytestmark = [pytest.mark.mobile, pytest.mark.critical]


def test_background_kill_reconcile(nurse_client, manifest):
    fx = manifest["lifecycle_fixtures"]
    stale = simulate_stale_offer_ui(fx["cancelled_shift_id"], 12345)
    simulate_reconcile_recovery(nurse_client, stale, trigger="app_killed")


def test_duplicate_notification_dedupe_via_reconcile(nurse_client):
    """Two reconcile calls return consistent DB truth."""
    a = nurse_client.reconcile(trigger="dup_1")
    b = nurse_client.reconcile(trigger="dup_2")
    assert a.get("clear_offer_shift_ids") == b.get("clear_offer_shift_ids")


def test_delayed_delivery_recovery(nurse_client, manifest):
    payload = nurse_client.reconcile(trigger="delayed_notification")
    assert payload.get("role") == "nurse"
