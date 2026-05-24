"""Suite 7 — No-show lifecycle."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers.cleanup_db import backdate_shift_start
from tests.helpers.config import MANIFEST_PATH


pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_no_show_recovery_flow(recruiter_client, nurse_online, manifest):
    nurse_online.set_nurse_online()
    created = recruiter_client.create_shift(hospital_name="No Show Recovery")
    sid = created["shift"]["id"]
    offer = nurse_online.wait_for_offer_on_shift(sid, timeout_sec=45.0)
    nurse_online.accept_offer(offer["offer_id"])
    recruiter_client.confirm_staff(sid, manifest["nurse_id"])

    db_url = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("database_url")
    backdate_shift_start(sid, minutes_ago=10, db_url=db_url)

    out = recruiter_client.mark_no_show(sid, manifest["nurse_id"])
    assert out.get("no_show") is True
    assert out.get("search_reopened") is True

    recon = nurse_online.reconcile(trigger="recruiter_no_show")
    assert recon.get("active_shift_id") != sid


def test_no_show_reconcile_fixture(nurse_client, manifest):
    fx = manifest["lifecycle_fixtures"]
    recon = nurse_client.reconcile(trigger="no_show_fixture")
    assert recon.get("active_shift_id") != fx.get("no_show_shift_id")
