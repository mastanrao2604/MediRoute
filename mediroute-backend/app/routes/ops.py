"""
Operations Dashboard routes — manual dispatch control for ops team.
These are mandatory, not optional (Swiggy lesson — §19 in ARCHITECTURE.md).

Endpoints:
  GET  /admin/ops/live-shifts           — active shifts with dispatch state
  GET  /admin/ops/presence              — online nurses in a city/zone
  POST /admin/ops/manual-assign         — force-assign nurse to shift
  POST /admin/ops/re-dispatch/{shift_id} — restart dispatch for failed shift
  GET  /admin/ops/failed-shifts         — expired/unfilled shifts (last N hours)
  GET  /admin/ops/metrics               — live AUSFT and funnel metrics
  PATCH /admin/ops/zones/{zone_code}    — pause/resume a dispatch zone
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_admin
from .. import models
from ..ws_manager import ws_manager
from ..dispatch.engine import (
    start_dispatch, get_dispatch_metrics, dispatch_events,
    is_dispatch_enabled, set_dispatch_enabled,
)
from ..dispatch.events import MANUAL_OVERRIDE, SHIFT_CREATED, OFFER_TIMED_OUT
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

    now = datetime.utcnow()

    # Create a manual dispatch session if one doesn't exist
    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == req.shift_id
    ).first()
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
        event_type=MANUAL_OVERRIDE,
        actor_user_id=admin.id,
        city_id=shift.city_id,
        payload={"nurse_id": req.nurse_user_id, "reason": req.reason, "admin_id": admin.id},
    )
    db.add(event)
    db.commit()

    # Notify nurse via WS
    await ws_manager.send(req.nurse_user_id, {
        "type": "assignment_confirmed",
        "assignment_id": assignment.id,
        "shift_id": req.shift_id,
        "hospital_name": shift.hospital_name,
        "shift_start": shift.shift_start.isoformat(),
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
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Restart dispatch for an expired or failed shift."""
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if shift.status == models.ShiftRequestStatus.filled:
        raise HTTPException(status_code=409, detail="Shift is already filled.")

    # Reset shift to open so dispatch engine will run it
    shift.status = models.ShiftRequestStatus.open

    # Remove old dispatch session so a new one can be created
    old_session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == shift_id
    ).first()
    if old_session:
        db.delete(old_session)

    db.commit()

    # Start dispatch
    await start_dispatch(shift_id)

    logger.info("[ops] re-dispatch triggered for shift %d by admin %d", shift_id, admin.id)
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
    All data is in-memory — zero DB queries. Safe to poll every 10s.
    Exposes: WS connections, dispatch state, janitor liveness, active session count.
    """
    janitor = get_janitor_health()
    return {
        "ts": datetime.utcnow().isoformat(),
        "dispatch_enabled": is_dispatch_enabled(),
        "active_dispatch_sessions": len(dispatch_events),
        "ws_connections": ws_manager.connection_count,
        "janitor": janitor,
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
    Lightweight query — indexed on (shift_request_id, occurred_at).
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
        .all()
    )

    return {
        "shift_id": shift_id,
        "hospital_name": shift.hospital_name,
        "role": shift.role_required.value,
        "urgency": shift.urgency.value,
        "status": shift.status.value,
        "created_at": shift.created_at.isoformat() if shift.created_at else None,
        "filled_at": shift.filled_at.isoformat() if shift.filled_at else None,
        "timeline": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "occurred_at": e.occurred_at.isoformat(),
                "actor_user_id": e.actor_user_id,
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
