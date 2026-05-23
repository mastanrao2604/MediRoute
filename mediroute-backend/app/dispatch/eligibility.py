"""Nurse ↔ shift matching: visibility, distance, and accept eligibility."""
import logging
import math
from typing import Optional, Tuple

from .. import models

logger = logging.getLogger(__name__)

# Phase 1 pilot — accept only within this radius (notifications go to all online staff).
PHASE1_ACCEPT_RADIUS_KM = 50.0

# Phase 2 TODO: notify ONLY job seekers inside configurable radius (not all online).
# Phase 2 TODO: tiered radius expansion, dynamic staffing zones, smart locality matching.


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def pincode_band(shift_pc: Optional[str], nurse_pc: Optional[str]) -> int:
    """0 = same pincode; 1 = same region (first 3 digits); 2 = no match."""
    if not shift_pc or not nurse_pc:
        return 2
    s = "".join(c for c in str(shift_pc) if c.isdigit())
    n = "".join(c for c in str(nurse_pc) if c.isdigit())
    if len(s) != 6 or len(n) != 6:
        return 2
    if s == n:
        return 0
    if s[:3] == n[:3]:
        return 1
    return 2


def normalize_pincode(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())
    return digits if len(digits) == 6 else None


def nurse_shift_visible(
    shift: models.ShiftRequest,
    nurse_role: models.UserRole,
    nurse_city_id: str,
) -> bool:
    """Phase 1: any online job seeker in same city + role can see the shift."""
    if shift.role_required != nurse_role:
        return False
    return (shift.city_id or "").strip() == (nurse_city_id or "HYD").strip()


def nurse_nearby_shift(
    shift: models.ShiftRequest,
    nurse_role: models.UserRole,
    nurse_city_id: str,
    nurse_pincode: Optional[str],
) -> bool:
    """Legacy pincode browse hint — prefer nurse_accept_eligible for accept control."""
    if not nurse_shift_visible(shift, nurse_role, nurse_city_id):
        return False
    shift_pc = normalize_pincode(getattr(shift, "hospital_pincode", None))
    if shift_pc and nurse_pincode:
        return pincode_band(shift_pc, nurse_pincode) <= 1
    return True


def _nurse_coords(
    db,
    nurse_user_id: int,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    avail = (
        db.query(models.NurseAvailability)
        .filter(models.NurseAvailability.user_id == nurse_user_id)
        .first()
    )
    lat = avail.latitude if avail and avail.latitude is not None else None
    lng = avail.longitude if avail and avail.longitude is not None else None
    nurse_pc = None
    if lat is None or lng is None:
        from .. import crud

        prof = crud.get_profile(db, nurse_user_id)
        nurse_pc = normalize_pincode(getattr(prof, "service_pincode", None) if prof else None)
    return lat, lng, nurse_pc


def nurse_distance_to_shift_km(
    db,
    shift: models.ShiftRequest,
    nurse_user_id: int,
) -> Optional[float]:
    """Great-circle km when coords exist; None when distance cannot be computed."""
    h_lat = shift.hospital_latitude
    h_lng = shift.hospital_longitude
    if h_lat is None or h_lng is None:
        return None

    n_lat, n_lng, nurse_pc = _nurse_coords(db, nurse_user_id)
    if n_lat is not None and n_lng is not None:
        return _haversine_km(h_lat, h_lng, n_lat, n_lng)

    shift_pc = normalize_pincode(getattr(shift, "hospital_pincode", None))
    if shift_pc and nurse_pc:
        band = pincode_band(shift_pc, nurse_pc)
        if band == 0:
            return 0.0
        if band == 1:
            return 25.0  # pilot pincode-region proxy when GPS unavailable
    return None


def nurse_accept_eligible(
    db,
    shift: models.ShiftRequest,
    nurse_user_id: int,
) -> Tuple[bool, Optional[float], str]:
    """
    Phase 1 accept gate — within PHASE1_ACCEPT_RADIUS_KM only.
    Returns (eligible, distance_km, user_message_if_blocked).
    """
    dist = nurse_distance_to_shift_km(db, shift, nurse_user_id)
    if dist is not None:
        ok = dist <= PHASE1_ACCEPT_RADIUS_KM
        logger.info(
            "[eligibility] accept nurse=%d shift=%d dist_km=%.1f eligible=%s",
            nurse_user_id, shift.id, dist, ok,
        )
        if ok:
            return True, round(dist, 1), ""
        return (
            False,
            round(dist, 1),
            "This shift is currently prioritizing nearby staff.",
        )

    n_lat, n_lng, nurse_pc = _nurse_coords(db, nurse_user_id)
    shift_pc = normalize_pincode(getattr(shift, "hospital_pincode", None))
    if nurse_pc and shift_pc:
        band = pincode_band(shift_pc, nurse_pc)
        if band <= 1:
            return True, None, ""

    return (
        False,
        None,
        "This shift is currently available only for nearby staff. Turn on location in Profile or go online with GPS.",
    )
