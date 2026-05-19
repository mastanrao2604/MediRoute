"""
Shift Request routes — hospital posts + manages instant-staffing shifts.

Endpoints:
  POST /shifts/             — hospital creates a shift request (triggers dispatch)
  GET  /shifts/             — list my shifts (hospital: own shifts; nurse: assigned)
  GET  /shifts/{shift_id}   — shift detail with dispatch status
  POST /shifts/{shift_id}/cancel     — recruiter cancels shift (stops dispatch + offers)
  POST /shifts/{shift_id}/re-dispatch — recruiter restarts dispatch (expired/cancelled/open)
  POST /shifts/{shift_id}/checkin  — nurse checks in at hospital
  POST /shifts/{shift_id}/checkout — nurse checks out (completes assignment)
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from .. import models
from ..dispatch.engine import start_dispatch, cancel_dispatch_session
from ..dispatch.events import (
    SHIFT_CREATED, SHIFT_CANCELLED, ASSIGNMENT_CHECKIN, ASSIGNMENT_CHECKOUT,
    ASSIGNMENT_COMPLETED, ASSIGNMENT_NO_SHOW, MANUAL_RETRY_TRIGGERED,
)
from ..ws_manager import ws_manager

logger = logging.getLogger(__name__)
logger.parent = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/shifts", tags=["Shifts"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ShiftCreateRequest(BaseModel):
    role_required: str
    specialty: Optional[str] = None
    hospital_name: str
    hospital_latitude: float
    hospital_longitude: float
    hospital_pincode: Optional[str] = None
    shift_start: datetime
    shift_end: Optional[datetime] = None
    urgency: str = "standard"
    pay_rate: Optional[str] = None
    notes: Optional[str] = None
    city_id: Optional[str] = "HYD"
    idempotency_key: Optional[str] = None
    dispatch_radius_km: Optional[float] = 10.0

    @field_validator("role_required")
    @classmethod
    def validate_role(cls, v):
        try:
            models.UserRole(v)
        except ValueError:
            raise ValueError(f"Invalid role: {v}")
        return v

    @field_validator("urgency")
    @classmethod
    def validate_urgency(cls, v):
        try:
            models.ShiftUrgency(v)
        except ValueError:
            raise ValueError(f"Invalid urgency: {v}")
        return v

    @field_validator("hospital_latitude")
    @classmethod
    def validate_lat(cls, v):
        if not (-90 <= v <= 90):
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("hospital_longitude")
    @classmethod
    def validate_lng(cls, v):
        if not (-180 <= v <= 180):
            raise ValueError("longitude must be between -180 and 180")
        return v

    @field_validator("hospital_pincode")
    @classmethod
    def hospital_pin_normalize(cls, v: Optional[str]) -> Optional[str]:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        clean = "".join(c for c in str(v) if c.isdigit())
        if len(clean) != 6:
            raise ValueError("hospital_pincode must be exactly 6 digits")
        return clean

    @field_validator("dispatch_radius_km")
    @classmethod
    def validate_radius(cls, v):
        if v and not (0.5 <= v <= 50):
            raise ValueError("dispatch_radius_km must be between 0.5 and 50")
        return v


class CheckInRequest(BaseModel):
    latitude: float
    longitude: float

    @field_validator("latitude")
    @classmethod
    def validate_lat(cls, v):
        if not (-90 <= v <= 90):
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_lng(cls, v):
        if not (-180 <= v <= 180):
            raise ValueError("longitude must be between -180 and 180")
        return v


def _shift_to_dict(shift: models.ShiftRequest, assignment: Optional[models.LiveAssignment] = None) -> dict:
    d = {
        "id": shift.id,
        "city_id": shift.city_id,
        "hospital_name": shift.hospital_name,
        "role_required": shift.role_required.value,
        "specialty": shift.specialty,
        "urgency": shift.urgency.value,
        "status": shift.status.value,
        "shift_start": shift.shift_start.isoformat(),
        "shift_end": shift.shift_end.isoformat() if shift.shift_end else None,
        "pay_rate": shift.pay_rate,
        "notes": shift.notes,
        "hospital_latitude": shift.hospital_latitude,
        "hospital_longitude": shift.hospital_longitude,
        "hospital_pincode": getattr(shift, "hospital_pincode", None),
        "dispatch_radius_km": shift.dispatch_radius_km,
        "filled_at": shift.filled_at.isoformat() if shift.filled_at else None,
        "created_at": shift.created_at.isoformat() if shift.created_at else None,
    }
    if assignment:
        d["assignment"] = {
            "id": assignment.id,
            "status": assignment.status.value,
            "confirmed_at": assignment.confirmed_at.isoformat() if assignment.confirmed_at else None,
            "check_in_at": assignment.check_in_at.isoformat() if assignment.check_in_at else None,
            "check_out_at": assignment.check_out_at.isoformat() if assignment.check_out_at else None,
        }
    return d


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_shift(
    req: ShiftCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Hospital creates a shift request. Triggers automatic dispatch after creation.

    Verification gate: hospital user must be verified (is_verified=True).
    The dispatch engine performs a second check — defence in depth.

    idempotency_key: provide a stable UUID from the client to prevent
    duplicate shifts on network retry. If key already exists, returns the
    existing shift (idempotent).
    """
    # Only recruiters can post shifts
    if current_user.role != models.UserRole.recruiter:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only verified hospitals (recruiter accounts) can post shifts.",
        )

    # Verification gate
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your hospital account is not verified. Submit your hospital documents to post shifts.",
        )

    # Idempotency check
    idempotency_key = req.idempotency_key or str(uuid.uuid4())
    existing = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.idempotency_key == idempotency_key
    ).first()
    if existing:
        logger.info("[shifts] duplicate request for idempotency_key=%s → returning existing shift %d",
                    idempotency_key, existing.id)
        return {"shift": _shift_to_dict(existing), "created": False}

    # Basic overlap check (FUTURE: AssignmentConflictValidator — §24.5)
    # For now: warn if shift_start is in the past
    if req.shift_start < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="shift_start cannot be in the past.",
        )

    shift = models.ShiftRequest(
        city_id=req.city_id or "HYD",
        hospital_user_id=current_user.id,
        role_required=models.UserRole(req.role_required),
        specialty=req.specialty,
        hospital_name=req.hospital_name,
        hospital_latitude=req.hospital_latitude,
        hospital_longitude=req.hospital_longitude,
        hospital_pincode=req.hospital_pincode,
        shift_start=req.shift_start,
        shift_end=req.shift_end,
        urgency=models.ShiftUrgency(req.urgency),
        pay_rate=req.pay_rate,
        notes=req.notes,
        idempotency_key=idempotency_key,
        dispatch_radius_km=req.dispatch_radius_km or 10.0,
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)

    # Timeline event
    event = models.ShiftTimelineEvent(
        shift_request_id=shift.id,
        event_type=SHIFT_CREATED,
        actor_user_id=current_user.id,
        city_id=shift.city_id,
        payload={"urgency": shift.urgency.value, "role": shift.role_required.value},
    )
    db.add(event)
    db.commit()

    logger.info(
        "[shifts] shift %d created by user %d (%s, %s, city=%s)",
        shift.id, current_user.id, shift.role_required.value, shift.urgency.value, shift.city_id
    )

    # Start dispatch in background (non-blocking asyncio task)
    background_tasks.add_task(start_dispatch, shift.id)

    return {"shift": _shift_to_dict(shift), "created": True}


