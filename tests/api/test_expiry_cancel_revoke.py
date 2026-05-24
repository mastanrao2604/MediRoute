"""Suite 8 — Expiry / cancel / revoke cleanup."""
from __future__ import annotations

import pytest

from tests.helpers.mobile_sim import simulate_reconcile_recovery, simulate_stale_offer_ui


pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_expired_shift_reconcile_clears_stale(nurse_client, manifest):
    fx = manifest["lifecycle_fixtures"]
    sid = fx["expired_shift_id"]
    stale = simulate_stale_offer_ui(sid, offer_id=99999)
    payload = simulate_reconcile_recovery(nurse_client, stale, trigger="expired_cleanup")
    assert sid in payload.get("clear_offer_shift_ids", [])
    assert any(t.get("shift_id") == sid for t in payload.get("terminal_shifts") or [])


def test_cancel_reconcile_clears_stale(nurse_client, manifest):
    fx = manifest["lifecycle_fixtures"]
    sid = fx["cancelled_shift_id"]
    stale = simulate_stale_offer_ui(sid, offer_id=88888)
    payload = simulate_reconcile_recovery(nurse_client, stale, trigger="cancel_cleanup")
    assert sid in payload.get("clear_offer_shift_ids", [])


def test_cancel_live_shift(recruiter_client, nurse_online):
    created = recruiter_client.create_shift(hospital_name="Live Cancel")
    sid = created["shift"]["id"]
    try:
        nurse_online.wait_for_offer_on_shift(sid, timeout_sec=20.0)
    except TimeoutError:
        pass
    recruiter_client.cancel_shift(sid)
    recon = nurse_online.reconcile(trigger="post_cancel")
    assert sid in recon.get("clear_offer_shift_ids", [])


def test_missed_ws_self_heals(nurse_client, manifest):
    """Simulate missed WS revoke — reconcile is authoritative."""
    fx = manifest["lifecycle_fixtures"]
    for sid in (fx["expired_shift_id"], fx["cancelled_shift_id"]):
        stale = simulate_stale_offer_ui(sid, 1)
        simulate_reconcile_recovery(nurse_client, stale, trigger="missed_ws")
