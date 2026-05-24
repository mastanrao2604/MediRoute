"""Suite 11 — Location reliability (API-level degraded mode)."""
from __future__ import annotations

import pytest

from tests.helpers.config import HOSP_LAT, HOSP_LNG


pytestmark = [pytest.mark.api]


def test_nurse_online_without_crash(nurse_client):
    out = nurse_client.set_nurse_online()
    assert out.get("is_available") is True or "available" in str(out).lower()


def test_checkin_with_cached_coords(confirmed_shift_fixture, nurse_client):
    """GPS at hospital coords — operations continue."""
    out = nurse_client.check_in(confirmed_shift_fixture, lat=HOSP_LAT, lng=HOSP_LNG)
    assert out.get("checked_in") is True


@pytest.fixture
def confirmed_shift_fixture(recruiter_client, nurse_online, manifest):
    created = recruiter_client.create_shift(hospital_name="Location Reliability")
    sid = created["shift"]["id"]
    offer = nurse_online.wait_for_offer_on_shift(sid, timeout_sec=25.0)
    nurse_online.accept_offer(offer["offer_id"])
    recruiter_client.confirm_staff(sid, manifest["nurse_id"])
    return sid
