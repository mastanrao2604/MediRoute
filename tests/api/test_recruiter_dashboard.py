"""Suite 3 — Recruiter dashboard (GET /shifts/) stability."""
from __future__ import annotations

import pytest

from tests.helpers.api_client import assert_json_serializable


pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_dashboard_load(recruiter_client):
    data = recruiter_client.list_shifts()
    assert "shifts" in data
    assert isinstance(data["shifts"], list)


def test_dashboard_includes_fixture_rows(recruiter_client, manifest):
    data = recruiter_client.list_shifts()
    fx = manifest["lifecycle_fixtures"]
    ids = {s["id"] for s in data["shifts"]}
    for key in ("expired_shift_id", "cancelled_shift_id", "under_review_shift_id", "confirmed_shift_id"):
        assert fx[key] in ids, f"Missing fixture shift {key}"


def test_malformed_rows_do_not_crash(recruiter_client):
    """Every row must serialize; no missing-field crash."""
    for _ in range(10):
        data = recruiter_client.list_shifts()
        for row in data["shifts"]:
            assert_json_serializable(row)
            assert "id" in row
            assert "status" in row


def test_expired_and_cancelled_visible(recruiter_client, manifest):
    data = recruiter_client.list_shifts()
    fx = manifest["lifecycle_fixtures"]
    by_id = {s["id"]: s for s in data["shifts"]}
    assert by_id[fx["expired_shift_id"]]["status"] == "expired"
    assert by_id[fx["cancelled_shift_id"]]["status"] == "cancelled"


def test_repeated_refresh_stable(recruiter_client):
    for _ in range(20):
        data = recruiter_client.list_shifts()
        assert isinstance(data["shifts"], list)
