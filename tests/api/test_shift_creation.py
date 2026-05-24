"""Suite 2 — Shift creation reliability."""
from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import ApiError, assert_json_serializable
from tests.helpers.config import STRESS_CREATE_COUNT


pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_post_shift_single(recruiter_client):
    out = recruiter_client.create_shift()
    assert out.get("created") is True
    shift = out["shift"]
    assert shift["id"]
    assert shift["status"] in ("open", "dispatching", "filled")


def test_list_shifts_after_create(recruiter_client):
    created = recruiter_client.create_shift(hospital_name="List Test Hospital")
    sid = created["shift"]["id"]
    listing = recruiter_client.list_shifts()
    ids = [s["id"] for s in listing.get("shifts") or []]
    assert sid in ids
    assert_json_serializable(listing)


def test_idempotent_create(recruiter_client):
    key = str(uuid.uuid4())
    a = recruiter_client.create_shift(idempotency_key=key)
    b = recruiter_client.create_shift(idempotency_key=key)
    assert a["shift"]["id"] == b["shift"]["id"]
    assert b.get("created") is False


def test_create_cancel_repost(recruiter_client):
    out = recruiter_client.create_shift(hospital_name="Cancel Repost")
    sid = out["shift"]["id"]
    cancel_resp = recruiter_client.cancel_shift(sid)
    assert cancel_resp.get("cancelled") is True
    import time
    deadline = time.monotonic() + 45.0
    row = None
    while time.monotonic() < deadline:
        listing = recruiter_client.list_shifts()
        row = next((s for s in listing["shifts"] if s["id"] == sid), None)
        if row and row["status"] == "cancelled":
            break
        time.sleep(0.5)
    assert row is not None, f"shift {sid} missing from recruiter list"
    assert row["status"] == "cancelled", f"shift {sid} stuck at {row['status']} after cancel"
    repost = recruiter_client.create_shift(hospital_name="Repost After Cancel")
    assert repost["shift"]["id"] != sid


@pytest.mark.stress
def test_repeated_shift_creates(recruiter_client):
    """100+ creates — zero 500s, zero serialization failures."""
    count = STRESS_CREATE_COUNT
    errors = []
    ids = []
    for i in range(count):
        try:
            out = recruiter_client.create_shift(
                hospital_name=f"Stress Hospital {i}",
                idempotency_key=str(uuid.uuid4()),
            )
            assert_json_serializable(out)
            sid = out["shift"]["id"]
            ids.append(sid)
            recruiter_client.cancel_shift(sid, reason="stress cleanup")
        except ApiError as exc:
            errors.append(f"#{i}: {exc}")
        except Exception as exc:
            errors.append(f"#{i}: {exc}")

    listing = recruiter_client.list_shifts()
    assert_json_serializable(listing)
    assert not errors, f"{len(errors)} create failures:\n" + "\n".join(errors[:10])
    assert len(ids) == count
