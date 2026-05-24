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
  POST /shifts/{shift_id}/mark-no-show — recruiter marks confirmed nurse as no-show
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
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
from ..ops_trace import shift_lifecycle, assignment_lifecycle, op_failure, api_timing_trace

logger = logging.getLogger(__name__)
logger.parent = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/shifts", tags=["Shifts"])

# Pilot: multi-nurse lifecycle incomplete — cap all staffing at one confirmed nurse.
PILOT_NURSES_REQUIRED = 1

# Minutes after shift_start before auto no-show (confirmed but never checked in).
NO_SHOW_GRACE_MINUTES = 30


def _pilot_nurses_required(_shift: Optional[models.ShiftRequest] = None) -> int:
    return PILOT_NURSES_REQUIRED


NURSE_DASHBOARD_ARCHIVED = "nurse.dashboard_archived"


def _enum_value(val, default: str = "unknown") -> str:
    """Safe enum → string for mixed historical DB rows."""
    if val is None:
        return default
    if hasattr(val, "value"):
        return str(val.value)
    return str(val)


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
    if st == models.AssignmentStatus.no_show:
        return "no_show"
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


class MarkNoShowRequest(BaseModel):
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


def _no_show_grace_deadline(shift: models.ShiftRequest) -> datetime:
    return _shift_start_utc_naive(shift.shift_start) + timedelta(minutes=NO_SHOW_GRACE_MINUTES)


def _assignment_awaiting_checkin(
    assignment: models.LiveAssignment,
    shift: models.ShiftRequest,
) -> bool:
    if not _assignment_recruiter_confirmed(assignment, shift):
        return False
    if assignment.status in (
        models.AssignmentStatus.checked_in,
        models.AssignmentStatus.completed,
        models.AssignmentStatus.no_show,
        models.AssignmentStatus.cancelled,
    ):
        return False
    return True


def _can_mark_no_show(
    assignment: models.LiveAssignment,
    shift: models.ShiftRequest,
    now: datetime,
    *,
    auto: bool = False,
) -> bool:
    if not _assignment_awaiting_checkin(assignment, shift):
        return False
    if auto:
        return now >= _no_show_grace_deadline(shift)
    return _shift_start_utc_naive(shift.shift_start) <= now


def _reliability_apply_no_show_sync(db: Session, nurse_user_id: int, now: datetime) -> None:
    rs = db.query(models.ReliabilityScore).filter(
        models.ReliabilityScore.user_id == nurse_user_id
    ).first()
    if not rs:
        rs = models.ReliabilityScore(user_id=nurse_user_id)
        db.add(rs)
    rs.no_shows = (rs.no_shows or 0) + 1
    total = rs.total_offers or 0
    if total > 0:
        accept_rate = (rs.accepted or 0) / total
        timeout_penalty = ((rs.timed_out or 0) * 0.5) / max(total, 1)
        no_show_penalty = (rs.no_shows * 3.0) / max(total, 1)
        rs.score = max(
            0.0,
            min(100.0, (accept_rate * 100) - (timeout_penalty * 10) - (no_show_penalty * 10)),
        )
    rs.last_calculated_at = now


def _apply_no_show_sync(
    db: Session,
    shift: models.ShiftRequest,
    assignment: models.LiveAssignment,
    now: datetime,
    *,
    actor_user_id: Optional[int] = None,
    auto: bool = False,
) -> int:
    """Mark no-show, reopen shift for recovery, restore nurse availability. Returns nurse_user_id."""
    nurse_id = assignment.nurse_user_id
    assignment.status = models.AssignmentStatus.no_show

    _reliability_apply_no_show_sync(db, nurse_id, now)

    shift.search_closed_at = None
    shift.filled_at = None
    if shift.status == models.ShiftRequestStatus.filled:
        shift.status = models.ShiftRequestStatus.open

    presence = db.query(models.PresenceState).filter(
        models.PresenceState.user_id == nurse_id
    ).first()
    if presence and presence.state == models.PresenceStateEnum.online_busy:
        presence.state = models.PresenceStateEnum.online_available

    avail = db.query(models.NurseAvailability).filter(
        models.NurseAvailability.user_id == nurse_id
    ).first()
    if avail:
        avail.is_available = True
        avail.updated_at = now

    db.add(
        models.ShiftTimelineEvent(
            shift_request_id=shift.id,
            event_type=ASSIGNMENT_NO_SHOW,
            actor_user_id=actor_user_id,
            city_id=shift.city_id,
            payload={"nurse_user_id": nurse_id, "auto": auto},
        )
    )
    db.commit()
    logger.info(
        "[shifts] no_show sid=%s aid=%s nurse=%s auto=%s actor=%s",
        shift.id,
        assignment.id,
        nurse_id,
        auto,
        actor_user_id,
    )
    return nurse_id


