"""Release nurse blocking assignments between operational tests."""
from __future__ import annotations

import json

from .api_client import MediRouteClient
from .cleanup_db import force_clear_blocking_assignments
from .config import MANIFEST_PATH


def release_nurse_blockers(
    nurse_client: MediRouteClient,
    recruiter_client: MediRouteClient,
    nurse_id: int,
) -> None:
    """Clear confirmed/checked-in rows that block POST /dispatch/offers/*/accept."""
    try:
        listing = nurse_client.list_shifts()
    except Exception:
        return

    for row in listing.get("shifts") or []:
        sid = row.get("id")
        assignment = row.get("assignment") or {}
        if sid is None or not assignment:
            continue

        stage = assignment.get("lifecycle_stage")
        status = assignment.get("status")
        recruiter_ok = assignment.get("recruiter_confirmed")

        if stage == "checked_in" or status == "checked_in":
            try:
                nurse_client.check_out(sid)
            except Exception:
                pass
            continue

        if stage == "recruiter_confirmed" or (recruiter_ok and status == "confirmed"):
            try:
                recruiter_client.cancel_shift(sid, reason="test cleanup")
            except Exception:
                try:
                    recruiter_client.mark_no_show(sid, nurse_id)
                except Exception:
                    pass
            continue

        if stage == "applied" or status == "applied":
            try:
                recruiter_client.cancel_shift(sid, reason="test cleanup")
            except Exception:
                pass

    # API path cannot cancel filled shifts — test DB fallback
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        force_clear_blocking_assignments(nurse_id, manifest.get("database_url"))
    except Exception:
        pass
