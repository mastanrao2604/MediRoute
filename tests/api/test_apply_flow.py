"""Suite 4 — Nurse apply flow."""
from __future__ import annotations

import pytest

from tests.helpers.api_client import ApiError


pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_nurse_apply_flow(recruiter_client, nurse_online, manifest):
    nurse_online.set_nurse_online()
    created = recruiter_client.create_shift(hospital_name="Apply Flow Test")
    sid = created["shift"]["id"]
    offer = nurse_online.wait_for_offer_on_shift(sid, timeout_sec=45.0)
    accepted = nurse_online.accept_offer(offer["offer_id"])
    assert accepted.get("accepted") is True
    assert accepted.get("lifecycle_stage") == "applied"


def test_duplicate_apply_blocked(recruiter_client, nurse_online, manifest):
    created = recruiter_client.create_shift(hospital_name="Dup Apply Test")
    sid = created["shift"]["id"]
    offer = nurse_online.wait_for_offer_on_shift(sid, timeout_sec=25.0)
    nurse_online.accept_offer(offer["offer_id"])
    with pytest.raises(ApiError) as exc:
        nurse_online.accept_offer(offer["offer_id"])
    assert exc.value.response.status_code in (409, 400)


def test_apply_on_cancelled_shift_blocked(recruiter_client, nurse_online, manifest):
    fx = manifest["lifecycle_fixtures"]
    cancelled_id = fx["cancelled_shift_id"]
    pending = nurse_online.pending_offers()
    offer_ids = [o["offer_id"] for o in pending.get("offers", []) if o.get("shift_id") == cancelled_id]
    if not offer_ids:
        recon = nurse_online.reconcile(trigger="test_cancelled")
        assert cancelled_id in recon.get("clear_offer_shift_ids", [])
        return
    with pytest.raises(ApiError):
        nurse_online.accept_offer(offer_ids[0])


def test_reconcile_clears_cancelled_offer(nurse_client, manifest):
    fx = manifest["lifecycle_fixtures"]
    recon = nurse_client.reconcile(trigger="test_cancelled_reconcile")
    assert fx["cancelled_shift_id"] in recon.get("clear_offer_shift_ids", [])
