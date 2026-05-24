"""Mobile app-state simulation — DB truth without live WS events."""
from __future__ import annotations

from typing import Any, Optional

from .api_client import MediRouteClient


def simulate_stale_offer_ui(shift_id: int, offer_id: int) -> dict:
    """In-memory 'UI state' that would be stale if WS missed revoke."""
    return {
        "type": "dispatch_offer",
        "shift_id": shift_id,
        "offer_id": offer_id,
        "lifecycle_stage": "pending",
    }


def simulate_reconcile_recovery(
    nurse_client: MediRouteClient,
    stale_ui: dict,
    *,
    trigger: str = "app_reopen",
) -> dict:
    """
    App killed with stale offer in memory → reconcile must clear ghost UI.
    Returns reconcile payload; raises if stale offer still valid incorrectly.
    """
    payload = nurse_client.reconcile(trigger=trigger)
    clear_ids = set(int(x) for x in payload.get("clear_offer_shift_ids") or [])
    terminal = {int(t["shift_id"]) for t in payload.get("terminal_shifts") or [] if t.get("shift_id")}
    sid = int(stale_ui["shift_id"])
    valid_ids = set(int(x) for x in payload.get("valid_offer_ids") or [])
    oid = int(stale_ui.get("offer_id", -1))

    stale_should_clear = sid in clear_ids or sid in terminal
    offer_invalid = oid not in valid_ids

    if not stale_should_clear and not offer_invalid:
        pending = payload.get("pending_offers") or []
        still_pending = any(int(o.get("offer_id", -1)) == oid for o in pending)
        if still_pending:
            raise AssertionError(
                f"Stale offer {oid} on shift {sid} still authoritative after reconcile"
            )

    return payload


def simulate_app_foreground_reconcile(client: MediRouteClient) -> dict:
    return client.reconcile(trigger="app_foreground")


def simulate_airplane_mode_recovery(client: MediRouteClient) -> dict:
    """Network restored — reconcile is authoritative."""
    return client.reconcile(trigger="network_online")


def assert_no_ghost_assignment(payload: dict, shift_id: int) -> None:
    """Active assignment must not coexist with terminal clear for same shift."""
    active = payload.get("active_shift_id")
    clear_ids = set(int(x) for x in payload.get("clear_offer_shift_ids") or [])
    terminal = {int(t["shift_id"]) for t in payload.get("terminal_shifts") or [] if t.get("shift_id")}
    sid = int(shift_id)
    if sid in clear_ids or sid in terminal:
        assert active != sid, f"Ghost active shift {sid} after terminal/clear"
