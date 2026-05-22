"""
Operations Dashboard routes — manual dispatch control for ops team.
These are mandatory, not optional (Swiggy lesson — §19 in ARCHITECTURE.md).

Endpoints:
  GET  /admin/ops/health-snapshot       — in-memory health (zero extra DB), poll 10s
  GET  /admin/ops/supply-snapshot       — nurse presence + offer counts, poll 30s
  GET  /admin/ops/live-shifts           — active shifts with dispatch state
  GET  /admin/ops/presence              — online nurses in a city/zone
  POST /admin/ops/manual-assign         — force-assign nurse to shift
  POST /admin/ops/re-dispatch/{shift_id} — restart dispatch for failed shift
  GET  /admin/ops/failed-shifts         — expired/unfilled shifts (last N hours)
  GET  /admin/ops/metrics               — live AUSFT and funnel metrics
  PATCH /admin/ops/zones/{zone_code}    — pause/resume a dispatch zone
  GET  /admin/ops/timeline/{shift_id}   — chronological shift event log
  POST /admin/ops/dispatch-toggle       — runtime kill switch
  POST /admin/ops/expire-session/{id}   — force-expire stuck session
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from ..utils.datetime_util import utc_iso
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_admin
from .. import models
from ..ws_manager import ws_manager
from ..dispatch.engine import (
    start_dispatch, get_dispatch_metrics, dispatch_events,
    is_dispatch_enabled, set_dispatch_enabled, get_semaphore_utilization,
    cancel_dispatch_session,
)
from ..dispatch.events import (
    MANUAL_OVERRIDE, SHIFT_CREATED, OFFER_TIMED_OUT,
    MANUAL_DISPATCH_CANCELLED, MANUAL_RETRY_TRIGGERED,
    MANUAL_ASSIGNMENT_CREATED, MANUAL_SESSION_CLOSED,
)
from ..dispatch.janitor import get_janitor_health, JANITOR_INTERVAL_SEC

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/ops", tags=["Operations"])


class ManualAssignRequest(BaseModel):
    shift_id: int
    nurse_user_id: int
    reason: str


class ZonePatchRequest(BaseModel):
    dispatch_paused: Optional[bool] = None
    is_active: Optional[bool] = None
    max_radius_km: Optional[float] = None


class DispatchToggleRequest(BaseModel):
    enabled: bool


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/live-shifts")
def live_shifts(
    city_id: str = "HYD",
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Active and recently dispatching shifts with session state."""
    cutoff = datetime.utcnow() - timedelta(hours=2)
    shifts = (
        db.query(models.ShiftRequest)
        .filter(
            models.ShiftRequest.city_id == city_id,
            models.ShiftRequest.status.in_([
                models.ShiftRequestStatus.open,
                models.ShiftRequestStatus.dispatching,
                models.ShiftRequestStatus.filled,
            ]),
            models.ShiftRequest.created_at >= cutoff,
        )
        .order_by(models.ShiftRequest.created_at.desc())
        .limit(100)
        .all()
    )

    result = []
    for shift in shifts:
        session = db.query(models.DispatchSession).filter(
            models.DispatchSession.shift_request_id == shift.id
        ).first()
        assignment = db.query(models.LiveAssignment).filter(
            models.LiveAssignment.shift_request_id == shift.id
        ).first()
        result.append({
            "shift_id": shift.id,
            "hospital_name": shift.hospital_name,
            "role": shift.role_required.value,
            "urgency": shift.urgency.value,
            "status": shift.status.value,
            "created_at": shift.created_at.isoformat() if shift.created_at else None,
            "filled_at": shift.filled_at.isoformat() if shift.filled_at else None,
            "fill_time_sec": int((shift.filled_at - shift.created_at).total_seconds())
            if shift.filled_at and shift.created_at else None,
            "dispatch": {
                "session_id": session.id,
                "wave": session.current_wave if session else None,
                "status": session.status.value if session else None,
            } if session else None,
            "assignment": {
                "nurse_id": assignment.nurse_user_id,
                "status": assignment.status.value,
            } if assignment else None,
        })

    return {"shifts": result, "city_id": city_id}