def process_auto_no_shows_sync(db: Session, now: Optional[datetime] = None) -> int:
    """Janitor: auto no-show confirmed nurses past grace without check-in."""
    now = now or datetime.utcnow()
    rows = (
        db.query(models.LiveAssignment, models.ShiftRequest)
        .join(models.ShiftRequest, models.LiveAssignment.shift_request_id == models.ShiftRequest.id)
        .filter(
            models.LiveAssignment.recruiter_confirmed_at.isnot(None),
            models.LiveAssignment.status == models.AssignmentStatus.confirmed,
            models.LiveAssignment.check_in_at.is_(None),
            models.ShiftRequest.status.in_([
                models.ShiftRequestStatus.filled,
                models.ShiftRequestStatus.open,
                models.ShiftRequestStatus.dispatching,
            ]),
        )
        .all()
    )
    count = 0
    for assignment, shift in rows:
        if not _can_mark_no_show(assignment, shift, now, auto=True):
            continue
        try:
            _apply_no_show_sync(db, shift, assignment, now, auto=True)
            count += 1
        except SQLAlchemyError as exc:
            db.rollback()
            logger.warning(
                "[shifts] auto_no_show failed sid=%s aid=%s err=%s",
                shift.id,
                assignment.id,
                exc,
            )
    return count


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


