"""Suite 6 — Check-in / check-out."""
from __future__ import annotations

import pytest

from tests.helpers.api_client import ApiError
from tests.helpers.config import HOSP_LAT, HOSP_LNG


pytestmark = [pytest.mark.api, pytest.mark.critical]


def _confirmed_shift(recruiter_client, nurse_online, manifest, label: str) -> int:
    nurse_online.set_nurse_online()
    created = recruiter_client.create_shift(hospital_name=f"Checkin {label}")
    sid = created["shift"]["id"]
    offer = nurse_online.wait_for_offer_on_shift(sid, timeout_sec=45.0)
    nurse_online.accept_offer(offer["offer_id"])
    recruiter_client.confirm_staff(sid, manifest["nurse_id"])
    return sid


def test_valid_gps_checkin(recruiter_client, nurse_online, nurse_client, manifest):
    sid = _confirmed_shift(recruiter_client, nurse_online, manifest, "valid")
    out = nurse_client.check_in(sid, lat=HOSP_LAT, lng=HOSP_LNG)
    assert out.get("checked_in") is True


def test_invalid_distance_checkin_blocked(recruiter_client, nurse_online, nurse_client, manifest):
    sid = _confirmed_shift(recruiter_client, nurse_online, manifest, "invalid")
    with pytest.raises(ApiError) as exc:
        nurse_client.check_in(sid, lat=28.6139, lng=77.2090)  # Delhi
    assert exc.value.response.status_code in (400, 422)


def test_reconnect_after_checkin(recruiter_client, nurse_online, nurse_client, manifest):
    sid = _confirmed_shift(recruiter_client, nurse_online, manifest, "reconnect")
    nurse_client.check_in(sid)
    nurse_client.reconcile(trigger="post_checkin")
    listing = nurse_client.list_shifts()
    row = next(s for s in listing["shifts"] if s["id"] == sid)
    assert row["assignment"]["lifecycle_stage"] == "checked_in"


def test_checkout_completes(recruiter_client, nurse_online, nurse_client, manifest):
    sid = _confirmed_shift(recruiter_client, nurse_online, manifest, "checkout")
    nurse_client.check_in(sid)
    out = nurse_client.check_out(sid)
    assert out.get("completed") is True
