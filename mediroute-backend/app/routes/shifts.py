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
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from .. import models
from ..dispatch.engine import (
    start_dispatch,
    cancel_dispatch_session,
    deliver_nurse_message,
    _finalize_search_closed_sync,
    _expire_shift_past_start_unfilled_sync,
)
from ..dispatch.events import (
    SHIFT_CREATED, SHIFT_CANCELLED, SHIFT_EXPIRED, ASSIGNMENT_CHECKIN, ASSIGNMENT_CHECKOUT,
    ASSIGNMENT_COMPLETED, ASSIGNMENT_NO_SHOW, MANUAL_RETRY_TRIGGERED,
    RECRUITER_ARCHIVED,
    RECRUITER_STAFF_CONFIRMED,
)
from ..ws_manager import ws_manager
from ..utils.datetime_util import to_utc_naive, utc_iso
from ..dispatch.eligibility import nurse_accept_eligible, nurse_shift_visible, normalize_pincode
from ..dispatch.offer_policy import offer_respondable, shift_search_open, nurse_blocks_other_acceptances
from ..routes.availability import DISPATCH_ELIGIBLE_ROLES
from .. import crud

logger = logging.getLogger(__name__)
logger.parent = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/shifts", tags=["Shifts"])

# Pilot: multi-nurse lifecycle incomplete — cap all staffing at one confirmed nurse.
PILOT_NURSES_REQUIRED = 1


def _pilot_nurses_required(_shift: Optional[models.ShiftRequest] = None) -> int:
    return PILOT_NURSES_REQUIRED


def _lifecycle_log(ev: str, **fields) -> None:
    """Compact shift/assignment lifecycle trace — sid/aid/uid/stage correlation keys."""
    entry = {"event": "shift.lifecycle", "ev": ev, "ts": datetime.utcnow().isoformat()}
    entry.update({k: v for k, v in fields.items() if v is not None})
    logger.info(json.dumps(entry, default=str))

_shift_cols_ready = False
_assignment_cols_ready = False

NURSE_DASHBOARD_ARCHIVED = "nurse.dashboard_archived"


def _ensure_pilot_schema(db: Session) -> None:
    """Idempotent schema guards — list/create paths must match before ORM reads/writes."""
    _ensure_shift_location_columns(db)
    _ensure_assignment_columns(db)


def _ensure_shift_location_columns(db: Session) -> None:
    """Idempotent DDL for pilot DBs where Alembic e5 did not run yet."""
    global _shift_cols_ready
    if _shift_cols_ready:
        return
    for stmt in (
        "ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS hospital_pincode VARCHAR(10)",
        "ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS hospital_locality VARCHAR(255)",
        "ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS nurses_required INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS search_closed_at TIMESTAMPTZ",
    ):
        db.execute(text(stmt))
    db.commit()
    _shift_cols_ready = True


def _ensure_assignment_columns(db: Session) -> None:
    """Idempotent DDL for recruiter confirmation + applied assignment status."""
    global _assignment_cols_ready
    if _assignment_cols_ready:
        return
    db.execute(
        text(
            "ALTER TABLE live_assignments "
            "ADD COLUMN IF NOT EXISTS recruiter_confirmed_at TIMESTAMPTZ"
        )
    )
    db.execute(
        text(
            "DO $$ BEGIN "
            "ALTER TYPE assignmentstatus ADD VALUE IF NOT EXISTS 'applied'; "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$"
        )
    )
    db.execute(
        text(
            "UPDATE live_assignments SET status = 'applied' "
            "WHERE recruiter_confirmed_at IS NULL AND status::text = 'confirmed'"
        )
    )
    db.commit()
    _assignment_cols_ready = True


def _assignment_recruiter_confirmed(
    assignment: Optional[models.LiveAssignment],
    shift: Optional[models.ShiftRequest] = None,
) -> bool:
    """True only after recruiter explicitly confirms (recruiter_confirmed_at set)."""
    if not assignment:
        return False
    return bool(getattr(assignment, "recruiter_confirmed_at", None))


def _assignment_lifecycle_stage(
    assignment: Optional[models.LiveAssignment],
    shift: Optional[models.ShiftRequest] = None,
) -> Optional[str]:
    """
    Operational lifecycle stage for API/UI (distinct from DB status during apply phase).
    invited = offer only (no assignment row).
    """
    if not assignment:
        return None
    st = assignment.status
    if st == models.AssignmentStatus.completed:
        return "completed"
    if st == models.AssignmentStatus.checked_in:
        return "checked_in"
    if st == models.AssignmentStatus.cancelled:
        if (
            shift
            and shift.status != models.ShiftRequestStatus.cancelled
            and not _assignment_recruiter_confirmed(assignment, shift)
        ):
            return "not_selected"
        return "cancelled"
    if _assignment_recruiter_confirmed(assignment, shift):
        return "recruiter_confirmed"
    if st == models.AssignmentStatus.applied:
        return "applied"
    if st == models.AssignmentStatus.confirmed:
        return "under_review"
    if st == models.AssignmentStatus.no_show:
        return "cancelled"
    return "applied"


def _assignment_is_pending_review(
    assignment: Optional[models.LiveAssignment],
    shift: Optional[models.ShiftRequest] = None,
) -> bool:
    stage = _assignment_lifecycle_stage(assignment, shift)
    return stage in ("applied", "under_review")