def _attach_recruiter_staffing(
    d: dict,
    shift: models.ShiftRequest,
    db: Session,
    *,
    assignments: Optional[list] = None,
    pending_offers: Optional[list] = None,
    nurse_summaries: Optional[dict] = None,
    list_mode: bool = False,
) -> dict:
    """Applicants + staffing progress for recruiter UI."""
    terminal = (
        models.ShiftRequestStatus.cancelled,
        models.ShiftRequestStatus.expired,
    )
    if list_mode and shift.status in terminal:
        d["applicants"] = []
        d["confirmed_count"] = 0
        d["applied_count"] = 0
        d["nurses_required"] = _pilot_nurses_required(shift)
        d["pending_responses"] = 0
        d["search_active"] = False
        return d

    if assignments is None:
        assignments = (
            db.query(models.LiveAssignment)
            .filter(models.LiveAssignment.shift_request_id == shift.id)
            .order_by(models.LiveAssignment.confirmed_at.asc())
            .all()
        )
    applicants = []
    for a in assignments:
        try:
            card = (
                dict(nurse_summaries[a.nurse_user_id])
                if nurse_summaries and a.nurse_user_id in nurse_summaries
                else _assigned_nurse_summary(db, a.nurse_user_id)
            )
            if list_mode:
                card.pop("phone", None)
                card.pop("education", None)
                card.pop("skills", None)
            stage = _assignment_lifecycle_stage(a, shift)
            card["lifecycle_stage"] = stage
            card["status"] = (
                "confirmed" if _assignment_recruiter_confirmed(a, shift) else "applied"
            )
            card["assignment_id"] = a.id
            card["assignment_status"] = _enum_value(a.status)
            card["check_in_at"] = utc_iso(a.check_in_at) if a.check_in_at else None
            card["check_out_at"] = utc_iso(a.check_out_at) if a.check_out_at else None
            now = datetime.utcnow()
            if _assignment_awaiting_checkin(a, shift):
                card["awaiting_check_in"] = True
                card["can_mark_no_show"] = _can_mark_no_show(a, shift, now, auto=False)
                if now >= _no_show_grace_deadline(shift):
                    card["no_show_overdue"] = True
            applicants.append(card)
        except Exception as exc:
            logger.warning(
                "[shifts] applicant_card_fallback sid=%s aid=%s uid=%s err=%s",
                shift.id,
                getattr(a, "id", None),
                getattr(a, "nurse_user_id", None),
                exc,
            )
            applicants.append({
                "user_id": getattr(a, "nurse_user_id", None),
                "name": f"Staff #{getattr(a, 'nurse_user_id', '?')}",
                "lifecycle_stage": _assignment_lifecycle_stage(a, shift),
                "status": "applied",
                "assignment_id": a.id,
                "assignment_status": _enum_value(getattr(a, "status", None)),
                "_serialize_degraded": True,
            })

    if pending_offers is None:
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
        try:
            card = (
                dict(nurse_summaries[offer.nurse_user_id])
                if nurse_summaries and offer.nurse_user_id in nurse_summaries
                else _assigned_nurse_summary(db, offer.nurse_user_id)
            )
            if list_mode:
                card.pop("phone", None)
                card.pop("education", None)
                card.pop("skills", None)
            card["lifecycle_stage"] = "invited"
            card["status"] = "waiting"
            card["offer_id"] = offer.id
            applicants.append(card)
        except Exception as exc:
            logger.warning(
                "[shifts] offer_card_fallback sid=%s offer_id=%s uid=%s err=%s",
                shift.id,
                offer.id,
                offer.nurse_user_id,
                exc,
            )
            applicants.append({
                "user_id": offer.nurse_user_id,
                "name": f"Staff #{offer.nurse_user_id}",
                "lifecycle_stage": "invited",
                "status": "waiting",
                "offer_id": offer.id,
                "_serialize_degraded": True,
            })

    pending_responses = sum(
        1 for o in pending_offers if o.nurse_user_id not in assignment_nurse_ids
    )
    nurses_required = _pilot_nurses_required(shift)
    d["applicants"] = applicants
    d["confirmed_count"] = sum(
        1 for a in assignments
        if _assignment_recruiter_confirmed(a, shift)
        and a.status not in (models.AssignmentStatus.no_show, models.AssignmentStatus.cancelled)
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
            "status": _enum_value(a0.status),
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
        try:
            d["assigned_nurse"] = (
                dict(nurse_summaries[a0.nurse_user_id])
                if nurse_summaries and a0.nurse_user_id in nurse_summaries
                else _assigned_nurse_summary(db, a0.nurse_user_id)
            )
        except Exception as exc:
            logger.warning(
                "[shifts] assigned_nurse_fallback sid=%s uid=%s err=%s",
                shift.id,
                a0.nurse_user_id,
                exc,
            )
            d["assigned_nurse"] = {
                "user_id": a0.nurse_user_id,
                "name": f"Staff #{a0.nurse_user_id}",
                "_serialize_degraded": True,
            }
    return d


def _reliability_display_score(rs: Optional[models.ReliabilityScore]) -> float:
    """New users show 100% until enough offer history to score fairly."""
    if not rs or rs.score is None:
        return 100.0
    if (rs.total_offers or 0) <= 2:
        return 100.0
    return round(float(rs.score), 1)


def _nurse_summary_from_parts(
    nurse_user_id: int,
    user: Optional[models.User],
    profile: Optional[models.Profile],
    rs: Optional[models.ReliabilityScore],
) -> dict:
    name = (user.name if user and user.name else None) or f"Staff #{nurse_user_id}"
    education = profile.education if profile else None
    if education is not None and not isinstance(education, (str, int, float, bool, list, dict, type(None))):
        education = str(education)
    return {
        "user_id": nurse_user_id,
        "name": name,
        "phone": user.phone if user else None,
        "role": _enum_value(user.role, None) if user else None,
        "experience_years": profile.experience_years if profile else None,
        "skills": profile.skills if profile else None,
        "education": education,
        "service_locality": profile.service_locality if profile else None,
        "rating": _reliability_display_score(rs),
        "completed_shifts": int(rs.completed_shifts) if rs else 0,
    }


def _batch_nurse_summaries(db: Session, nurse_user_ids: set) -> dict:
    """One round-trip per table for recruiter applicant cards."""
    if not nurse_user_ids:
        return {}
    ids = list(nurse_user_ids)
    users = {
        u.id: u
        for u in db.query(models.User).filter(models.User.id.in_(ids)).all()
    }
    profiles = {
        p.user_id: p
        for p in db.query(models.Profile).filter(models.Profile.user_id.in_(ids)).all()
    }
    scores = {
        r.user_id: r
        for r in db.query(models.ReliabilityScore).filter(
            models.ReliabilityScore.user_id.in_(ids)
        ).all()
    }
    return {
        uid: _nurse_summary_from_parts(uid, users.get(uid), profiles.get(uid), scores.get(uid))
        for uid in ids
    }


def _batch_recruiter_list_context(db: Session, shift_ids: list[int]):
    if not shift_ids:
        return {}, {}, {}
    assignments = (
        db.query(models.LiveAssignment)
        .filter(models.LiveAssignment.shift_request_id.in_(shift_ids))
        .order_by(models.LiveAssignment.confirmed_at.asc())
        .all()
    )
    assignments_by_shift: dict = {}
    nurse_ids: set = set()
    for a in assignments:
        assignments_by_shift.setdefault(a.shift_request_id, []).append(a)
        nurse_ids.add(a.nurse_user_id)
    pending_offers = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.shift_request_id.in_(shift_ids),
            models.DispatchOffer.status == models.OfferStatus.pending,
        )
        .order_by(models.DispatchOffer.offered_at.asc())
        .all()
    )
    offers_by_shift: dict = {}
    for o in pending_offers:
        offers_by_shift.setdefault(o.shift_request_id, []).append(o)
        nurse_ids.add(o.nurse_user_id)
    return assignments_by_shift, offers_by_shift, _batch_nurse_summaries(db, nurse_ids)


