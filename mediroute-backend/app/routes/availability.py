"""
Availability + Presence + Device Token routes.

Endpoints:
  PUT  /availability/toggle         — nurse toggles available on/off
  PUT  /availability/location       — heartbeat: update location + last_seen
  GET  /availability/status         — get current availability state
  PUT  /devices/token               — upsert FCM token (call on every app launch)
"""
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from .. import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/availability", tags=["Availability"])
device_router = APIRouter(prefix="/devices", tags=["Devices"])

# ── Heartbeat rate limiting (Task 3) ─────────────────────────────────────────
# Prevents GPS spam: battery drain, unnecessary DB writes, metric noise.
# Per-user in-memory monotonic timestamps — bounded by active user count.
# Cleanup not required: dict is naturally bounded (~1 entry per active nurse).
HEARTBEAT_MIN_INTERVAL_SEC: float = 10.0
_heartbeat_throttle: dict[int, float] = {}  # user_id → last accepted timestamp

# ── CANDIDATE_ROLES: roles that participate in dispatch ──────────────────────
DISPATCH_ELIGIBLE_ROLES = {
    models.UserRole.nurse,
    models.UserRole.staff_nurse,
    models.UserRole.icu_nurse,
    models.UserRole.ot_nurse,
    models.UserRole.emergency_nurse,
    models.UserRole.home_care_nurse,
    models.UserRole.doctor,
    models.UserRole.lab_tech,
    models.UserRole.pharmacist,
    models.UserRole.driver,
    models.UserRole.front_office,
}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AvailabilityToggleRequest(BaseModel):
    is_available: bool
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city_id: Optional[str] = "HYD"


class LocationHeartbeatRequest(BaseModel):
    latitude: float
    longitude: float
    city_id: Optional[str] = "HYD"

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


class AvailabilityStatusResponse(BaseModel):
    is_available: bool
    presence_state: str
    latitude: Optional[float]
    longitude: Optional[float]
    city_id: str
    last_seen: Optional[str]


class DeviceTokenUpsertRequest(BaseModel):
    fcm_token: str
    platform: str = "android"

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v):
        if v not in ("android", "ios", "web"):
            raise ValueError("platform must be android, ios, or web")
        return v

    @field_validator("fcm_token")
    @classmethod
    def validate_token(cls, v):
        if not v or len(v) < 10:
            raise ValueError("invalid FCM token")
        return v.strip()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _upsert_nurse_availability(
    db: Session,
    user_id: int,
    is_available: bool,
    latitude: Optional[float],
    longitude: Optional[float],
    city_id: str,
) -> models.NurseAvailability:
    """Create or update NurseAvailability row."""
    now = datetime.utcnow()
    avail = db.query(models.NurseAvailability).filter(
        models.NurseAvailability.user_id == user_id
    ).first()

    if avail:
        avail.is_available = is_available
        if latitude is not None:
            avail.latitude = latitude
        if longitude is not None:
            avail.longitude = longitude
        avail.city_id = city_id
        avail.last_seen = now
        avail.updated_at = now
    else:
        avail = models.NurseAvailability(
            user_id=user_id,
            is_available=is_available,
            latitude=latitude,
            longitude=longitude,
            city_id=city_id,
            last_seen=now,
        )
        db.add(avail)

    db.commit()
    db.refresh(avail)
    return avail


def _upsert_presence_state(
    db: Session,
    user_id: int,
    state: models.PresenceStateEnum,
    latitude: Optional[float],
    longitude: Optional[float],
    city_id: str,
) -> models.PresenceState:
    """Create or update PresenceState row."""
    now = datetime.utcnow()
    presence = db.query(models.PresenceState).filter(
        models.PresenceState.user_id == user_id
    ).first()

    if presence:
        presence.state = state
        if latitude is not None:
            presence.latitude = latitude
            presence.last_location_at = now
        if longitude is not None:
            presence.longitude = longitude
        presence.city_id = city_id
        presence.last_heartbeat = now
    else:
        presence = models.PresenceState(
            user_id=user_id,
            state=state,
            latitude=latitude,
            longitude=longitude,
            city_id=city_id,
            last_heartbeat=now,
            last_location_at=now if latitude else None,
        )
        db.add(presence)

    db.commit()
    db.refresh(presence)
    return presence


# ── Routes ────────────────────────────────────────────────────────────────────