def _attach_hospital_contact(
    d: dict,
    shift: models.ShiftRequest,
    db: Session,
    *,
    full_contact: bool,
) -> None:
    """Hospital/recruiter contact for job seeker shift views."""
    recruiter = (
        db.query(models.User)
        .filter(models.User.id == shift.hospital_user_id)
        .first()
    )
    contact = {
        "hospital_name": shift.hospital_name,
        "company_name": getattr(recruiter, "company_name", None) if recruiter else None,
        "locality": getattr(shift, "hospital_locality", None),
        "city_id": shift.city_id,
    }
    if full_contact and recruiter and recruiter.phone:
        contact["phone"] = recruiter.phone
    d["hospital_contact"] = contact


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ShiftCreateRequest(BaseModel):
    role_required: str
    specialty: Optional[str] = None
    hospital_name: str
    hospital_latitude: float
    hospital_longitude: float
    hospital_pincode: Optional[str] = None
    hospital_locality: Optional[str] = None
    shift_start: datetime
    shift_end: Optional[datetime] = None
    urgency: str = "standard"
    pay_rate: Optional[str] = None
    notes: Optional[str] = None
    city_id: Optional[str] = "HYD"
    idempotency_key: Optional[str] = None
    dispatch_radius_km: Optional[float] = 10.0
    nurses_required: Optional[int] = 1

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

    @field_validator("nurses_required")
    @classmethod
    def pilot_single_nurse(cls, v):
        if v is not None and int(v) > PILOT_NURSES_REQUIRED:
            raise ValueError(
                f"Pilot supports {PILOT_NURSES_REQUIRED} nurse per shift only."
            )
        return PILOT_NURSES_REQUIRED


class CancelShiftRequest(BaseModel):
    reason: Optional[str] = None


class ConfirmStaffRequest(BaseModel):
    nurse_user_id: int


class ShiftUpdateRequest(BaseModel):
    """Recruiter edits while shift is still open or dispatching (not after fill)."""
    urgency: Optional[str] = None
    shift_start: Optional[datetime] = None
    shift_end: Optional[datetime] = None
    notes: Optional[str] = None
    pay_rate: Optional[str] = None
    specialty: Optional[str] = None
    dispatch_radius_km: Optional[float] = None

    @field_validator("urgency")
    @classmethod
    def validate_urgency(cls, v):
        if v is None:
            return v
        try:
            models.ShiftUrgency(v)
        except ValueError:
            raise ValueError(f"Invalid urgency: {v}")
        return v

    @field_validator("dispatch_radius_km")
    @classmethod
    def validate_radius(cls, v):
        if v is None:
            return v
        if not (0.5 <= v <= 50):
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


def _shift_start_utc_naive(shift_start: datetime) -> datetime:
    if shift_start.tzinfo:
        return shift_start.astimezone(timezone.utc).replace(tzinfo=None)
    return shift_start


def _expire_shift_if_past_start_unfilled(db: Session, shift: models.ShiftRequest) -> bool:
    """
    If shift start passed with no recruiter-confirmed nurse, mark expired and stop offers.
    Safe to call on list/detail reads (idempotent for terminal shifts).
    """
    return _expire_shift_past_start_unfilled_sync(db, shift)


def _stop_all_dispatch_for_shift(db: Session, shift: models.ShiftRequest) -> None:
    """Stop in-flight search (DB + engine) so re-post can start cleanly."""
    now = datetime.utcnow()
    sessions = (
        db.query(models.DispatchSession)
        .filter(models.DispatchSession.shift_request_id == shift.id)
        .all()
    )
    for session in sessions:
        cancel_dispatch_session(session.id)
        if session.status == models.DispatchSessionStatus.active:
            session.status = models.DispatchSessionStatus.failed
            session.completed_at = now

    pending_rows = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.shift_request_id == shift.id,
            models.DispatchOffer.status == models.OfferStatus.pending,
        )
        .all()
    )
    for row in pending_rows:
        row.status = models.OfferStatus.timed_out
        row.responded_at = now

    if shift.status == models.ShiftRequestStatus.dispatching:
        shift.status = models.ShiftRequestStatus.open


def _schedule_shift_expired_notifications(
    shift: models.ShiftRequest,
    nurse_user_ids: set,
) -> None:
    """Best-effort WS sync when expiry is detected on a read path."""

    async def _send_all() -> None:
        hospital_msg = {
            "type": "shift_expired",
            "shift_id": shift.id,
            "message": "No staff confirmed before the shift start time.",
        }
        await ws_manager.send(shift.hospital_user_id, hospital_msg)
        if nurse_user_ids:
            revoked = {
                "type": "offer_revoked",
                "shift_id": shift.id,
                "message": "This shift expired and is no longer available.",
            }
            await asyncio.gather(
                *[ws_manager.send(uid, revoked) for uid in nurse_user_ids],
                return_exceptions=True,
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_all())
    except RuntimeError:
        pass


def _recruiter_archived_shift_ids(db: Session, recruiter_user_id: int) -> set:
    rows = (
        db.query(models.ShiftTimelineEvent.shift_request_id)
        .join(
            models.ShiftRequest,
            models.ShiftTimelineEvent.shift_request_id == models.ShiftRequest.id,
        )
        .filter(
            models.ShiftRequest.hospital_user_id == recruiter_user_id,
            models.ShiftTimelineEvent.event_type == RECRUITER_ARCHIVED,
        )
        .all()
    )
    return {r[0] for r in rows}


def _nurse_dashboard_archived_ids(db: Session, nurse_user_id: int) -> set:
    rows = (
        db.query(models.ShiftTimelineEvent.shift_request_id)
        .join(
            models.LiveAssignment,
            models.LiveAssignment.shift_request_id == models.ShiftTimelineEvent.shift_request_id,
        )
        .filter(
            models.LiveAssignment.nurse_user_id == nurse_user_id,
            models.ShiftTimelineEvent.event_type == NURSE_DASHBOARD_ARCHIVED,
            models.ShiftTimelineEvent.actor_user_id == nurse_user_id,
        )
        .all()
    )
    return {r[0] for r in rows}


def _nurse_may_dismiss_dashboard(
    assignment: models.LiveAssignment,
    shift: models.ShiftRequest,
) -> bool:
    """Block removal for operational (confirmed / on-site) assignments."""
    if assignment.status == models.AssignmentStatus.checked_in:
        return False
    if _assignment_recruiter_confirmed(assignment, shift):
        return False
    stage = _assignment_lifecycle_stage(assignment, shift)
    if stage in ("recruiter_confirmed", "checked_in"):
        return False
    return True