def _build_recruiter_shift_rows_batch(
    db: Session,
    shifts: list[models.ShiftRequest],
) -> list[dict]:
    shift_ids = [s.id for s in shifts]
    assignments_by_shift, offers_by_shift, summaries = _batch_recruiter_list_context(db, shift_ids)
    payload = []
    active_statuses = (
        models.ShiftRequestStatus.open,
        models.ShiftRequestStatus.dispatching,
        models.ShiftRequestStatus.filled,
    )
    for s in shifts:
        try:
            if s.status in active_statuses:
                _try_expire_shift(db, s)
            primary = None
            if assignments_by_shift.get(s.id):
                primary = assignments_by_shift[s.id][0]
            d = _shift_to_dict(s, primary)
            d = _attach_recruiter_staffing(
                d,
                s,
                db,
                assignments=assignments_by_shift.get(s.id, []),
                pending_offers=offers_by_shift.get(s.id, []),
                nurse_summaries=summaries,
                list_mode=True,
            )
            payload.append(d)
        except Exception as exc:
            payload.append(_shift_list_fallback(s, exc))
    return payload


def _assigned_nurse_summary(db: Session, nurse_user_id: int) -> dict:
    """Staff card for recruiter — contact + profile fields only."""
    batch = _batch_nurse_summaries(db, {nurse_user_id})
    return batch[nurse_user_id]


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
        "role_required": _enum_value(shift.role_required),
        "specialty": shift.specialty,
        "urgency": _enum_value(shift.urgency),
        "status": _enum_value(shift.status),
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
            "status": _enum_value(assignment.status),
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


def _shift_list_fallback(shift: models.ShiftRequest, err: Exception) -> dict:
    """Minimal shift card when full serialization fails — keeps dashboard alive."""
    logger.warning(
        "[shifts] serialize_fallback sid=%s err=%s",
        getattr(shift, "id", None),
        err,
        exc_info=True,
    )
    return {
        "id": shift.id,
        "city_id": getattr(shift, "city_id", "HYD"),
        "hospital_name": shift.hospital_name or "Shift",
        "role_required": _enum_value(getattr(shift, "role_required", None)),
        "urgency": _enum_value(getattr(shift, "urgency", None), "standard"),
        "status": _enum_value(getattr(shift, "status", None), "open"),
        "shift_start": utc_iso(getattr(shift, "shift_start", None)),
        "shift_end": utc_iso(getattr(shift, "shift_end", None)),
        "nurses_required": PILOT_NURSES_REQUIRED,
        "search_closed": bool(getattr(shift, "search_closed_at", None)),
        "applicants": [],
        "confirmed_count": 0,
        "applied_count": 0,
        "pending_responses": 0,
        "search_active": False,
        "_serialize_degraded": True,
    }


def _try_expire_shift(db: Session, shift: models.ShiftRequest) -> None:
    """Expire on read without failing the entire list."""
    try:
        if _expire_shift_if_past_start_unfilled(db, shift):
            db.refresh(shift)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("[shifts] expire_on_read failed sid=%s err=%s", shift.id, exc)
    except Exception as exc:
        logger.warning("[shifts] expire_on_read unexpected sid=%s err=%s", shift.id, exc)


