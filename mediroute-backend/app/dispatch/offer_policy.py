"""When nurses may accept/decline invitations (open until shift start)."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, or_

from .. import models

RESPONDABLE_OFFER_STATUSES = (
    models.OfferStatus.pending,
    models.OfferStatus.declined,
    models.OfferStatus.timed_out,
)


def shift_start_utc_naive(shift_start: datetime) -> datetime:
    if shift_start.tzinfo:
        return shift_start.astimezone(timezone.utc).replace(tzinfo=None)
    return shift_start


def offer_expires_at(shift: models.ShiftRequest) -> datetime:
    """Invitation valid until shift start (not per-wave seconds)."""
    return shift_start_utc_naive(shift.shift_start)


def shift_search_open(shift: models.ShiftRequest, now: Optional[datetime] = None) -> bool:
    """True while nurses may still accept and the engine may send offers."""
    now = now or datetime.utcnow()
    if getattr(shift, "search_closed_at", None):
        return False
    return shift_accepting_staff(shift, now)


def shift_accepting_staff(shift: models.ShiftRequest, now: Optional[datetime] = None) -> bool:
    """True while shift is open for new applications (not the same as staffing finalized)."""
    now = now or datetime.utcnow()
    if shift.status not in (
        models.ShiftRequestStatus.open,
        models.ShiftRequestStatus.dispatching,
    ):
        return False
    return shift_start_utc_naive(shift.shift_start) > now


_TERMINAL_ASSIGNMENT_STATUSES = (
    models.AssignmentStatus.cancelled,
    models.AssignmentStatus.completed,
    models.AssignmentStatus.no_show,
)

_TERMINAL_SHIFT_STATUSES = (
    models.ShiftRequestStatus.cancelled,
    models.ShiftRequestStatus.expired,
    models.ShiftRequestStatus.filled,
)


def shift_staffing_finalized(db, shift_id: int) -> bool:
    """True only when recruiter has explicitly confirmed at least one nurse."""
    return (
        db.query(models.LiveAssignment.id)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.recruiter_confirmed_at.isnot(None),
        )
        .first()
        is not None
    )


def offer_respondable(offer: models.DispatchOffer, shift: models.ShiftRequest) -> bool:
    if offer.status not in RESPONDABLE_OFFER_STATUSES:
        return False
    if shift.status in _TERMINAL_SHIFT_STATUSES:
        return False
    return shift_search_open(shift)


def seconds_until_shift_start(shift: models.ShiftRequest, now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    return max(0, int((shift_start_utc_naive(shift.shift_start) - now).total_seconds()))


def nurse_blocks_other_acceptances(
    db,
    nurse_user_id: int,
    *,
    exclude_shift_id: Optional[int] = None,
) -> Optional[models.LiveAssignment]:
    """
    Assignment that prevents accepting another shift: checked-in, or recruiter-confirmed
    on a non-cancelled shift. Pending applications alone do not block.
    """
    q = (
        db.query(models.LiveAssignment)
        .join(
            models.ShiftRequest,
            models.LiveAssignment.shift_request_id == models.ShiftRequest.id,
        )
        .filter(
            models.LiveAssignment.nurse_user_id == nurse_user_id,
            models.LiveAssignment.status.notin_(_TERMINAL_ASSIGNMENT_STATUSES),
            models.ShiftRequest.status.notin_(
                (
                    models.ShiftRequestStatus.cancelled,
                    models.ShiftRequestStatus.expired,
                )
            ),
            or_(
                models.LiveAssignment.status == models.AssignmentStatus.checked_in,
                and_(
                    models.LiveAssignment.status == models.AssignmentStatus.confirmed,
                    models.LiveAssignment.recruiter_confirmed_at.isnot(None),
                ),
            ),
        )
    )
    if exclude_shift_id is not None:
        q = q.filter(models.LiveAssignment.shift_request_id != exclude_shift_id)
    return q.first()
