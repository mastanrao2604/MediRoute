"""Nurse ↔ shift matching for browse/detail (mirrors dispatch engine pincode tiers)."""
from datetime import datetime
from typing import Optional

from .. import models


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


def nurse_nearby_shift(
    shift: models.ShiftRequest,
    nurse_role: models.UserRole,
    nurse_city_id: str,
    nurse_pincode: Optional[str],
) -> bool:
    if shift.role_required != nurse_role:
        return False
    if (shift.city_id or "").strip() != (nurse_city_id or "HYD").strip():
        return False
    shift_pc = normalize_pincode(getattr(shift, "hospital_pincode", None))
    if shift_pc and nurse_pincode:
        return pincode_band(shift_pc, nurse_pincode) <= 1
    return True