def _build_recruiter_shift_row(
    db: Session,
    shift: models.ShiftRequest,
) -> dict:
    _try_expire_shift(db, shift)
    try:
        primary = (
            db.query(models.LiveAssignment)
            .filter(models.LiveAssignment.shift_request_id == shift.id)
            .order_by(models.LiveAssignment.confirmed_at.asc())
            .first()
        )
        d = _shift_to_dict(shift, primary)
        return _attach_recruiter_staffing(d, shift, db)
    except Exception as exc:
        return _shift_list_fallback(shift, exc)


def _build_nurse_shift_row(
    db: Session,
    assignment: models.LiveAssignment,
    shift: models.ShiftRequest,
) -> dict:
    _try_expire_shift(db, shift)
    db.refresh(assignment)
    return _shift_to_dict(shift, assignment, db)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_shift(
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
        shift_lifecycle(
            "shift_post_denied",
            uid=current_user.id,
            auth_role=_enum_value(current_user.role),
            reason="not_recruiter",
        )
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
        try:
            shift_dict = _shift_to_dict(existing)
        except Exception as exc:
            shift_dict = _shift_list_fallback(existing, exc)
        return {"shift": shift_dict, "created": False}

    # Basic overlap check (FUTURE: AssignmentConflictValidator — §24.5)
    # For now: warn if shift_start is in the past
    if req.shift_start < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="shift_start cannot be in the past.",
        )

    shift_lifecycle(
        "shift_post_start",
        uid=current_user.id,
        auth_role=_enum_value(current_user.role),
        role_required=req.role_required,
        urgency=req.urgency,
    )

    try:
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
    shift_lifecycle(
        "shift_created",
        sid=shift.id,
        uid=current_user.id,
        actor="recruiter",
        auth_role=_enum_value(current_user.role),
        role_required=shift.role_required.value,
        urgency=shift.urgency.value,
    )

    # Start dispatch in background (non-blocking asyncio task)
    background_tasks.add_task(start_dispatch, shift.id)

    try:
        shift_dict = _shift_to_dict(shift)
    except Exception as exc:
        logger.warning(
            "[shifts] create_shift serialize degraded sid=%s err=%s",
            shift.id,
            exc,
            exc_info=True,
        )
        shift_dict = _shift_list_fallback(shift, exc)
    return {"shift": shift_dict, "created": True}


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
        t0 = time.perf_counter()
        archived = _recruiter_archived_shift_ids(db, current_user.id)
        t_arch = time.perf_counter()
        shifts = (
            db.query(models.ShiftRequest)
            .filter(models.ShiftRequest.hospital_user_id == current_user.id)
            .order_by(models.ShiftRequest.created_at.desc())
            .limit(50)
            .all()
        )
        visible = [s for s in shifts if s.id not in archived]
        t_query = time.perf_counter()
        payload = _build_recruiter_shift_rows_batch(db, visible)
        t_done = time.perf_counter()
        api_timing_trace(
            "GET /shifts/",
            role="recruiter",
            count=len(payload),
            archived_ms=int((t_arch - t0) * 1000),
            query_ms=int((t_query - t_arch) * 1000),
            build_ms=int((t_done - t_query) * 1000),
            total_ms=int((t_done - t0) * 1000),
        )
        shift_lifecycle(
            "list_shifts_recruiter",
            uid=current_user.id,
            actor="recruiter",
            count=len(payload),
        )
        return {"shifts": payload}
    else:
        archived = _nurse_dashboard_archived_ids(db, current_user.id)
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
            try:
                payload.append(_build_nurse_shift_row(db, assignment, shift))
            except Exception as exc:
                payload.append(_shift_list_fallback(shift, exc))
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

    shift_lifecycle(
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
        shift_lifecycle(
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

    shift_lifecycle(
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
    shift_lifecycle(
        "recruiter_confirmed",
        sid=shift_id,
        aid=assignment.id,
        uid=nurse_id,
        actor="recruiter",
        stage="recruiter_confirmed",
        not_selected=len(not_selected_ids),
    )
    assignment_lifecycle(
        "recruiter_confirmed",
        sid=shift_id,
        aid=assignment.id,
        uid=nurse_id,
        rid=current_user.id,
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
    shift_lifecycle(
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

    # Stop in-flight dispatch loops immediately (DB updates follow below).
    for session in (
        db.query(models.DispatchSession)
        .filter(models.DispatchSession.shift_request_id == shift_id)
        .all()
    ):
        cancel_dispatch_session(session.id)

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
    shift.search_closed_at = shift.search_closed_at or now

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

    hospital_payload = {
        "type": "shift_cancelled",
        "shift_id": shift_id,
        "message": "You cancelled this shift. Dispatch has stopped.",
        "cancellation_reason": cancel_reason,
        "lifecycle_stage": "cancelled",
    }
    hospital_ok = await ws_manager.send(shift.hospital_user_id, hospital_payload)

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
    nurse_delivered = 0
    nurse_failed = 0
    if nurse_ws_ids:
        results = await asyncio.gather(
            *[deliver_nurse_message(db, uid, nurse_payload) for uid in nurse_ws_ids],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                nurse_failed += 1
            else:
                nurse_delivered += 1
        logger.info(
            "[shifts] shift %d cancelled — nurse notify delivered=%d failed=%d",
            shift_id,
            nurse_delivered,
            nurse_failed,
        )

    logger.info(
        "[shifts] shift %d cancelled by user %d hospital_ws=%s",
        shift_id,
        current_user.id,
        hospital_ok,
    )
    shift_lifecycle(
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
    shift_lifecycle("redispatch", sid=shift_id, uid=current_user.id, actor="recruiter")
    return {"success": True, "message": f"Dispatch restarted for shift {shift_id}"}


@router.post("/{shift_id}/mark-no-show")
async def mark_no_show(
    shift_id: int,
    req: MarkNoShowRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Recruiter marks a confirmed nurse as no-show and reopens shift for recovery."""
    if current_user.role != models.UserRole.recruiter:
        raise HTTPException(status_code=403, detail="Only recruiters can mark no-show.")

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id,
        models.ShiftRequest.hospital_user_id == current_user.id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    assignment = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.nurse_user_id == req.nurse_user_id,
        )
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found.")

    now = datetime.utcnow()
    if not _can_mark_no_show(assignment, shift, now, auto=False):
        raise HTTPException(
            status_code=400,
            detail="This nurse is not eligible for no-show (not confirmed, already checked in, or shift has not started).",
        )

    nurse_id = _apply_no_show_sync(
        db, shift, assignment, now, actor_user_id=current_user.id, auto=False
    )
    db.refresh(shift)

    nurse = db.query(models.User).filter(models.User.id == nurse_id).first()
    nurse_name = (nurse.name if nurse and nurse.name else None) or f"Staff #{nurse_id}"

    nurse_payload = {
        "type": "assignment_no_show",
        "shift_id": shift_id,
        "lifecycle_stage": "no_show",
        "message": "You were marked as a no-show for this shift.",
    }
    hospital_payload = {
        "type": "nurse_no_show",
        "shift_id": shift_id,
        "nurse_user_id": nurse_id,
        "nurse_name": nurse_name,
        "message": f"{nurse_name} did not arrive — shift reopened for staffing.",
        "lifecycle_stage": "no_show",
    }
    await asyncio.gather(
        deliver_nurse_message(db, nurse_id, nurse_payload),
        ws_manager.send(shift.hospital_user_id, hospital_payload),
        return_exceptions=True,
    )

    shift_lifecycle(
        "no_show",
        sid=shift_id,
        aid=assignment.id,
        uid=nurse_id,
        actor="recruiter",
        stage="no_show",
    )
    assignment_lifecycle(
        "no_show",
        sid=shift_id,
        aid=assignment.id,
        uid=nurse_id,
        rid=current_user.id,
        stage="no_show",
    )
    return {
        "no_show": True,
        "shift_id": shift_id,
        "nurse_user_id": nurse_id,
        "shift_status": _enum_value(shift.status),
        "search_reopened": True,
    }


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
    shift_lifecycle(
        "check_in",
        sid=shift_id,
        aid=assignment.id,
        uid=current_user.id,
        actor="nurse",
        stage="checked_in",
        dist_m=round(distance_m),
    )
    assignment_lifecycle(
        "checked_in",
        sid=shift_id,
        aid=assignment.id,
        uid=current_user.id,
        stage="checked_in",
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
    shift_lifecycle(
        "check_out",
        sid=shift_id,
        aid=assignment.id,
        uid=current_user.id,
        actor="nurse",
        stage="completed",
    )
    assignment_lifecycle(
        "completed",
        sid=shift_id,
        aid=assignment.id,
        uid=current_user.id,
        stage="completed",
    )
    return {"completed": True, "check_out_at": now.isoformat()}