@router.get("/presence")
def nurse_presence(
    city_id: str = "HYD",
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """All online nurses in a city — for ops map view."""
    freshness_cutoff = datetime.utcnow() - timedelta(minutes=5)
    rows = (
        db.query(models.User, models.PresenceState, models.NurseAvailability)
        .join(models.PresenceState, models.User.id == models.PresenceState.user_id)
        .outerjoin(models.NurseAvailability, models.User.id == models.NurseAvailability.user_id)
        .filter(
            models.PresenceState.city_id == city_id,
            models.PresenceState.state != models.PresenceStateEnum.offline,
            models.PresenceState.last_heartbeat >= freshness_cutoff,
        )
        .all()
    )

    return {
        "nurses": [
            {
                "user_id": user.id,
                "name": user.name,
                "role": user.role.value if user.role else None,
                "state": presence.state.value,
                "is_available": avail.is_available if avail else False,
                "latitude": presence.latitude,
                "longitude": presence.longitude,
                "last_heartbeat": presence.last_heartbeat.isoformat() if presence.last_heartbeat else None,
            }
            for user, presence, avail in rows
        ],
        "online_count": len(rows),
        "city_id": city_id,
    }


@router.post("/manual-assign")
async def manual_assign(
    req: ManualAssignRequest,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Bypass dispatch engine. Directly assign a nurse to a shift.
    Use when dispatch fails or for urgent situations.
    """
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == req.shift_id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if shift.status == models.ShiftRequestStatus.filled:
        raise HTTPException(status_code=409, detail="Shift is already filled.")
    if shift.status == models.ShiftRequestStatus.cancelled:
        raise HTTPException(status_code=409, detail="Shift is cancelled.")

    nurse = db.query(models.User).filter(models.User.id == req.nurse_user_id).first()
    if not nurse:
        raise HTTPException(status_code=404, detail="Nurse not found.")

    # Guard: prevent duplicate assignments (LiveAssignment.shift_request_id is UNIQUE)
    existing_assignment = db.query(models.LiveAssignment).filter(
        models.LiveAssignment.shift_request_id == req.shift_id,
    ).first()
    if existing_assignment:
        raise HTTPException(status_code=409, detail="Shift already has an active assignment.")

    now = datetime.utcnow()

    # If an active dispatch session is running, cancel it before assigning
    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == req.shift_id
    ).first()
    if session and session.status == models.DispatchSessionStatus.active:
        pending = db.query(models.DispatchOffer).filter(
            models.DispatchOffer.session_id == session.id,
            models.DispatchOffer.status == models.OfferStatus.pending,
        ).all()
        for o in pending:
            o.status = models.OfferStatus.cancelled
            o.responded_at = now
        session.status = models.DispatchSessionStatus.cancelled
        session.completed_at = now
        db.flush()
        # Wake up the dispatch engine so it stops at the next wave boundary
        cancel_dispatch_session(session.id)

    # Create a manual dispatch session if one doesn't exist
    if not session:
        session = models.DispatchSession(
            shift_request_id=req.shift_id,
            status=models.DispatchSessionStatus.completed,
            completed_at=now,
        )
        db.add(session)
        db.flush()

    # Create a manual offer
    offer = models.DispatchOffer(
        session_id=session.id,
        shift_request_id=req.shift_id,
        nurse_user_id=req.nurse_user_id,
        status=models.OfferStatus.accepted,
        expires_at=now,
        responded_at=now,
        delivery_method=models.OfferDeliveryMethod.websocket,
    )
    db.add(offer)
    db.flush()

    # Create assignment
    assignment = models.LiveAssignment(
        shift_request_id=req.shift_id,
        nurse_user_id=req.nurse_user_id,
        offer_id=offer.id,
        confirmed_at=now,
    )
    db.add(assignment)

    # Mark shift filled
    shift.status = models.ShiftRequestStatus.filled
    shift.filled_at = now

    # Mark nurse busy
    presence = db.query(models.PresenceState).filter(
        models.PresenceState.user_id == req.nurse_user_id
    ).first()
    if presence:
        presence.state = models.PresenceStateEnum.online_busy

    avail = db.query(models.NurseAvailability).filter(
        models.NurseAvailability.user_id == req.nurse_user_id
    ).first()
    if avail:
        avail.is_available = False

    # Timeline event
    event = models.ShiftTimelineEvent(
        shift_request_id=req.shift_id,
        event_type=MANUAL_ASSIGNMENT_CREATED,
        actor_user_id=admin.id,
        city_id=shift.city_id,
        payload={
            "nurse_id": req.nurse_user_id,
            "nurse_name": nurse.name or f"Nurse #{req.nurse_user_id}",
            "reason": req.reason,
            "admin_id": admin.id,
        },
    )
    db.add(event)
    db.commit()

    # Notify nurse via WS
    await ws_manager.send(req.nurse_user_id, {
        "type": "assignment_confirmed",
        "assignment_id": assignment.id,
        "shift_id": req.shift_id,
        "hospital_name": shift.hospital_name,
        "shift_start": utc_iso(shift.shift_start),
        "message": "Assignment confirmed by operations team.",
    })

    # Notify hospital via WS
    await ws_manager.send(shift.hospital_user_id, {
        "type": "shift_filled",
        "shift_id": req.shift_id,
        "nurse_name": nurse.name or f"Nurse #{req.nurse_user_id}",
        "manual": True,
        "message": f"✅ Manually assigned by operations: {nurse.name or 'Nurse'}",
    })

    logger.info(
        "[ops] manual assign: shift %d → nurse %d by admin %d (%s)",
        req.shift_id, req.nurse_user_id, admin.id, req.reason
    )

    return {"success": True, "assignment_id": assignment.id}


@router.post("/re-dispatch/{shift_id}")
async def re_dispatch(
    shift_id: int,
    reason: Optional[str] = None,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Restart dispatch for an expired, failed, or cancelled shift.

    Deletes old offers then session (required — DispatchSession.shift_request_id
    has a unique constraint, so a new session cannot be created while the old one
    exists). Shift-level timeline events are preserved.

    Rejects if shift is currently dispatching — use cancel-dispatch first.
    """
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if shift.status == models.ShiftRequestStatus.filled:
        raise HTTPException(status_code=409, detail="Shift is already filled.")
    if shift.status == models.ShiftRequestStatus.dispatching:
        raise HTTPException(
            status_code=409,
            detail="Dispatch is currently active. Use cancel-dispatch first.",
        )

    # Clean up old session — must delete offers before session (FK constraint)
    old_session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == shift_id
    ).first()
    if old_session:
        db.query(models.DispatchOffer).filter(
            models.DispatchOffer.session_id == old_session.id
        ).delete(synchronize_session=False)
        db.delete(old_session)
        db.flush()

    # Reset shift to open
    shift.status = models.ShiftRequestStatus.open

    # Audit trail
    db.add(models.ShiftTimelineEvent(
        shift_request_id=shift_id,
        event_type=MANUAL_RETRY_TRIGGERED,
        actor_user_id=admin.id,
        city_id=shift.city_id,
        payload={"admin_id": admin.id, "reason": reason or ""},
    ))
    db.commit()

    await start_dispatch(shift_id)

    logger.info(
        "[ops] re-dispatch triggered for shift %d by admin %d (reason: %s)",
        shift_id, admin.id, reason,
    )
    return {"success": True, "message": f"Dispatch restarted for shift {shift_id}"}


@router.get("/failed-shifts")
def failed_shifts(
    hours: int = 24,
    city_id: str = "HYD",
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Expired/unfilled shifts in the last N hours for ops review."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    shifts = (
        db.query(models.ShiftRequest)
        .filter(
            models.ShiftRequest.city_id == city_id,
            models.ShiftRequest.status.in_([
                models.ShiftRequestStatus.expired,
                models.ShiftRequestStatus.cancelled,
            ]),
            models.ShiftRequest.created_at >= cutoff,
        )
        .order_by(models.ShiftRequest.created_at.desc())
        .all()
    )
    return {
        "failed_shifts": [
            {
                "shift_id": s.id,
                "hospital_name": s.hospital_name,
                "role": s.role_required.value,
                "urgency": s.urgency.value,
                "status": s.status.value,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in shifts
        ],
        "count": len(shifts),
        "hours_window": hours,
    }


@router.get("/metrics")
def ops_metrics(
    city_id: str = "HYD",
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Live operational metrics for the day.
    AUSFT = average time from shift_created_at to filled_at.
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    filled = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.city_id == city_id,
        models.ShiftRequest.status == models.ShiftRequestStatus.filled,
        models.ShiftRequest.filled_at >= today_start,
    ).all()

    fill_times = [
        (s.filled_at - s.created_at).total_seconds()
        for s in filled
        if s.filled_at and s.created_at
    ]
    ausft_sec = sum(fill_times) / len(fill_times) if fill_times else None

    online_nurses = db.query(models.PresenceState).filter(
        models.PresenceState.city_id == city_id,
        models.PresenceState.state == models.PresenceStateEnum.online_available,
        models.PresenceState.last_heartbeat >= datetime.utcnow() - timedelta(minutes=5),
    ).count()

    active_dispatches = db.query(models.DispatchSession).filter(
        models.DispatchSession.status == models.DispatchSessionStatus.active,
    ).count()

    expired_today = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.city_id == city_id,
        models.ShiftRequest.status == models.ShiftRequestStatus.expired,
        models.ShiftRequest.created_at >= today_start,
    ).count()

    return {
        "city_id": city_id,
        "ausft_sec": round(ausft_sec, 1) if ausft_sec else None,
        "ausft_min": round(ausft_sec / 60, 1) if ausft_sec else None,
        "shifts_filled_today": len(filled),
        "shifts_expired_today": expired_today,
        "online_nurses_now": online_nurses,
        "active_dispatches_now": active_dispatches,
        "ws_connections": ws_manager.connection_count,
        "dispatch_metrics": get_dispatch_metrics(),
    }


@router.patch("/zones/{zone_code}")
def patch_zone(
    zone_code: str,
    req: ZonePatchRequest,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Update zone operational config without a deploy.
    FUTURE: Full ZoneOperationalConfig (§24.10).
    """
    zone = db.query(models.DispatchZone).filter(
        models.DispatchZone.zone_code == zone_code
    ).first()
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_code}' not found.")

    if req.dispatch_paused is not None:
        zone.dispatch_paused = req.dispatch_paused
    if req.is_active is not None:
        zone.is_active = req.is_active
    if req.max_radius_km is not None:
        zone.max_radius_km = req.max_radius_km

    db.commit()
    logger.info("[ops] zone %s updated: %s", zone_code, req.dict(exclude_none=True))

    return {
        "zone_code": zone_code,
        "dispatch_paused": zone.dispatch_paused,
        "is_active": zone.is_active,
        "max_radius_km": zone.max_radius_km,
    }


# ── Operational health snapshot (Task 7) ─────────────────────────────────────

@router.get("/health-snapshot")
def health_snapshot(
    admin: models.User = Depends(require_admin),
):
    """
    Lightweight real-time operational health check.
    All data is in-memory — zero additional DB queries. Safe to poll every 10s.
    Exposes: WS connections, dispatch state, janitor liveness, semaphore, stale sockets.
    """
    janitor = get_janitor_health()
    semaphore = get_semaphore_utilization()
    return {
        "ts": datetime.utcnow().isoformat(),
        "dispatch_enabled": is_dispatch_enabled(),
        "active_dispatch_sessions": len(dispatch_events),
        "ws_connections": ws_manager.connection_count,
        "ws_stale": ws_manager.stale_count(),
        "janitor": janitor,
        "semaphore": semaphore,
        "dispatch_metrics": get_dispatch_metrics(),
    }


# ── Runtime dispatch kill switch (Task 4) ─────────────────────────────────────

@router.post("/dispatch-toggle")
def dispatch_toggle(
    req: DispatchToggleRequest,
    admin: models.User = Depends(require_admin),
):
    """
    Toggle the dispatch kill switch at runtime (no restart required).
    In-flight dispatches continue to completion; new ones are blocked when disabled.
    Action is logged and immediately reflected in /health-snapshot.
    """
    new_state = set_dispatch_enabled(req.enabled, actor=f"admin:{admin.id}:{admin.phone}")
    logger.warning(
        "[ops] dispatch kill switch → %s by admin %d",
        "ENABLED" if new_state else "DISABLED", admin.id,
    )
    return {
        "dispatch_enabled": new_state,
        "message": (
            "Dispatch enabled. New shifts will be dispatched normally."
            if new_state else
            "Dispatch DISABLED. No new dispatch tasks will start. In-flight dispatches continue."
        ),
    }


# ── Shift timeline viewer (Task 3) ───────────────────────────────────────────

@router.get("/timeline/{shift_id}")
def shift_timeline(
    shift_id: int,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Chronological timeline of all dispatch events for a shift.
    Use this to debug incidents: what happened, when, and why.

    Enhanced v2:
    - actor_names: resolves all actor_user_ids to names in one query (no N+1)
    - fill_time_sec: pre-calculated for summary display
    - dispatch_session: session state context for the timeline header
    - actor_name on each event: ready for direct display
    - Bounded to 200 events max (shifts never approach this in practice)
    """
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    events = (
        db.query(models.ShiftTimelineEvent)
        .filter(models.ShiftTimelineEvent.shift_request_id == shift_id)
        .order_by(models.ShiftTimelineEvent.occurred_at.asc())
        .limit(200)
        .all()
    )

    # Resolve actor names in one query — no N+1
    actor_ids = {e.actor_user_id for e in events if e.actor_user_id is not None}
    actor_names: dict = {}
    if actor_ids:
        rows = db.query(models.User.id, models.User.name).filter(
            models.User.id.in_(actor_ids)
        ).all()
        actor_names = {row.id: (row.name or f"User #{row.id}") for row in rows}

    # Dispatch session context
    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == shift_id
    ).first()

    # Pre-calculate fill time for summary display
    fill_time_sec = None
    if shift.filled_at and shift.created_at:
        fill_time_sec = int((shift.filled_at - shift.created_at).total_seconds())

    return {
        "shift_id": shift_id,
        "hospital_name": shift.hospital_name,
        "role": shift.role_required.value,
        "urgency": shift.urgency.value,
        "status": shift.status.value,
        "created_at": shift.created_at.isoformat() if shift.created_at else None,
        "filled_at": shift.filled_at.isoformat() if shift.filled_at else None,
        "fill_time_sec": fill_time_sec,
        "actor_names": actor_names,
        "dispatch_session": {
            "id": session.id,
            "status": session.status.value,
            "current_wave": session.current_wave,
            "waves_exhausted": session.waves_exhausted,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        } if session else None,
        "timeline": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "occurred_at": e.occurred_at.isoformat(),
                "actor_user_id": e.actor_user_id,
                "actor_name": actor_names.get(e.actor_user_id) if e.actor_user_id else None,
                "payload": e.payload or {},
            }
            for e in events
        ],
        "event_count": len(events),
    }