def _attach_recruiter_staffing(d: dict, shift: models.ShiftRequest, db: Session) -> dict:
    """Applicants + staffing progress for recruiter UI."""
    assignments = (
        db.query(models.LiveAssignment)
        .filter(models.LiveAssignment.shift_request_id == shift.id)
        .order_by(models.LiveAssignment.confirmed_at.asc())
        .all()
    )
    applicants = []
    for a in assignments:
        card = _assigned_nurse_summary(db, a.nurse_user_id)
        stage = _assignment_lifecycle_stage(a, shift)
        card["lifecycle_stage"] = stage
        card["status"] = (
            "confirmed" if _assignment_recruiter_confirmed(a, shift) else "applied"
        )
        card["assignment_id"] = a.id
        card["assignment_status"] = a.status.value
        card["check_in_at"] = utc_iso(a.check_in_at) if a.check_in_at else None
        card["check_out_at"] = utc_iso(a.check_out_at) if a.check_out_at else None
        applicants.append(card)

    pending_offers = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.shift_request_id == shift.id,
            models.DispatchOffer.status == models.OfferStatus.pending,
        )
        .order_by(models.DispatchOffer.offered_at.asc())
        .all()
    )
    assignment_nurse_ids = {a.nurse_user_id for a in assignments}
    for offer in pending_offers:
        if offer.nurse_user_id in assignment_nurse_ids:
            continue
        card = _assigned_nurse_summary(db, offer.nurse_user_id)
        card["lifecycle_stage"] = "invited"
        card["status"] = "waiting"
        card["offer_id"] = offer.id
        applicants.append(card)

    pending_responses = sum(
        1 for o in pending_offers if o.nurse_user_id not in assignment_nurse_ids
    )
    nurses_required = _pilot_nurses_required(shift)
    d["applicants"] = applicants
    d["confirmed_count"] = sum(
        1 for a in assignments if _assignment_recruiter_confirmed(a, shift)
    )
    d["applied_count"] = sum(
        1 for a in assignments if not _assignment_recruiter_confirmed(a, shift)
    )
    d["nurses_required"] = nurses_required
    d["pending_responses"] = pending_responses
    d["search_active"] = shift_search_open(shift)
    if assignments and not d.get("assignment"):
        a0 = assignments[0]
        recruiter_ok = _assignment_recruiter_confirmed(a0, shift)
        d["assignment"] = {
            "id": a0.id,
            "nurse_user_id": a0.nurse_user_id,
            "status": a0.status.value,
            "lifecycle_stage": _assignment_lifecycle_stage(a0, shift),
            "recruiter_confirmed": recruiter_ok,
            "application_status": (
                "recruiter_confirmed" if recruiter_ok else "under_review"
            ),
            "confirmed_at": a0.confirmed_at.isoformat() if a0.confirmed_at else None,
            "recruiter_confirmed_at": utc_iso(getattr(a0, "recruiter_confirmed_at", None)),
            "check_in_at": a0.check_in_at.isoformat() if a0.check_in_at else None,
            "check_out_at": a0.check_out_at.isoformat() if a0.check_out_at else None,
        }
        d["assigned_nurse"] = _assigned_nurse_summary(db, a0.nurse_user_id)
    return d


def _reliability_display_score(rs: Optional[models.ReliabilityScore]) -> float:
    """New users show 100% until enough offer history to score fairly."""
    if not rs or rs.score is None:
        return 100.0
    if (rs.total_offers or 0) <= 2:
        return 100.0
    return round(float(rs.score), 1)


def _assigned_nurse_summary(db: Session, nurse_user_id: int) -> dict:
    """Staff card for recruiter — contact + profile fields only."""
    user = db.query(models.User).filter(models.User.id == nurse_user_id).first()
    profile = crud.get_profile(db, nurse_user_id)
    rs = (
        db.query(models.ReliabilityScore)
        .filter(models.ReliabilityScore.user_id == nurse_user_id)
        .first()
    )
    name = (user.name if user and user.name else None) or f"Staff #{nurse_user_id}"
    return {
        "user_id": nurse_user_id,
        "name": name,
        "phone": user.phone if user else None,
        "role": user.role.value if user else None,
        "experience_years": profile.experience_years if profile else None,
        "skills": profile.skills if profile else None,
        "education": profile.education if profile else None,
        "service_locality": profile.service_locality if profile else None,
        "rating": _reliability_display_score(rs),
        "completed_shifts": int(rs.completed_shifts) if rs else 0,
    }


def _shift_cancel_reason(db: Session, shift_id: int) -> Optional[str]:
    ev = (
        db.query(models.ShiftTimelineEvent)
        .filter(
            models.ShiftTimelineEvent.shift_request_id == shift_id,
            models.ShiftTimelineEvent.event_type == SHIFT_CANCELLED,
        )
        .order_by(models.ShiftTimelineEvent.occurred_at.desc())
        .first()
    )
    if not ev or not isinstance(ev.payload, dict):
        return None
    reason = ev.payload.get("reason")
    if reason and isinstance(reason, str):
        trimmed = reason.strip()
        return trimmed[:500] if trimmed else None
    return None


def _shift_to_dict(
    shift: models.ShiftRequest,
    assignment: Optional[models.LiveAssignment] = None,
    db: Optional[Session] = None,
) -> dict:
    d = {
        "id": shift.id,
        "city_id": shift.city_id,
        "hospital_name": shift.hospital_name,
        "role_required": shift.role_required.value,
        "specialty": shift.specialty,
        "urgency": shift.urgency.value,
        "status": shift.status.value,
        "shift_start": utc_iso(shift.shift_start),
        "shift_end": utc_iso(shift.shift_end),
        "pay_rate": shift.pay_rate,
        "notes": shift.notes,
        "hospital_latitude": shift.hospital_latitude,
        "hospital_longitude": shift.hospital_longitude,
        "hospital_pincode": getattr(shift, "hospital_pincode", None),
        "hospital_locality": getattr(shift, "hospital_locality", None),
        "dispatch_radius_km": shift.dispatch_radius_km,
        "nurses_required": _pilot_nurses_required(shift),
        "search_closed": bool(getattr(shift, "search_closed_at", None)),
        "search_closed_at": utc_iso(getattr(shift, "search_closed_at", None)),
        "filled_at": utc_iso(shift.filled_at),
        "created_at": utc_iso(shift.created_at),
    }
    if assignment:
        recruiter_ok = _assignment_recruiter_confirmed(assignment, shift)
        stage = _assignment_lifecycle_stage(assignment, shift)
        d["assignment"] = {
            "id": assignment.id,
            "nurse_user_id": assignment.nurse_user_id,
            "status": assignment.status.value,
            "lifecycle_stage": stage,
            "recruiter_confirmed": recruiter_ok,
            "application_status": (
                "recruiter_confirmed" if recruiter_ok else "under_review"
            ),
            "confirmed_at": assignment.confirmed_at.isoformat() if assignment.confirmed_at else None,
            "recruiter_confirmed_at": utc_iso(
                getattr(assignment, "recruiter_confirmed_at", None)
            ),
            "check_in_at": assignment.check_in_at.isoformat() if assignment.check_in_at else None,
            "check_out_at": assignment.check_out_at.isoformat() if assignment.check_out_at else None,
        }
    if shift.status == models.ShiftRequestStatus.cancelled and db is not None:
        d["cancellation_reason"] = _shift_cancel_reason(db, shift.id)
    return d


