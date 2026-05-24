"""Suite 15 — Full end-to-end pilot flow with reconnect at each stage."""
from __future__ import annotations

import pytest

from tests.helpers.config import HOSP_LAT, HOSP_LNG
from tests.helpers.ws_client import run_async, ws_reconnect_cycle


pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_full_pilot_flow_with_reconnects(
    base_url,
    recruiter_client,
    nurse_online,
    nurse_client,
    recruiter_token,
    nurse_token,
    manifest,
):
    # 1. Recruiter posts shift
    created = recruiter_client.create_shift(hospital_name="E2E Pilot Flow")
    sid = created["shift"]["id"]
    run_async(ws_reconnect_cycle(base_url, manifest["recruiter_id"], recruiter_token, cycles=1))

    # 2. Nurse receives offer (via DB/dispatch, not WS dependency)
    offer = nurse_online.wait_for_offer_on_shift(sid, timeout_sec=30.0)
    run_async(ws_reconnect_cycle(base_url, manifest["nurse_id"], nurse_token, cycles=1))

    # 3. Nurse applies
    nurse_online.accept_offer(offer["offer_id"])
    nurse_client.reconcile(trigger="e2e_applied")

    # 4. Recruiter reviews (list shifts)
    listing = recruiter_client.list_shifts()
    row = next(s for s in listing["shifts"] if s["id"] == sid)
    assert row["status"] in ("dispatching", "open", "filled")

    # 5. Recruiter confirms
    recruiter_client.confirm_staff(sid, manifest["nurse_id"])
    run_async(ws_reconnect_cycle(base_url, manifest["nurse_id"], nurse_token, cycles=1))
    nurse_client.reconcile(trigger="e2e_confirmed")

    # 6. Nurse checks in
    nurse_client.check_in(sid, lat=HOSP_LAT, lng=HOSP_LNG)
    nurse_client.reconcile(trigger="e2e_checkin")

    # 7. Shift completes
    nurse_client.check_out(sid)
    recon = nurse_client.reconcile(trigger="e2e_complete")
    assert recon.get("role") == "nurse"