# ── Force-expire stuck session offers (Task 5) ────────────────────────────────

@router.post("/expire-session/{session_id}")
def expire_session_offers(
    session_id: int,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Force-expire all pending offers in a stuck dispatch session.
    Use when a dispatch session is frozen and not progressing.
    Marks all pending offers as timed_out. The dispatch engine will
    detect the event and move to next wave or mark the session failed.
    """
    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Dispatch session not found.")

    now = datetime.utcnow()
    pending_offers = db.query(models.DispatchOffer).filter(
        models.DispatchOffer.session_id == session_id,
        models.DispatchOffer.status == models.OfferStatus.pending,
    ).all()

    if not pending_offers:
        return {"expired_count": 0, "message": "No pending offers in this session."}

    for offer in pending_offers:
        offer.status = models.OfferStatus.timed_out
        offer.responded_at = now

    # Write timeline event for audit trail
    shift_id = session.shift_request_id
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    event = models.ShiftTimelineEvent(
        shift_request_id=shift_id,
        event_type=OFFER_TIMED_OUT,
        actor_user_id=admin.id,
        city_id=shift.city_id if shift else "HYD",
        payload={
            "manual": True,
            "admin_id": admin.id,
            "expired_count": len(pending_offers),
            "session_id": session_id,
        },
    )
    db.add(event)
    db.commit()

    # Signal the dispatch engine so it stops waiting on this session
    event_signal = dispatch_events.get(session_id)
    if event_signal:
        event_signal.set()

    logger.warning(
        "[ops] admin %d force-expired %d offers in session %d",
        admin.id, len(pending_offers), session_id,
    )
    return {
        "expired_count": len(pending_offers),
        "session_id": session_id,
        "message": f"Force-expired {len(pending_offers)} pending offers. Dispatch engine will advance.",
    }


# ── Supply snapshot — nurse presence + offer counts (2 queries) ───────────────

# Failure event types tracked for the failure breakdown panel
_FAILURE_EVENT_TYPES = [
    "dispatch.wave_exhausted",
    "dispatch.failed",
    "shift.expired",
    "shift.cancelled",
]

@router.get("/supply-snapshot")
def supply_snapshot(
    city_id: str = "HYD",
    hours: int = 4,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Nurse presence counts + offer pipeline counts + failure breakdown.
    3 efficient GROUP BY queries — all use indexed columns.
    Safe to poll every 30s from dashboard.

    Returns:
      - nurses: presence state breakdown (fresh heartbeat only)
      - offers: offer status breakdown for last N hours
      - failures: timeline event type breakdown for last N hours
      - db_ok: True (reaching here proves DB is reachable)
    """
    fresh_cutoff = datetime.utcnow() - timedelta(minutes=5)
    time_cutoff = datetime.utcnow() - timedelta(hours=hours)

    # ── 1. Nurse presence: GROUP BY state ─────────────────────────────────────
    # Uses idx_presence_city_state. One query replaces 4 separate COUNTs.
    presence_rows = (
        db.query(models.PresenceState.state, func.count(models.PresenceState.id))
        .filter(
            models.PresenceState.city_id == city_id,
            models.PresenceState.last_heartbeat >= fresh_cutoff,
        )
        .group_by(models.PresenceState.state)
        .all()
    )
    nurses_by_state = {row[0].value: row[1] for row in presence_rows}

    # ── 2. Offer counts: GROUP BY status for last N hours ─────────────────────
    # Uses idx_offer_expires_status. Bounded by hours parameter.
    offer_rows = (
        db.query(models.DispatchOffer.status, func.count(models.DispatchOffer.id))
        .filter(models.DispatchOffer.offered_at >= time_cutoff)
        .group_by(models.DispatchOffer.status)
        .all()
    )
    offers_by_status = {row[0].value: row[1] for row in offer_rows}

    # ── 3. Failure breakdown: GROUP BY event_type ─────────────────────────────
    # Uses idx_timeline_city_type. Only selects failure-related event types.
    failure_rows = (
        db.query(
            models.ShiftTimelineEvent.event_type,
            func.count(models.ShiftTimelineEvent.id),
        )
        .filter(
            models.ShiftTimelineEvent.city_id == city_id,
            models.ShiftTimelineEvent.occurred_at >= time_cutoff,
            models.ShiftTimelineEvent.event_type.in_(_FAILURE_EVENT_TYPES),
        )
        .group_by(models.ShiftTimelineEvent.event_type)
        .all()
    )
    failures_by_type = {row[0]: row[1] for row in failure_rows}

    total_nurses = sum(nurses_by_state.values())

    return {
        "ts": datetime.utcnow().isoformat(),
        "city_id": city_id,
        "window_hours": hours,
        "nurses": {
            "online_available": nurses_by_state.get("online_available", 0),
            "online_busy": nurses_by_state.get("online_busy", 0),
            "background": nurses_by_state.get("background", 0),
            "total_fresh": total_nurses,
            "freshness_window_min": 5,
        },
        "offers": {
            "pending": offers_by_status.get("pending", 0),
            "accepted": offers_by_status.get("accepted", 0),
            "declined": offers_by_status.get("declined", 0),
            "timed_out": offers_by_status.get("timed_out", 0),
            "cancelled": offers_by_status.get("cancelled", 0),
        },
        "failures": {
            "wave_exhausted": failures_by_type.get("dispatch.wave_exhausted", 0),
            "dispatch_failed": failures_by_type.get("dispatch.failed", 0),
            "shift_expired": failures_by_type.get("shift.expired", 0),
            "shift_cancelled": failures_by_type.get("shift.cancelled", 0),
        },
        "db_ok": True,
    }


# ── Cancel active dispatch ─────────────────────────────────────────────────────

@router.post("/cancel-dispatch/{shift_id}")
async def cancel_dispatch(
    shift_id: int,
    reason: Optional[str] = None,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Stop an active dispatch for a shift.

    - Cancels all pending offers (nurses will no longer see the offer)
    - Marks the dispatch session as cancelled
    - Resets shift status to 'open' (ready for re-dispatch or manual assign)
    - Signals the dispatch engine to stop at the next wave boundary
    - Emits MANUAL_DISPATCH_CANCELLED timeline event for full audit trail

    Use when: dispatch is stuck, wrong nurses notified, or ops needs to intervene.
    """
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if shift.status != models.ShiftRequestStatus.dispatching:
        raise HTTPException(
            status_code=409,
            detail=f"Shift is not actively dispatching (status: {shift.status.value}). Nothing to cancel.",
        )

    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == shift_id,
        models.DispatchSession.status == models.DispatchSessionStatus.active,
    ).first()
    if not session:
        # Shift status inconsistency — just reset it
        shift.status = models.ShiftRequestStatus.open
        db.commit()
        return {
            "success": True,
            "cancelled_offers": 0,
            "message": "Shift was dispatching but no active session found. Reset to open.",
        }

    now = datetime.utcnow()

    # Cancel all pending offers
    pending_offers = db.query(models.DispatchOffer).filter(
        models.DispatchOffer.session_id == session.id,
        models.DispatchOffer.status == models.OfferStatus.pending,
    ).all()
    cancelled_count = len(pending_offers)
    for offer in pending_offers:
        offer.status = models.OfferStatus.cancelled
        offer.responded_at = now

    # Close the session
    session.status = models.DispatchSessionStatus.cancelled
    session.completed_at = now

    # Reset shift to open — admins choose whether to retry or manually assign
    shift.status = models.ShiftRequestStatus.open

    # Audit trail
    db.add(models.ShiftTimelineEvent(
        shift_request_id=shift_id,
        event_type=MANUAL_DISPATCH_CANCELLED,
        actor_user_id=admin.id,
        city_id=shift.city_id,
        payload={
            "admin_id": admin.id,
            "reason": reason or "",
            "cancelled_offers": cancelled_count,
            "session_id": session.id,
        },
    ))
    db.commit()

    # Signal dispatch engine to stop at next wave boundary (non-blocking)
    cancel_dispatch_session(session.id)

    # Notify hospital
    await ws_manager.send(shift.hospital_user_id, {
        "type": "dispatch_error",
        "shift_id": shift_id,
        "reason": "manual_cancelled",
        "message": "Dispatch cancelled by operations team. Shift is open for re-dispatch.",
    })

    logger.warning(
        "[ops] admin %d cancelled dispatch for shift %d (session %d, %d offers, reason: %s)",
        admin.id, shift_id, session.id, cancelled_count, reason,
    )
    return {
        "success": True,
        "cancelled_offers": cancelled_count,
        "session_id": session.id,
        "message": f"Dispatch cancelled. {cancelled_count} pending offers expired. Shift reset to open.",
    }


# ── Close stuck / orphaned session ────────────────────────────────────────────

@router.post("/close-session/{session_id}")
def close_session(
    session_id: int,
    reason: Optional[str] = None,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Force-close an orphaned dispatch session stuck in 'active' state.

    Use when: the dispatch engine crashed or restarted mid-dispatch, leaving a
    session in 'active' with no engine running for it. This cleans up the state
    so the shift can be re-dispatched or manually assigned.

    Any remaining pending offers are expired. Shift is reset to 'open'.
    Emits MANUAL_SESSION_CLOSED timeline event for audit.
    """
    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.id == session_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.status != models.DispatchSessionStatus.active:
        raise HTTPException(
            status_code=409,
            detail=f"Session is not active (status: {session.status.value}). Already closed.",
        )

    now = datetime.utcnow()

    # Expire remaining pending offers
    pending_offers = db.query(models.DispatchOffer).filter(
        models.DispatchOffer.session_id == session_id,
        models.DispatchOffer.status == models.OfferStatus.pending,
    ).all()
    for offer in pending_offers:
        offer.status = models.OfferStatus.timed_out
        offer.responded_at = now

    # Mark session failed
    session.status = models.DispatchSessionStatus.failed
    session.completed_at = now

    # Get shift for timeline event + status reset
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == session.shift_request_id
    ).first()

    # Reset shift if it was stuck in dispatching
    if shift and shift.status == models.ShiftRequestStatus.dispatching:
        shift.status = models.ShiftRequestStatus.open

    # Audit trail
    db.add(models.ShiftTimelineEvent(
        shift_request_id=session.shift_request_id,
        event_type=MANUAL_SESSION_CLOSED,
        actor_user_id=admin.id,
        city_id=shift.city_id if shift else "HYD",
        payload={
            "admin_id": admin.id,
            "reason": reason or "",
            "session_id": session_id,
            "expired_offers": len(pending_offers),
        },
    ))
    db.commit()

    logger.warning(
        "[ops] admin %d closed session %d (shift %d, %d offers expired)",
        admin.id, session_id, session.shift_request_id, len(pending_offers),
    )
    return {
        "success": True,
        "session_id": session_id,
        "expired_offers": len(pending_offers),
        "message": f"Session {session_id} closed. {len(pending_offers)} pending offers expired.",
    }