def _attach_nurse_shift_context(
    d: dict,
    shift: models.ShiftRequest,
    nurse_user_id: int,
    nurse_role: models.UserRole,
    nurse_city_id: str,
    nurse_pincode: Optional[str],
    db: Session,
) -> dict:
    """Add nearby_match + my_offer + accept eligibility for employee browse/detail."""
    accept_ok, dist_km, block_msg = nurse_accept_eligible(db, shift, nurse_user_id)
    d["accept_eligible"] = accept_ok
    d["distance_km"] = dist_km
    d["nearby_match"] = accept_ok
    if block_msg and not accept_ok:
        d["accept_blocked_message"] = block_msg
    offer = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.shift_request_id == shift.id,
            models.DispatchOffer.nurse_user_id == nurse_user_id,
        )
        .order_by(models.DispatchOffer.id.desc())
        .first()
    )
    if offer:
        d["my_offer"] = {
            "offer_id": offer.id,
            "status": offer.status.value,
            "respondable": offer_respondable(offer, shift),
            "accept_eligible": accept_ok,
            "distance_km": dist_km,
        }
    return d


def _nurse_browse_context(db: Session, user: models.User):
    avail = (
        db.query(models.NurseAvailability)
        .filter(models.NurseAvailability.user_id == user.id)
        .first()
    )
    city_id = (avail.city_id if avail else None) or "HYD"
    prof = crud.get_profile(db, user.id)
    nurse_pc = normalize_pincode(getattr(prof, "service_pincode", None) if prof else None)
    return city_id, nurse_pc


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

    try:
        _ensure_pilot_schema(db)
        shift = models.ShiftRequest(
            city_id=req.city_id or "HYD",
            hospital_user_id=current_user.id,
            role_required=models.UserRole(req.role_required),
            specialty=req.specialty,
            hospital_name=req.hospital_name,
            hospital_latitude=req.hospital_latitude,
            hospital_longitude=req.hospital_longitude,
            hospital_pincode=req.hospital_pincode,
            hospital_locality=(req.hospital_locality or "").strip() or None,
            shift_start=to_utc_naive(req.shift_start),
            shift_end=to_utc_naive(req.shift_end) if req.shift_end else None,
            urgency=models.ShiftUrgency(req.urgency),
            pay_rate=req.pay_rate,
            notes=req.notes,
            idempotency_key=idempotency_key,
            dispatch_radius_km=req.dispatch_radius_km or 10.0,
            nurses_required=PILOT_NURSES_REQUIRED,
        )
        db.add(shift)
        db.commit()
        db.refresh(shift)

        event = models.ShiftTimelineEvent(
            shift_request_id=shift.id,
            event_type=SHIFT_CREATED,
            actor_user_id=current_user.id,
            city_id=shift.city_id,
            payload={"urgency": shift.urgency.value, "role": shift.role_required.value},
        )
        db.add(event)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("[shifts] create_shift DB error user=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save shift. Please retry in a moment.",
        ) from exc

    logger.info(
        "[shifts] shift %d created by user %d (%s, %s, city=%s)",
        shift.id, current_user.id, shift.role_required.value, shift.urgency.value, shift.city_id
    )
    _lifecycle_log(
        "shift_created",
        sid=shift.id,
        uid=current_user.id,
        actor="recruiter",
        urgency=shift.urgency.value,
        role=shift.role_required.value,
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
    _ensure_pilot_schema(db)
    if current_user.role == models.UserRole.recruiter:
        archived = _recruiter_archived_shift_ids(db, current_user.id)
        shifts = (
            db.query(models.ShiftRequest)
            .filter(models.ShiftRequest.hospital_user_id == current_user.id)
            .order_by(models.ShiftRequest.created_at.desc())
            .limit(50)
            .all()
        )
        visible = [s for s in shifts if s.id not in archived]
        for s in visible:
            if _expire_shift_if_past_start_unfilled(db, s):
                db.refresh(s)
        payload = []
        for s in visible:
            primary = (
                db.query(models.LiveAssignment)
                .filter(models.LiveAssignment.shift_request_id == s.id)
                .order_by(models.LiveAssignment.confirmed_at.asc())
                .first()
            )
            d = _shift_to_dict(s, primary)
            _attach_recruiter_staffing(d, s, db)
            payload.append(d)
        return {"shifts": payload}
    else:
        archived = _nurse_dashboard_archived_ids(db, current_user.id)
        # Nurse: return assignments with shift details
        assignments = (
            db.query(models.LiveAssignment, models.ShiftRequest)
            .join(models.ShiftRequest, models.LiveAssignment.shift_request_id == models.ShiftRequest.id)
            .filter(models.LiveAssignment.nurse_user_id == current_user.id)
            .order_by(models.LiveAssignment.confirmed_at.desc())
            .limit(20)
            .all()
        )
        payload = []
        for assignment, shift in assignments:
            if shift.id in archived:
                continue
            if _expire_shift_if_past_start_unfilled(db, shift):
                db.refresh(shift)
                db.refresh(assignment)
            payload.append(_shift_to_dict(shift, assignment, db))
        return {"shifts": payload}


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
    Open instant shifts for the Jobs listing (open + actively finding staff).

    Nurses: shifts in their role + city (Phase 1: all visible; accept within 50 km).
    Includes my_offer when the hospital has already notified them.
    """
    now = datetime.utcnow()
    q = (
        db.query(models.ShiftRequest)
        .filter(
            models.ShiftRequest.status.in_(
                (
                    models.ShiftRequestStatus.open,
                    models.ShiftRequestStatus.dispatching,
                )
            ),
            models.ShiftRequest.shift_start > now,
        )
    )

    if current_user.role in DISPATCH_ELIGIBLE_ROLES:
        nurse_city, nurse_pc = _nurse_browse_context(db, current_user)
        effective_role = role or current_user.role
        effective_city = (city_id.strip() if city_id else nurse_city)
        q = q.filter(
            models.ShiftRequest.city_id == effective_city,
            models.ShiftRequest.role_required == effective_role,
        )
        shifts = q.order_by(models.ShiftRequest.shift_start.asc()).offset(skip).limit(limit * 3).all()
        # Phase 1: show all city+role shifts. Phase 2 TODO: filter by notification radius.
        eligible = [
            s for s in shifts
            if nurse_shift_visible(s, current_user.role, effective_city)
        ]
        eligible = eligible[skip : skip + limit]
        payload = []
        for s in eligible:
            if _expire_shift_if_past_start_unfilled(db, s):
                db.refresh(s)
            if s.status not in (
                models.ShiftRequestStatus.open,
                models.ShiftRequestStatus.dispatching,
            ):
                continue
            if _shift_start_utc_naive(s.shift_start) <= now:
                continue
            if db.query(models.LiveAssignment).filter(
                models.LiveAssignment.shift_request_id == s.id,
                models.LiveAssignment.nurse_user_id == current_user.id,
                models.LiveAssignment.status != models.AssignmentStatus.cancelled,
            ).first():
                continue
            if db.query(models.DispatchOffer).filter(
                models.DispatchOffer.shift_request_id == s.id,
                models.DispatchOffer.nurse_user_id == current_user.id,
                models.DispatchOffer.status == models.OfferStatus.accepted,
            ).first():
                continue
            d = _shift_to_dict(s)
            _attach_nurse_shift_context(
                d, s, current_user.id, current_user.role, effective_city, nurse_pc, db
            )
            payload.append(d)
        logger.info(
            "[browse] nurse %d role=%s city=%s pin=%s → %d/%d eligible shifts",
            current_user.id,
            effective_role.value,
            effective_city,
            nurse_pc or "—",
            len(payload),
            len(shifts),
        )
        return {"shifts": payload}

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

    if _expire_shift_if_past_start_unfilled(db, shift):
        db.refresh(shift)

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
            d = _shift_to_dict(shift, assignment, db)
            _attach_hospital_contact(
                d,
                shift,
                db,
                full_contact=_assignment_recruiter_confirmed(assignment, shift),
            )
            return {"shift": d}
        # Browse/detail: nurses may view open shifts before receiving an offer
        if shift.status in (
            models.ShiftRequestStatus.open,
            models.ShiftRequestStatus.dispatching,
        ) and _shift_start_utc_naive(shift.shift_start) > datetime.utcnow():
            d = _shift_to_dict(shift, None)
            if current_user.role in DISPATCH_ELIGIBLE_ROLES:
                nurse_city, nurse_pc = _nurse_browse_context(db, current_user)
                _attach_nurse_shift_context(
                    d, shift, current_user.id, current_user.role, nurse_city, nurse_pc, db
                )
            return {"shift": d}
        raise HTTPException(
            status_code=404,
            detail="This shift is no longer available.",
        )

    assignment = db.query(models.LiveAssignment).filter(
        models.LiveAssignment.shift_request_id == shift_id
    ).first()

    # Include dispatch session status for hospital
    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == shift_id
    ).first()

    result = _shift_to_dict(shift, assignment)
    _attach_recruiter_staffing(result, shift, db)
    if session:
        result["dispatch"] = {
            "session_id": session.id,
            "status": session.status.value,
            "current_wave": session.current_wave,
            "waves_exhausted": session.waves_exhausted,
        }
    return {"shift": result}


@router.patch("/{shift_id}")
def update_shift(
    shift_id: int,
    req: ShiftUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Recruiter updates an open/dispatching shift (blocked after fill/cancel/expired)."""
    if current_user.role != models.UserRole.recruiter:
        raise HTTPException(status_code=403, detail="Only recruiters can update shifts.")

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id,
        models.ShiftRequest.hospital_user_id == current_user.id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if shift.status not in (
        models.ShiftRequestStatus.open,
        models.ShiftRequestStatus.dispatching,
        models.ShiftRequestStatus.expired,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot edit a shift with status '{shift.status.value}'.",
        )

    if req.shift_start is not None:
        incoming = req.shift_start
        if incoming.tzinfo:
            incoming = incoming.astimezone(timezone.utc).replace(tzinfo=None)
        current = _shift_start_utc_naive(shift.shift_start)
        start_changed = abs((incoming - current).total_seconds()) > 60
        if incoming < datetime.utcnow() and (
            shift.status in (
                models.ShiftRequestStatus.open,
                models.ShiftRequestStatus.dispatching,
            )
            or (shift.status == models.ShiftRequestStatus.expired and start_changed)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please choose a new future shift time to repost this requirement.",
            )

    if req.urgency is not None:
        shift.urgency = models.ShiftUrgency(req.urgency)
    if req.shift_start is not None:
        shift.shift_start = to_utc_naive(req.shift_start)
    if req.shift_end is not None:
        shift.shift_end = to_utc_naive(req.shift_end)
    if req.notes is not None:
        shift.notes = req.notes.strip() or None
    if req.pay_rate is not None:
        shift.pay_rate = req.pay_rate.strip() or None
    if req.specialty is not None:
        shift.specialty = req.specialty.strip() or None
    if req.dispatch_radius_km is not None:
        shift.dispatch_radius_km = req.dispatch_radius_km

    if (
        shift.status == models.ShiftRequestStatus.expired
        and _shift_start_utc_naive(shift.shift_start) > datetime.utcnow()
    ):
        shift.status = models.ShiftRequestStatus.open

    db.commit()
    db.refresh(shift)
    logger.info("[shifts] shift %d updated by recruiter %d", shift_id, current_user.id)
    return {"shift": _shift_to_dict(shift)}


@router.post("/{shift_id}/archive")
def archive_shift(
    shift_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hide a cancelled/expired shift from the recruiter dashboard (timeline audit kept)."""
    if current_user.role != models.UserRole.recruiter:
        raise HTTPException(status_code=403, detail="Only recruiters can archive shifts.")

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id,
        models.ShiftRequest.hospital_user_id == current_user.id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    now = datetime.now(timezone.utc)
    shift_start = shift.shift_start
    if shift_start.tzinfo:
        shift_start = shift_start.astimezone(timezone.utc).replace(tzinfo=None)
    past_start = shift_start < datetime.utcnow()

    if shift.status in (models.ShiftRequestStatus.open, models.ShiftRequestStatus.dispatching):
        if past_start:
            shift.status = models.ShiftRequestStatus.expired
            db.commit()
        else:
            raise HTTPException(
                status_code=409,
                detail="Cannot remove an active shift. Cancel it first or wait until it expires.",
            )
    elif shift.status not in (
        models.ShiftRequestStatus.cancelled,
        models.ShiftRequestStatus.expired,
        models.ShiftRequestStatus.filled,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot remove a shift with status '{shift.status.value}'.",
        )

    existing = db.query(models.ShiftTimelineEvent).filter(
        models.ShiftTimelineEvent.shift_request_id == shift_id,
        models.ShiftTimelineEvent.event_type == RECRUITER_ARCHIVED,
    ).first()
    if not existing:
        db.add(models.ShiftTimelineEvent(
            shift_request_id=shift_id,
            event_type=RECRUITER_ARCHIVED,
            actor_user_id=current_user.id,
            city_id=shift.city_id,
            payload={"archived_by": "recruiter"},
        ))
        db.commit()

    return {"success": True, "shift_id": shift_id}


@router.post("/{shift_id}/dismiss")
def nurse_dismiss_shift_from_dashboard(
    shift_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hide a past/inactive assignment from the nurse dashboard (audit kept in timeline)."""
    if current_user.role not in DISPATCH_ELIGIBLE_ROLES:
        raise HTTPException(status_code=403, detail="Only staff accounts can dismiss shifts.")

    _ensure_pilot_schema(db)
    assignment = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.nurse_user_id == current_user.id,
        )
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found.")

    shift = db.query(models.ShiftRequest).filter(models.ShiftRequest.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    if not _nurse_may_dismiss_dashboard(assignment, shift):
        raise HTTPException(
            status_code=409,
            detail="Cannot remove an active confirmed or on-site shift.",
        )

    existing = db.query(models.ShiftTimelineEvent).filter(
        models.ShiftTimelineEvent.shift_request_id == shift_id,
        models.ShiftTimelineEvent.event_type == NURSE_DASHBOARD_ARCHIVED,
        models.ShiftTimelineEvent.actor_user_id == current_user.id,
    ).first()
    if not existing:
        db.add(
            models.ShiftTimelineEvent(
                shift_request_id=shift_id,
                event_type=NURSE_DASHBOARD_ARCHIVED,
                actor_user_id=current_user.id,
                city_id=shift.city_id,
                payload={
                    "assignment_status": assignment.status.value,
                    "lifecycle_stage": _assignment_lifecycle_stage(assignment, shift),
                },
            )
        )
        db.commit()

    _lifecycle_log(
        "nurse_dashboard_dismiss",
        sid=shift_id,
        aid=assignment.id,
        uid=current_user.id,
        actor="nurse",
        stage=_assignment_lifecycle_stage(assignment, shift),
    )
    return {"dismissed": True, "shift_id": shift_id}


@router.post("/{shift_id}/confirm-staff")
async def confirm_staff(
    shift_id: int,
    req: ConfirmStaffRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Recruiter selects a nurse who already accepted and closes the shift for applications.
    Cancels other applicants, stops dispatch, and notifies both sides in real time.
    """
    if current_user.role != models.UserRole.recruiter:
        raise HTTPException(status_code=403, detail="Only recruiters can confirm staff.")

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id,
        models.ShiftRequest.hospital_user_id == current_user.id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    nurse_id = req.nurse_user_id
    assignment = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.nurse_user_id == nurse_id,
            models.LiveAssignment.status.in_([
                models.AssignmentStatus.applied,
                models.AssignmentStatus.confirmed,
                models.AssignmentStatus.checked_in,
            ]),
        )
        .first()
    )
    if not assignment:
        pending = db.query(models.DispatchOffer).filter(
            models.DispatchOffer.shift_request_id == shift_id,
            models.DispatchOffer.nurse_user_id == nurse_id,
            models.DispatchOffer.status == models.OfferStatus.pending,
        ).first()
        if pending:
            raise HTTPException(
                status_code=400,
                detail="This applicant has not applied yet. Wait for them to apply first.",
            )
        raise HTTPException(status_code=404, detail="Applicant not found for this shift.")

    _ensure_assignment_columns(db)

    already_confirmed_other = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.recruiter_confirmed_at.isnot(None),
            models.LiveAssignment.nurse_user_id != nurse_id,
        )
        .first()
    )
    if already_confirmed_other:
        _lifecycle_log(
            "recruiter_confirm_rejected",
            sid=shift_id,
            uid=nurse_id,
            actor="recruiter",
            reason="pilot_single_nurse",
        )
        raise HTTPException(
            status_code=400,
            detail="This shift already has confirmed staff. Pilot supports one nurse per shift.",
        )

    if _assignment_recruiter_confirmed(assignment, shift) and getattr(
        shift, "search_closed_at", None
    ):
        confirmed = (
            db.query(models.LiveAssignment)
            .filter(
                models.LiveAssignment.shift_request_id == shift_id,
                models.LiveAssignment.recruiter_confirmed_at.isnot(None),
            )
            .count()
        )
        return {
            "confirmed": True,
            "shift_id": shift_id,
            "nurse_user_id": nurse_id,
            "confirmed_count": confirmed,
            "search_closed": True,
        }

    _lifecycle_log(
        "recruiter_confirm_start",
        sid=shift_id,
        aid=assignment.id,
        uid=nurse_id,
        actor="recruiter",
        stage=_assignment_lifecycle_stage(assignment, shift),
    )

    now = datetime.utcnow()
    if not getattr(assignment, "recruiter_confirmed_at", None):
        assignment.recruiter_confirmed_at = now
    if assignment.status == models.AssignmentStatus.applied:
        assignment.status = models.AssignmentStatus.confirmed

    other_assignments = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.nurse_user_id != nurse_id,
            models.LiveAssignment.status.in_([
                models.AssignmentStatus.applied,
                models.AssignmentStatus.confirmed,
                models.AssignmentStatus.checked_in,
            ]),
        )
        .all()
    )
    not_selected_ids = []
    for other in other_assignments:
        other.status = models.AssignmentStatus.cancelled
        not_selected_ids.append(other.nurse_user_id)

    _stop_all_dispatch_for_shift(db, shift)
    _finalize_search_closed_sync(db, shift_id, now, "recruiter_confirm")
    db.refresh(shift)

    nurse = db.query(models.User).filter(models.User.id == nurse_id).first()
    nurse_name = (nurse.name if nurse and nurse.name else None) or f"Staff #{nurse_id}"

    db.add(
        models.ShiftTimelineEvent(
            shift_request_id=shift_id,
            event_type=RECRUITER_STAFF_CONFIRMED,
            actor_user_id=current_user.id,
            city_id=shift.city_id,
            payload={
                "nurse_user_id": nurse_id,
                "not_selected": not_selected_ids,
            },
        )
    )
    db.commit()

    confirmed_count = 1
    await deliver_nurse_message(db, nurse_id, {
        "type": "assignment_confirmed",
        "shift_id": shift_id,
        "assignment_id": assignment.id,
        "hospital_name": shift.hospital_name,
        "shift_start": utc_iso(shift.shift_start),
        "application_status": "recruiter_confirmed",
        "lifecycle_stage": "recruiter_confirmed",
        "message": "Shift confirmed — the hospital selected you. Get ready for your shift.",
    })
    for uid in not_selected_ids:
        await deliver_nurse_message(db, uid, {
            "type": "offer_revoked",
            "shift_id": shift_id,
            "lifecycle_stage": "not_selected",
            "message": "Position filled — another staff member was selected for this shift.",
        })
    await ws_manager.send(shift.hospital_user_id, {
        "type": "shift_filled",
        "shift_id": shift_id,
        "nurse_name": nurse_name,
        "nurse_user_id": nurse_id,
        "confirmed_count": confirmed_count,
        "message": f"{nurse_name} confirmed · applications closed.",
    })
    await ws_manager.send(shift.hospital_user_id, {
        "type": "shift_search_stopped",
        "shift_id": shift_id,
        "confirmed_count": confirmed_count,
        "message": "Applications closed — no new staff can apply.",
    })

    logger.info(
        "[shifts] shift %d staff confirmed nurse=%d by recruiter %d",
        shift_id, nurse_id, current_user.id,
    )
    _lifecycle_log(
        "recruiter_confirmed",
        sid=shift_id,
        aid=assignment.id,
        uid=nurse_id,
        actor="recruiter",
        stage="recruiter_confirmed",
        not_selected=len(not_selected_ids),
    )
    return {
        "confirmed": True,
        "shift_id": shift_id,
        "nurse_user_id": nurse_id,
        "confirmed_count": confirmed_count,
        "search_closed": True,
    }


@router.post("/{shift_id}/stop-search")
async def stop_shift_search(
    shift_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Recruiter manually closes staff search; confirmed nurses remain booked."""
    if current_user.role != models.UserRole.recruiter:
        raise HTTPException(status_code=403, detail="Only recruiters can stop staff search.")

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id,
        models.ShiftRequest.hospital_user_id == current_user.id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if getattr(shift, "search_closed_at", None):
        confirmed = (
            db.query(models.LiveAssignment)
            .filter(
                models.LiveAssignment.shift_request_id == shift_id,
                models.LiveAssignment.recruiter_confirmed_at.isnot(None),
            )
            .count()
        )
        return {"stopped": True, "confirmed_count": confirmed}

    _stop_all_dispatch_for_shift(db, shift)
    now = datetime.utcnow()
    _finalize_search_closed_sync(db, shift_id, now, "manual")
    db.refresh(shift)

    confirmed = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.recruiter_confirmed_at.isnot(None),
        )
        .count()
    )
    await ws_manager.send(shift.hospital_user_id, {
        "type": "shift_search_stopped",
        "shift_id": shift_id,
        "confirmed_count": confirmed,
        "message": "Staff search paused — no new applications.",
    })
    if confirmed > 0:
        await ws_manager.send(shift.hospital_user_id, {
            "type": "shift_filled",
            "shift_id": shift_id,
            "confirmed_count": confirmed,
            "message": f"{confirmed} nurse confirmed for this shift.",
        })

    logger.info("[shifts] shift %d search stopped by recruiter %d", shift_id, current_user.id)
    _lifecycle_log(
        "search_stopped",
        sid=shift_id,
        uid=current_user.id,
        actor="recruiter",
        confirmed=confirmed,
    )
    return {"stopped": True, "confirmed_count": confirmed}


@router.post("/{shift_id}/cancel")
async def cancel_shift(
    shift_id: int,
    body: Optional[CancelShiftRequest] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Recruiter/hospital cancels an open/dispatching shift.

    Stops the in-process dispatch loop (waves + watchlist), marks pending offers
    cancelled, releases nurse applications, closes active dispatch session.
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
    cancel_reason = None
    if body and body.reason:
        trimmed = body.reason.strip()
        if trimmed:
            cancel_reason = trimmed[:500]

    nurse_ws_ids: set[int] = set()

    offer_rows = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.shift_request_id == shift_id,
            models.DispatchOffer.status.in_(
                (models.OfferStatus.pending, models.OfferStatus.accepted)
            ),
        )
        .all()
    )
    for row in offer_rows:
        nurse_ws_ids.add(row.nurse_user_id)
        if row.status != models.OfferStatus.cancelled:
            row.status = models.OfferStatus.cancelled
            row.responded_at = now

    assignment_rows = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.status.in_(
                (
                    models.AssignmentStatus.applied,
                    models.AssignmentStatus.confirmed,
                    models.AssignmentStatus.checked_in,
                )
            ),
        )
        .all()
    )
    for row in assignment_rows:
        nurse_ws_ids.add(row.nurse_user_id)
        row.status = models.AssignmentStatus.cancelled

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

    timeline_payload: dict = {"cancelled_by": "hospital"}
    if cancel_reason:
        timeline_payload["reason"] = cancel_reason

    event = models.ShiftTimelineEvent(
        shift_request_id=shift_id,
        event_type=SHIFT_CANCELLED,
        actor_user_id=current_user.id,
        city_id=shift.city_id,
        payload=timeline_payload,
    )
    db.add(event)
    db.commit()

    if active_session:
        cancel_dispatch_session(active_session.id)

    await ws_manager.send(shift.hospital_user_id, {
        "type": "shift_cancelled",
        "shift_id": shift_id,
        "message": "You cancelled this shift. Dispatch has stopped.",
        "cancellation_reason": cancel_reason,
    })

    nurse_message = "The hospital cancelled this shift."
    if cancel_reason:
        nurse_message = f"The hospital cancelled this shift: {cancel_reason}"

    nurse_payload = {
        "type": "shift_cancelled",
        "shift_id": shift_id,
        "message": nurse_message,
        "cancellation_reason": cancel_reason,
        "lifecycle_stage": "cancelled",
    }
    if nurse_ws_ids:
        for uid in nurse_ws_ids:
            await deliver_nurse_message(db, uid, nurse_payload)
        logger.info(
            "[shifts] shift %d cancelled — notified %d nurse(s)",
            shift_id,
            len(nurse_ws_ids),
        )

    logger.info("[shifts] shift %d cancelled by user %d", shift_id, current_user.id)
    _lifecycle_log(
        "shift_cancelled",
        sid=shift_id,
        uid=current_user.id,
        actor="recruiter",
        stage="cancelled",
        nurses_notified=len(nurse_ws_ids),
        has_reason=bool(cancel_reason),
    )
    return {"cancelled": True, "cancellation_reason": cancel_reason}