@router.get("/")
def list_shifts(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List shifts for the authenticated user.
    Recruiters see their posted shifts.
    Nurses/healthcare workers see their assigned shifts.
    """
    if current_user.role == models.UserRole.recruiter:
        shifts = (
            db.query(models.ShiftRequest)
            .filter(models.ShiftRequest.hospital_user_id == current_user.id)
            .order_by(models.ShiftRequest.created_at.desc())
            .limit(50)
            .all()
        )
        return {"shifts": [_shift_to_dict(s) for s in shifts]}
    else:
        # Nurse: return assignments with shift details
        assignments = (
            db.query(models.LiveAssignment, models.ShiftRequest)
            .join(models.ShiftRequest, models.LiveAssignment.shift_request_id == models.ShiftRequest.id)
            .filter(models.LiveAssignment.nurse_user_id == current_user.id)
            .order_by(models.LiveAssignment.confirmed_at.desc())
            .limit(20)
            .all()
        )
        return {
            "shifts": [_shift_to_dict(shift, assignment) for assignment, shift in assignments]
        }


@router.get("/browse")
def browse_open_shifts(
    city_id: Optional[str] = Query(None, description="Filter by dispatch city id (e.g. HYD)"),
    role: Optional[models.UserRole] = Query(None, description="Filter by role required on the shift"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Open instant shifts for the Jobs listing (open + actively dispatching).

    Authenticated users only. Used alongside GET /jobs so nurses see both shift work and postings.
    """
    q = (
        db.query(models.ShiftRequest)
        .filter(
            models.ShiftRequest.status.in_(
                (
                    models.ShiftRequestStatus.open,
                    models.ShiftRequestStatus.dispatching,
                )
            )
        )
    )
    if city_id:
        q = q.filter(models.ShiftRequest.city_id == city_id.strip())
    if role:
        q = q.filter(models.ShiftRequest.role_required == role)
    shifts = (
        q.order_by(models.ShiftRequest.shift_start.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return {"shifts": [_shift_to_dict(s) for s in shifts]}


@router.get("/{shift_id}")
def get_shift(
    shift_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get shift detail. Hospital sees dispatch status; nurse sees assignment detail."""
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    # Access control: hospital can see own shifts; nurse can see assigned shifts
    if current_user.role == models.UserRole.recruiter:
        if shift.hospital_user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied.")
    else:
        assignment = db.query(models.LiveAssignment).filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.nurse_user_id == current_user.id,
        ).first()
        if assignment:
            return {"shift": _shift_to_dict(shift, assignment)}
        # Browse/detail: nurses may view open shifts before receiving an offer
        if shift.status in (
            models.ShiftRequestStatus.open,
            models.ShiftRequestStatus.dispatching,
        ):
            return {"shift": _shift_to_dict(shift, None)}
        raise HTTPException(status_code=403, detail="Access denied.")

    assignment = db.query(models.LiveAssignment).filter(
        models.LiveAssignment.shift_request_id == shift_id
    ).first()

    # Include dispatch session status for hospital
    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == shift_id
    ).first()

    result = _shift_to_dict(shift, assignment)
    if session:
        result["dispatch"] = {
            "session_id": session.id,
            "status": session.status.value,
            "current_wave": session.current_wave,
            "waves_exhausted": session.waves_exhausted,
        }
    return {"shift": result}


@router.post("/{shift_id}/cancel")
async def cancel_shift(
    shift_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Recruiter/hospital cancels an open/dispatching shift.

    Stops the in-process dispatch loop (waves + watchlist), marks pending offers
    cancelled, closes active dispatch session — no further WS/FCM offers go out.
    """
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id,
        models.ShiftRequest.hospital_user_id == current_user.id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if shift.status in (models.ShiftRequestStatus.filled, models.ShiftRequestStatus.cancelled):
        raise HTTPException(status_code=409, detail=f"Cannot cancel a shift with status '{shift.status.value}'.")

    now = datetime.utcnow()

    pending_rows = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.shift_request_id == shift_id,
            models.DispatchOffer.status == models.OfferStatus.pending,
        )
        .all()
    )
    nurse_ws_ids = {row.nurse_user_id for row in pending_rows}
    for row in pending_rows:
        row.status = models.OfferStatus.cancelled
        row.responded_at = now

    active_session = (
        db.query(models.DispatchSession)
        .filter(
            models.DispatchSession.shift_request_id == shift_id,
            models.DispatchSession.status == models.DispatchSessionStatus.active,
        )
        .first()
    )
    if active_session:
        active_session.status = models.DispatchSessionStatus.cancelled
        active_session.completed_at = now

    shift.status = models.ShiftRequestStatus.cancelled

    event = models.ShiftTimelineEvent(
        shift_request_id=shift_id,
        event_type=SHIFT_CANCELLED,
        actor_user_id=current_user.id,
        city_id=shift.city_id,
        payload={"cancelled_by": "hospital"},
    )
    db.add(event)
    db.commit()

    if active_session:
        cancel_dispatch_session(active_session.id)

    await ws_manager.send(shift.hospital_user_id, {
        "type": "shift_cancelled",
        "shift_id": shift_id,
        "message": "You cancelled this shift. Dispatch has stopped.",
    })

    nurse_payload = {
        "type": "offer_revoked",
        "shift_id": shift_id,
        "message": "This staffing request was cancelled.",
    }
    if nurse_ws_ids:
        await asyncio.gather(
            *[ws_manager.send(uid, nurse_payload) for uid in nurse_ws_ids],
            return_exceptions=True,
        )

    logger.info("[shifts] shift %d cancelled by user %d", shift_id, current_user.id)
    return {"cancelled": True}


@router.post("/{shift_id}/re-dispatch")
async def recruiter_redispatch_shift(
    shift_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Recruiter restarts automated dispatch after expiry/cancel/manual stop.

    Same cleanup rules as ops re-dispatch: delete old session and offers first,
    set shift open, enqueue a new dispatch run. Refuses while already dispatching.
    """
    if current_user.role != models.UserRole.recruiter:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only recruiter accounts can re-dispatch shifts.",
        )
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your hospital account is not verified.",
        )

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id,
        models.ShiftRequest.hospital_user_id == current_user.id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if shift.status == models.ShiftRequestStatus.filled:
        raise HTTPException(status_code=409, detail="Shift is already filled.")
    if shift.status == models.ShiftRequestStatus.dispatching:
        raise HTTPException(
            status_code=409,
            detail="Dispatch is already active. Cancel the shift first to stop it.",
        )

    old_session = (
        db.query(models.DispatchSession)
        .filter(models.DispatchSession.shift_request_id == shift_id)
        .first()
    )
    if old_session:
        db.query(models.DispatchOffer).filter(
            models.DispatchOffer.session_id == old_session.id
        ).delete(synchronize_session=False)
        db.delete(old_session)
        db.flush()

    shift.status = models.ShiftRequestStatus.open

    db.add(
        models.ShiftTimelineEvent(
            shift_request_id=shift_id,
            event_type=MANUAL_RETRY_TRIGGERED,
            actor_user_id=current_user.id,
            city_id=shift.city_id,
            payload={"recruiter_id": current_user.id, "trigger": "recruiter_ui"},
        )
    )
    db.commit()

    await start_dispatch(shift_id)

    logger.info("[shifts] re-dispatch for shift %d by recruiter %d", shift_id, current_user.id)
    return {"success": True, "message": f"Dispatch restarted for shift {shift_id}"}


@router.post("/{shift_id}/checkin")
def check_in(
    shift_id: int,
    req: CheckInRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Nurse checks in at hospital. Validates GPS within 200m of hospital (§21.2).
    FUTURE: QR code alternative for indoor check-in (§24.9).
    """
    from ..dispatch.engine import haversine_km

    assignment = db.query(models.LiveAssignment).filter(
        models.LiveAssignment.shift_request_id == shift_id,
        models.LiveAssignment.nurse_user_id == current_user.id,
        models.LiveAssignment.status == models.AssignmentStatus.confirmed,
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="No confirmed assignment found for this shift.")

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()

    # GPS proximity check (§21.2)
    distance_m = haversine_km(
        req.latitude, req.longitude,
        shift.hospital_latitude, shift.hospital_longitude,
    ) * 1000
    if distance_m > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You are {distance_m:.0f}m from the hospital. Check-in requires being within 200m.",
        )

    now = datetime.utcnow()
    assignment.status = models.AssignmentStatus.checked_in
    assignment.check_in_at = now
    assignment.check_in_latitude = req.latitude
    assignment.check_in_longitude = req.longitude

    event = models.ShiftTimelineEvent(
        shift_request_id=shift_id,
        event_type=ASSIGNMENT_CHECKIN,
        actor_user_id=current_user.id,
        city_id=shift.city_id,
        payload={"distance_m": round(distance_m, 1)},
    )
    db.add(event)
    db.commit()

    logger.info("[shifts] nurse %d checked in to shift %d (%.0fm from hospital)", current_user.id, shift_id, distance_m)
    return {"checked_in": True, "check_in_at": now.isoformat()}


@router.post("/{shift_id}/checkout")
def check_out(
    shift_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nurse checks out. Marks assignment completed and restores availability."""
    assignment = db.query(models.LiveAssignment).filter(
        models.LiveAssignment.shift_request_id == shift_id,
        models.LiveAssignment.nurse_user_id == current_user.id,
        models.LiveAssignment.status == models.AssignmentStatus.checked_in,
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="No checked-in assignment found for this shift.")

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()

    now = datetime.utcnow()
    assignment.status = models.AssignmentStatus.completed
    assignment.check_out_at = now

    # Update reliability score: completed_shifts +1
    rs = db.query(models.ReliabilityScore).filter(
        models.ReliabilityScore.user_id == current_user.id
    ).first()
    if rs:
        rs.completed_shifts += 1
        rs.last_calculated_at = now
    else:
        rs = models.ReliabilityScore(user_id=current_user.id, completed_shifts=1)
        db.add(rs)

    # Restore nurse availability
    presence = db.query(models.PresenceState).filter(
        models.PresenceState.user_id == current_user.id
    ).first()
    if presence and presence.state == models.PresenceStateEnum.online_busy:
        presence.state = models.PresenceStateEnum.online_available

    avail = db.query(models.NurseAvailability).filter(
        models.NurseAvailability.user_id == current_user.id
    ).first()
    if avail:
        avail.is_available = True
        avail.updated_at = now

    event = models.ShiftTimelineEvent(
        shift_request_id=shift_id,
        event_type=ASSIGNMENT_COMPLETED,
        actor_user_id=current_user.id,
        city_id=shift.city_id if shift else "HYD",
        payload={"check_in_at": assignment.check_in_at.isoformat() if assignment.check_in_at else None},
    )
    db.add(event)
    db.commit()

    logger.info("[shifts] nurse %d checked out of shift %d", current_user.id, shift_id)
    return {"completed": True, "check_out_at": now.isoformat()}
