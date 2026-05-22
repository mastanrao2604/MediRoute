"""When nurses may accept/decline invitations (open until shift start)."""
from datetime import datetime, timezone
from typing import Optional

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


def shift_accepting_staff(shift: models.ShiftRequest, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    if shift.status not in (
        models.ShiftRequestStatus.open,
        models.ShiftRequestStatus.dispatching,
    ):
        return False
    return shift_start_utc_naive(shift.shift_start) > now


def offer_respondable(offer: models.DispatchOffer, shift: models.ShiftRequest) -> bool:
    if offer.status not in RESPONDABLE_OFFER_STATUSES:
        return False
    return shift_accepting_staff(shift)


def seconds_until_shift_start(shift: models.ShiftRequest, now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    return max(0, int((shift_start_utc_naive(shift.shift_start) - now).total_seconds()))