@router.post("/{shift_id}/re-dispatch")
async def recruiter_redispatch_shift(
    shift_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Recruiter restarts staff search after expiry/cancel/manual stop.

    Clears any stale session/engine state, then starts a fresh search run.
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

    _expire_shift_if_past_start_unfilled(db, shift)
    db.refresh(shift)
    _stop_all_dispatch_for_shift(db, shift)
    db.commit()
    db.refresh(shift)

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

    if _shift_start_utc_naive(shift.shift_start) <= datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please choose a new future shift time to repost this requirement.",
        )

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
    _lifecycle_log("redispatch", sid=shift_id, uid=current_user.id, actor="recruiter")
    return {"success": True, "message": f"Dispatch restarted for shift {shift_id}"}


@router.post("/{shift_id}/checkin")
async def check_in(
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
    if not _assignment_recruiter_confirmed(assignment, shift):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The hospital has not confirmed your application yet.",
        )

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

    nurse = db.query(models.User).filter(models.User.id == current_user.id).first()
    nurse_name = (nurse.name if nurse and nurse.name else None) or f"Staff #{current_user.id}"

    await ws_manager.send(shift.hospital_user_id, {
        "type": "nurse_checked_in",
        "shift_id": shift_id,
        "nurse_user_id": current_user.id,
        "nurse_name": nurse_name,
        "check_in_at": now.isoformat(),
        "message": f"{nurse_name} checked in on site.",
    })
    await ws_manager.send(current_user.id, {
        "type": "assignment_checked_in",
        "shift_id": shift_id,
        "check_in_at": now.isoformat(),
        "lifecycle_stage": "checked_in",
        "message": "You are checked in. Remember to check out when your shift ends.",
    })

    logger.info("[shifts] nurse %d checked in to shift %d (%.0fm from hospital)", current_user.id, shift_id, distance_m)
    _lifecycle_log(
        "check_in",
        sid=shift_id,
        aid=assignment.id,
        uid=current_user.id,
        actor="nurse",
        stage="checked_in",
        dist_m=round(distance_m),
    )
    return {"checked_in": True, "check_in_at": now.isoformat()}


@router.post("/{shift_id}/checkout")
async def check_out(
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

    nurse = db.query(models.User).filter(models.User.id == current_user.id).first()
    nurse_name = (nurse.name if nurse and nurse.name else None) or f"Staff #{current_user.id}"

    await ws_manager.send(shift.hospital_user_id, {
        "type": "nurse_checked_out",
        "shift_id": shift_id,
        "nurse_user_id": current_user.id,
        "nurse_name": nurse_name,
        "check_out_at": now.isoformat(),
        "message": f"{nurse_name} completed the shift.",
    })
    await ws_manager.send(current_user.id, {
        "type": "assignment_completed",
        "shift_id": shift_id,
        "check_out_at": now.isoformat(),
        "lifecycle_stage": "completed",
        "message": "Shift completed. You are available for new shifts.",
    })

    logger.info("[shifts] nurse %d checked out of shift %d", current_user.id, shift_id)
    _lifecycle_log(
        "check_out",
        sid=shift_id,
        aid=assignment.id,
        uid=current_user.id,
        actor="nurse",
        stage="completed",
    )
    return {"completed": True, "check_out_at": now.isoformat()}
