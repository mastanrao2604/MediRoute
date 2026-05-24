"""Suite 5 — Recruiter confirm flow."""
from __future__ import annotations

import pytest

from tests.helpers.api_client import ApiError


pytestmark = [pytest.mark.api, pytest.mark.critical]


@pytest.fixture
def applied_shift(recruiter_client, nurse_online, manifest):
    nurse_online.set_nurse_online()
    created = recruiter_client.create_shift(hospital_name="Confirm Flow Test")
    sid = created["shift"]["id"]
    offer = nurse_online.wait_for_offer_on_shift(sid, timeout_sec=45.0)
    nurse_online.accept_offer(offer["offer_id"])
    return sid


def test_recruiter_confirm(applied_shift, recruiter_client, nurse_client, manifest):
    out = recruiter_client.confirm_staff(applied_shift, manifest["nurse_id"])
    assert out.get("confirmed") is True
    recon = nurse_client.reconcile(trigger="post_confirm")
    assert recon.get("active_shift_id") == applied_shift or applied_shift in (
        recon.get("clear_offer_shift_ids") or []
    )


def test_confirm_idempotent(recruiter_client, nurse_online, manifest):
    created = recruiter_client.create_shift(hospital_name="Double Confirm Idempotent")
    sid = created["shift"]["id"]
    offer = nurse_online.wait_for_offer_on_shift(sid, timeout_sec=45.0)
    nurse_online.accept_offer(offer["offer_id"])
    first = recruiter_client.confirm_staff(sid, manifest["nurse_id"])
    second = recruiter_client.confirm_staff(sid, manifest["nurse_id"])
    assert first.get("confirmed") is True
    assert second.get("confirmed") is True


def test_reconnect_after_confirm(applied_shift, recruiter_client, nurse_client, manifest):
    recruiter_client.confirm_staff(applied_shift, manifest["nurse_id"])
    recon = nurse_client.reconcile(trigger="ws_reconnect")
    clear = set(recon.get("clear_offer_shift_ids") or [])
    assert applied_shift in clear or recon.get("active_shift_id") == applied_shift