@router.put("/toggle")
def toggle_availability(
    req: AvailabilityToggleRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Toggle nurse availability. Sets is_available and updates presence state.
    Only dispatch-eligible roles can go available.
    Going unavailable while on an active assignment is blocked.
    """
    if current_user.role not in DISPATCH_ELIGIBLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only healthcare workers can toggle availability.",
        )

    # Block going offline if currently on active assignment
    if not req.is_available:
        active_assignment = db.query(models.LiveAssignment).filter(
            models.LiveAssignment.nurse_user_id == current_user.id,
            models.LiveAssignment.status.in_([
                models.AssignmentStatus.confirmed,
                models.AssignmentStatus.checked_in,
            ]),
        ).first()
        if active_assignment:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot go offline while on an active assignment.",
            )

    presence_state = (
        models.PresenceStateEnum.online_available
        if req.is_available
        else models.PresenceStateEnum.offline
    )

    _upsert_nurse_availability(
        db, current_user.id, req.is_available,
        req.latitude, req.longitude, req.city_id or "HYD"
    )
    _upsert_presence_state(
        db, current_user.id, presence_state,
        req.latitude, req.longitude, req.city_id or "HYD"
    )

    logger.info(
        "[availability] user %d (%s) → %s in %s",
        current_user.id, current_user.role.value,
        "available" if req.is_available else "offline",
        req.city_id or "HYD"
    )

    return {
        "is_available": req.is_available,
        "presence_state": presence_state.value,
        "city_id": req.city_id or "HYD",
    }


@router.put("/location")
def update_location(
    req: LocationHeartbeatRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Heartbeat: update nurse location and last_seen timestamp.
    Call every 60-120 seconds while available.
    Nurses with last_seen > 5 min are excluded from dispatch.
    Rate-limited to 1 update per 10 seconds to prevent GPS spam.
    """
    if current_user.role not in DISPATCH_ELIGIBLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only healthcare workers can send location updates.",
        )

    # Rate limit: at most 1 DB write per HEARTBEAT_MIN_INTERVAL_SEC
    now_ts = time.monotonic()
    last_ts = _heartbeat_throttle.get(current_user.id, 0.0)
    if now_ts - last_ts < HEARTBEAT_MIN_INTERVAL_SEC:
        retry_after = HEARTBEAT_MIN_INTERVAL_SEC - (now_ts - last_ts)
        return Response(
            content='{"throttled":true}',
            media_type="application/json",
            status_code=429,
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    _heartbeat_throttle[current_user.id] = now_ts

    now = datetime.utcnow()
    avail = db.query(models.NurseAvailability).filter(
        models.NurseAvailability.user_id == current_user.id
    ).first()

    if avail:
        avail.latitude = req.latitude
        avail.longitude = req.longitude
        avail.city_id = req.city_id or "HYD"
        avail.last_seen = now
        avail.updated_at = now
    else:
        avail = models.NurseAvailability(
            user_id=current_user.id,
            is_available=False,
            latitude=req.latitude,
            longitude=req.longitude,
            city_id=req.city_id or "HYD",
            last_seen=now,
        )
        db.add(avail)

    presence = db.query(models.PresenceState).filter(
        models.PresenceState.user_id == current_user.id
    ).first()
    if presence:
        presence.latitude = req.latitude
        presence.longitude = req.longitude
        presence.last_heartbeat = now
        presence.last_location_at = now

    db.commit()
    return {"updated": True, "last_seen": now.isoformat()}


@router.get("/status", response_model=AvailabilityStatusResponse)
def get_availability_status(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get current availability and presence state for the authenticated user."""
    avail = db.query(models.NurseAvailability).filter(
        models.NurseAvailability.user_id == current_user.id
    ).first()
    presence = db.query(models.PresenceState).filter(
        models.PresenceState.user_id == current_user.id
    ).first()

    return AvailabilityStatusResponse(
        is_available=avail.is_available if avail else False,
        presence_state=presence.state.value if presence else "offline",
        latitude=avail.latitude if avail else None,
        longitude=avail.longitude if avail else None,
        city_id=avail.city_id if avail else "HYD",
        last_seen=avail.last_seen.isoformat() if avail and avail.last_seen else None,
    )


# ── Device token routes ───────────────────────────────────────────────────────

@device_router.put("/token")
def upsert_device_token(
    req: DeviceTokenUpsertRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upsert FCM device token. Call on every app launch (token can rotate).
    One token per user per platform — UPSERT updates existing record.
    """
    platform = models.DevicePlatform(req.platform)
    token = db.query(models.DeviceToken).filter(
        models.DeviceToken.user_id == current_user.id,
        models.DeviceToken.platform == platform,
    ).first()

    if token:
        token.fcm_token = req.fcm_token
        token.updated_at = datetime.utcnow()
    else:
        token = models.DeviceToken(
            user_id=current_user.id,
            fcm_token=req.fcm_token,
            platform=platform,
        )
        db.add(token)

    db.commit()
    return {"registered": True, "platform": req.platform}
