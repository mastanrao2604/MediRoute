"""
Phase 1 Dispatch Engine — asyncio + Haversine + in-process event signaling.

Architecture:
  - Wave-based dispatch: send offers to N nurses per wave, wait for acceptance
  - First-accept-wins: SELECT FOR UPDATE SKIP LOCKED prevents double-acceptance
  - Adaptive wave sizes: never exceed available pool (prevents empty waves)
  - run_in_executor: all sync SQLAlchemy calls wrapped — never blocks event loop
  - dispatch_events: in-process dict[session_id → asyncio.Event] for signaling
  - ShiftTimelineEvent: emitted for every significant state change (sacred audit log)

Phase 2 upgrade (when you add a second Render instance):
  Replace dispatch_events with Redis key polling in _wait_for_acceptance().
  Zero other changes needed.

See §4 (Dispatch Engine) and §9 (Stage 1) in ARCHITECTURE.md.
"""
import asyncio
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Optional

import sentry_sdk

from ..database import SessionLocal
from .. import models
from ..utils.datetime_util import utc_iso


# Route engine logs through uvicorn.error so they appear in the captured log file.
# Without this the app module logger has no handler when run via Start-Process.
logging.getLogger(__name__).parent = logging.getLogger("uvicorn.error")
from .events import (
    SHIFT_DISPATCHING, SHIFT_EXPIRED, SHIFT_FILLED, SHIFT_CANCELLED,
    OFFER_SENT, OFFER_ACCEPTED, OFFER_DECLINED, OFFER_TIMED_OUT, OFFER_CANCELLED,
    WAVE_EXHAUSTED, DISPATCH_FAILED,
)
from .offer_policy import shift_search_open, shift_start_utc_naive

logger = logging.getLogger(__name__)

# ── Structured dispatch log helper (Task 10) ──────────────────────────────────
def _dispatch_log(level: str, event_type: str, **kwargs) -> None:
    """
    Emit a structured JSON dispatch log entry.
    Lightweight: one json.dumps per call, no blocking I/O.
    """
    entry = {"event": event_type, "ts": datetime.utcnow().isoformat()}
    entry.update({k: v for k, v in kwargs.items() if v is not None})
    msg = json.dumps(entry, default=str)
    if level == "warning":
        logger.warning(msg)
    elif level == "error":
        logger.error(msg)
    elif level == "debug":
        logger.debug(msg)
    else:
        logger.info(msg)


# ── Lightweight in-process metrics (Task 14) ──────────────────────────────────
# Pure counters — zero DB, zero I/O. Exposed via GET /admin/ops/metrics.
# Resets on process restart (acceptable for Phase 1 single-instance).
_metrics: dict = {
    "dispatches_started": 0,
    "dispatches_filled": 0,
    "dispatches_expired": 0,
    "dispatches_failed": 0,
    "offers_sent": 0,
    "offers_accepted": 0,
    "offers_declined": 0,
    "offers_timed_out": 0,
    "total_fill_time_sec": 0.0,
    "total_waves_used": 0,
}


def get_dispatch_metrics() -> dict:
    """Return a snapshot copy of the metrics dict. Safe to call from any route."""
    m = dict(_metrics)
    filled = m["dispatches_filled"]
    m["avg_fill_time_sec"] = (
        round(m["total_fill_time_sec"] / filled, 1) if filled > 0 else None
    )
    m["avg_waves_per_dispatch"] = (
        round(m["total_waves_used"] / filled, 2) if filled > 0 else None
    )
    total_resp = m["offers_accepted"] + m["offers_declined"] + m["offers_timed_out"]
    m["accept_rate"] = round(m["offers_accepted"] / total_resp, 3) if total_resp > 0 else None
    m["timeout_rate"] = round(m["offers_timed_out"] / total_resp, 3) if total_resp > 0 else None
    return m


def get_semaphore_utilization() -> dict:
    """Return dispatch semaphore utilization. In-memory only. Safe to call from any route."""
    sem = _dispatch_semaphore
    if sem is None:
        # Not yet created — no dispatch has run in this process
        return {"capacity": _MAX_CONCURRENT_DISPATCHES, "in_use": 0, "available": _MAX_CONCURRENT_DISPATCHES}
    in_use = max(0, _MAX_CONCURRENT_DISPATCHES - sem._value)
    return {
        "capacity": _MAX_CONCURRENT_DISPATCHES,
        "in_use": in_use,
        "available": max(0, sem._value),
    }


# ── Global kill switch (Task 11) ────────────────────────────────────────────────
# Set DISPATCH_ENABLED=false in Render env vars to halt new dispatches without a deploy.
# In-flight dispatches are NOT interrupted — they complete normally.
# Runtime toggle: call set_dispatch_enabled(False) via POST /admin/ops/dispatch-toggle
# (no restart required; resets to env-var default on next deploy).
_DISPATCH_ENABLED: bool = os.getenv("DISPATCH_ENABLED", "true").lower() != "false"
# Keep old name as alias so existing code using DISPATCH_ENABLED still imports OK
DISPATCH_ENABLED: bool = _DISPATCH_ENABLED


def is_dispatch_enabled() -> bool:
    """Runtime-readable kill-switch state. Always use this in hot paths."""
    return _DISPATCH_ENABLED


def set_dispatch_enabled(enabled: bool, actor: str = "unknown") -> bool:
    """
    Toggle the dispatch kill switch at runtime (no restart required).
    Returns the new state.
    Logs the change with the actor for audit purposes.
    """
    global _DISPATCH_ENABLED, DISPATCH_ENABLED
    prev = _DISPATCH_ENABLED
    _DISPATCH_ENABLED = enabled
    DISPATCH_ENABLED = enabled  # keep alias in sync
    _dispatch_log(
        "warning" if not enabled else "info",
        "dispatch.kill_switch_changed",
        actor=actor,
        prev=prev,
        new=enabled,
    )
    return enabled


# ── Dispatch concurrency semaphore (Task 4) ───────────────────────────────────
# Prevents event-loop overload from a sudden burst of simultaneous shifts.
# 30 concurrent dispatches = 30 × max_wave_timeout (300s standard) potential
# overlap, each holding DB connections via run_in_executor.
# Phase 2: replace with Redis-backed distributed semaphore.
_MAX_CONCURRENT_DISPATCHES: int = int(os.getenv("MAX_CONCURRENT_DISPATCHES", "30"))
# Semaphore is created lazily in start_dispatch (requires running event loop).
_dispatch_semaphore: Optional[asyncio.Semaphore] = None


def _get_dispatch_semaphore() -> asyncio.Semaphore:
    """Lazy-init semaphore (must be created inside the event loop)."""
    global _dispatch_semaphore
    if _dispatch_semaphore is None:
        _dispatch_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_DISPATCHES)
    return _dispatch_semaphore


# ── Offer fatigue throttling (Task 5) ────────────────────────────────────────
# Prevents a nurse from being flooded with offers from many concurrent shifts.
# Max OFFER_FATIGUE_MAX offers within OFFER_FATIGUE_WINDOW_SEC seconds.
# In-memory: monotonic timestamps per nurse. Bounded by active nurse count.
_OFFER_FATIGUE_MAX: int = int(os.getenv("OFFER_FATIGUE_MAX", "5"))
_OFFER_FATIGUE_WINDOW_SEC: float = float(os.getenv("OFFER_FATIGUE_WINDOW_SEC", "900"))  # 15 min
_offer_fatigue: dict[int, list] = {}  # nurse_user_id → [monotonic_timestamp, ...]


def _is_nurse_fatigued(nurse_id: int) -> bool:
    """
    Return True if nurse has received too many offers recently.
    Also prunes stale timestamps from the list (amortized cleanup).
    """
    now = time.monotonic()
    cutoff = now - _OFFER_FATIGUE_WINDOW_SEC
    recent = [t for t in _offer_fatigue.get(nurse_id, []) if t > cutoff]
    _offer_fatigue[nurse_id] = recent  # update in-place with pruned list
    return len(recent) >= _OFFER_FATIGUE_MAX


def _record_offer_sent(nurse_id: int) -> None:
    """Record that an offer was sent to this nurse (called after offers are created)."""
    now = time.monotonic()
    if nurse_id not in _offer_fatigue:
        _offer_fatigue[nurse_id] = []
    _offer_fatigue[nurse_id].append(now)
    _metrics["offers_sent"] += 1

# ── Thread pool for sync DB calls ─────────────────────────────────────────────
# All SQLAlchemy operations run in this pool via run_in_executor.
# Sized to match DB connection pool (pool_size=10 in database.py).
_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="dispatch-db")

# ── In-process dispatch signaling ─────────────────────────────────────────────
# session_id → asyncio.Event, set when any offer for that session is accepted.
# Phase 2: replace with Redis key presence check.
dispatch_events: dict[int, asyncio.Event] = {}

# ── Manual cancellation signal ────────────────────────────────────────────────
# session_ids cancelled via POST /admin/ops/cancel-dispatch.
# Checked at every wave boundary so in-flight dispatches stop cleanly
# without corrupting DB state. Cleaned up in the dispatch finally block.
_cancelled_sessions: set[int] = set()


def cancel_dispatch_session(session_id: int) -> None:
    """
    Signal the dispatch engine to stop a session at the next wave boundary.
    Called by POST /admin/ops/cancel-dispatch. Thread-safe: set.add is atomic
    in CPython. Caller is responsible for all DB state mutations.
    Also fires the asyncio.Event so _wait_for_acceptance returns immediately
    (avoids waiting out the full wave timeout).
    """
    _cancelled_sessions.add(session_id)
    event = dispatch_events.get(session_id)
    if event is not None:
        event.set()


# ── Per-urgency dispatch parameters ──────────────────────────────────────────
URGENCY_CONFIG: dict[str, dict] = {
    "emergency": {
        "wave_timeout_sec": 30,
        "max_waves": 5,
        "base_radius_km": 3.0,
        "radius_step_km": 2.0,   # expand radius each wave
        "wave_sizes": [3, 5, 8, 12, 20],
        # How long to pause between waves when no nurses are online yet.
        # Gives nurses time to come online before we expand radius further.
        "no_candidate_wait_sec": 20,
        # After ALL waves exhausted with zero candidates, how often to re-check
        # for nurses who came online — keeps dispatch alive until shift_start.
        "watchlist_interval_sec": 30,
    },
    "urgent": {
        "wave_timeout_sec": 90,
        "max_waves": 4,
        "base_radius_km": 5.0,
        "radius_step_km": 3.0,
        "wave_sizes": [3, 5, 8, 12],
        "no_candidate_wait_sec": 45,
        "watchlist_interval_sec": 60,
    },
    "standard": {
        "wave_timeout_sec": 300,
        "max_waves": 3,
        "base_radius_km": 10.0,
        "radius_step_km": 5.0,
        "wave_sizes": [5, 8, 15],
        "no_candidate_wait_sec": 90,
        "watchlist_interval_sec": 120,
    },
    "planned": {
        "wave_timeout_sec": 600,
        "max_waves": 2,
        "base_radius_km": 15.0,
        "radius_step_km": 10.0,
        "wave_sizes": [10, 20],
        "no_candidate_wait_sec": 180,
        "watchlist_interval_sec": 300,
    },
}


# ── Geo utility ───────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km. Phase 1 replacement for PostGIS ST_DWithin."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Timeline event writer ─────────────────────────────────────────────────────

def _write_timeline_event_sync(
    db,
    shift_id: int,
    event_type: str,
    city_id: str,
    actor_id: Optional[int] = None,
    payload: Optional[dict] = None,
) -> None:
    """Sync write to ShiftTimelineEvent. Called inside run_in_executor."""
    try:
        event = models.ShiftTimelineEvent(
            shift_request_id=shift_id,
            event_type=event_type,
            actor_user_id=actor_id,
            city_id=city_id,
            payload=payload or {},
        )
        db.add(event)
        db.commit()
    except Exception as exc:
        logger.error("[dispatch] timeline event write failed (%s): %s", event_type, exc)
        db.rollback()


# ── DB operations (sync, run inside executor) ─────────────────────────────────

def _get_shift_sync(db, shift_id: int) -> Optional[models.ShiftRequest]:
    return db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()


def _is_shift_cancelled_in_db_sync(db, shift_id: int) -> bool:
    """True if hospital/recruiter cancelled the shift row — defense against stale engine state."""
    row = (
        db.query(models.ShiftRequest.status)
        .filter(models.ShiftRequest.id == shift_id)
        .first()
    )
    return row is not None and row[0] == models.ShiftRequestStatus.cancelled


def _get_hospital_user_sync(db, user_id: int) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.id == user_id).first()


def _check_hospital_verified_sync(db, hospital_user_id: int) -> bool:
    """Verification gate — block dispatch if hospital user is not verified."""
    user = db.query(models.User).filter(
        models.User.id == hospital_user_id,
    ).first()
    if not user:
        logger.warning("[dispatch][verify] hospital_user_id=%d NOT FOUND in users table", hospital_user_id)
        return False
    verified = bool(user.is_verified)
    logger.info(
        "[dispatch][verify] hospital_user_id=%d name=%r role=%s is_verified=%s",
        hospital_user_id, user.name, user.role.value if user.role else "?", verified
    )
    return verified


def _create_session_sync(db, shift_id: int) -> models.DispatchSession:
    session = models.DispatchSession(shift_request_id=shift_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _update_shift_status_sync(
    db, shift_id: int, status: models.ShiftRequestStatus, filled_at: Optional[datetime] = None
) -> None:
    shift = db.query(models.ShiftRequest).filter(models.ShiftRequest.id == shift_id).first()
    if shift:
        shift.status = status
        if filled_at:
            shift.filled_at = filled_at
        db.commit()


def _update_session_sync(
    db,
    session_id: int,
    status: models.DispatchSessionStatus,
    wave: Optional[int] = None,
    waves_exhausted: Optional[bool] = None,
) -> None:
    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.id == session_id
    ).first()
    if session:
        session.status = status
        if wave is not None:
            session.current_wave = wave
        if waves_exhausted is not None:
            session.waves_exhausted = waves_exhausted
        if status != models.DispatchSessionStatus.active:
            session.completed_at = datetime.utcnow()
        db.commit()


def _pincode_band(shift_pc: Optional[str], nurse_pc: Optional[str]) -> int:
    """0 = same 6-digit pincode; 1 = same postal region (first 3 digits); 2 = no match."""
    if not shift_pc or not nurse_pc:
        return 2
    s = "".join(c for c in shift_pc if c.isdigit())
    n = "".join(c for c in nurse_pc if c.isdigit())
    if len(s) != 6 or len(n) != 6:
        return 2
    if s == n:
        return 0
    if s[:3] == n[:3]:
        return 1
    return 2


def _find_candidates_sync(
    db,
    shift: models.ShiftRequest,
    radius_km: float,
    exclude_user_ids: list[int],
) -> list[tuple[models.User, models.NurseAvailability, float]]:
    """
    Find eligible nurses for dispatch in city + role.

    Phase 1 (pilot): include ALL online nurses for notification visibility.
    Phase 2 TODO: restrict notifications to nurses inside configurable radius only.

    Returns list of (User, NurseAvailability, distance_km) sorted by
    (pincode band, distance ASC, reliability DESC).
    """
    freshness_cutoff = datetime.utcnow() - timedelta(minutes=5)

    # ── Geo-located online nurses (all in city — no wave-radius filter) ───────
    geo_rows = (
        db.query(models.User, models.NurseAvailability)
        .join(models.NurseAvailability, models.User.id == models.NurseAvailability.user_id)
        .filter(
            models.NurseAvailability.city_id == shift.city_id,
            models.NurseAvailability.is_available == True,
            models.NurseAvailability.last_seen >= freshness_cutoff,
            models.NurseAvailability.latitude.isnot(None),
            models.NurseAvailability.longitude.isnot(None),
            models.User.role == shift.role_required,
            models.User.id.not_in(exclude_user_ids) if exclude_user_ids else True,
        )
        .all()
    )

    candidates = []
    geo_total = len(geo_rows)
    max_dist = 0.0
    for user, avail in geo_rows:
        dist = haversine_km(
            shift.hospital_latitude, shift.hospital_longitude,
            avail.latitude, avail.longitude,
        )
        candidates.append((user, avail, dist))
        max_dist = max(max_dist, dist)

    logger.info(
        "[dispatch] shift %d: phase1 notify pool geo=%d (max_dist=%.1fkm, accept_cap=%.0fkm), excluded=%d",
        shift.id, geo_total, max_dist, 50.0, len(exclude_user_ids),
    )

    # ── Online nurses without GPS (still notified in Phase 1) ─────────────────
    no_geo_rows = (
        db.query(models.User, models.NurseAvailability)
        .join(models.NurseAvailability, models.User.id == models.NurseAvailability.user_id)
        .filter(
            models.NurseAvailability.city_id == shift.city_id,
            models.NurseAvailability.is_available == True,
            models.NurseAvailability.last_seen >= freshness_cutoff,
            models.User.role == shift.role_required,
            models.User.id.not_in(exclude_user_ids) if exclude_user_ids else True,
            models.NurseAvailability.latitude.is_(None),
        )
        .all()
    )
    for user, avail in no_geo_rows:
        candidates.append((user, avail, radius_km + 1.0))

    if no_geo_rows:
        logger.info(
            "[dispatch] shift %d: phase1 notify pool +%d no-GPS nurses",
            shift.id, len(no_geo_rows),
        )

    # ── Sort: pincode relevance, distance ASC, reliability DESC ─────────────────
    scores = {
        rs.user_id: rs.score
        for rs in db.query(models.ReliabilityScore).filter(
            models.ReliabilityScore.user_id.in_([u.id for u, _, _ in candidates])
        ).all()
    } if candidates else {}

    raw_shift_pc = getattr(shift, "hospital_pincode", None)
    shift_pc: Optional[str] = None
    if raw_shift_pc:
        shift_pc = "".join(c for c in str(raw_shift_pc) if c.isdigit())
        if len(shift_pc) != 6:
            shift_pc = None

    profiles_by_uid: dict[int, models.Profile] = {}
    if candidates:
        uids = list({u.id for u, _, _ in candidates})
        if uids:
            for p in db.query(models.Profile).filter(models.Profile.user_id.in_(uids)).all():
                profiles_by_uid[p.user_id] = p

    def pin_rank(uid: int) -> int:
        prof = profiles_by_uid.get(uid)
        np = prof.service_pincode if prof else None
        return _pincode_band(shift_pc, np)

    candidates.sort(
        key=lambda x: (pin_rank(x[0].id), x[2], -scores.get(x[0].id, 100.0))
    )
    return candidates


def _create_offers_sync(
    db,
    session_id: int,
    shift_id: int,
    nurse_ids: list[int],
    wave_num: int,
    expires_at: datetime,
) -> list[models.DispatchOffer]:
    """Create DispatchOffer rows for a wave. Returns created offers."""
    offers = []
    for nurse_id in nurse_ids:
        offer = models.DispatchOffer(
            session_id=session_id,
            shift_request_id=shift_id,
            nurse_user_id=nurse_id,
            wave_number=wave_num,
            expires_at=expires_at,
        )
        db.add(offer)
        offers.append(offer)
    db.commit()
    # Refresh to get IDs
    for offer in offers:
        db.refresh(offer)
    return offers


def _expire_wave_offers_sync(db, session_id: int, wave_num: int) -> int:
    """
    Wave wait ended with no accept. Offers stay pending until shift start so nurses
    can respond from Jobs/Dashboard later; engine continues to next wave.
    """
    offers = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.session_id == session_id,
            models.DispatchOffer.wave_number == wave_num,
            models.DispatchOffer.status == models.OfferStatus.pending,
        )
        .all()
    )
    return len(offers)


def _check_session_filled_sync(db, session_id: int) -> bool:
    """Return True if the session already has a filled shift (accepted offer)."""
    return db.query(models.DispatchOffer).filter(
        models.DispatchOffer.session_id == session_id,
        models.DispatchOffer.status == models.OfferStatus.accepted,
    ).first() is not None


def _count_assignments_sync(db, shift_id: int) -> int:
    return (
        db.query(models.LiveAssignment)
        .filter(models.LiveAssignment.shift_request_id == shift_id)
        .count()
    )


def _is_search_closed_sync(db, shift_id: int) -> bool:
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    if not shift:
        return True
    if getattr(shift, "search_closed_at", None):
        return True
    if shift.status in (
        models.ShiftRequestStatus.cancelled,
        models.ShiftRequestStatus.expired,
    ):
        return True
    if shift_start_utc_naive(shift.shift_start) <= datetime.utcnow():
        return True
    return False


def record_nurse_accept_sync(
    db, shift_id: int, nurse_id: int, offer_id: int, now: datetime, *, commit: bool = True
) -> models.LiveAssignment:
    """
    Confirm one nurse for a shift without closing the hospital search.
    Idempotent per (shift, nurse).
    """
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    if not shift:
        raise ValueError("Shift not found")
    if not shift_search_open(shift, now):
        raise ValueError("Search is closed for this shift")

    existing = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == shift_id,
            models.LiveAssignment.nurse_user_id == nurse_id,
        )
        .first()
    )
    if existing:
        return existing

    assignment = models.LiveAssignment(
        shift_request_id=shift_id,
        nurse_user_id=nurse_id,
        offer_id=offer_id,
        confirmed_at=now,
    )
    db.add(assignment)
    if shift.status == models.ShiftRequestStatus.open:
        shift.status = models.ShiftRequestStatus.dispatching
    if commit:
        db.commit()
        db.refresh(assignment)
    else:
        db.flush()
    return assignment


def _finalize_search_closed_sync(
    db, shift_id: int, now: datetime, reason: str = "manual"
) -> None:
    """Stop search, mark nurses busy, set shift filled when staff confirmed."""
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    if not shift:
        return
    if not getattr(shift, "search_closed_at", None):
        shift.search_closed_at = now

    assignments = (
        db.query(models.LiveAssignment)
        .filter(models.LiveAssignment.shift_request_id == shift_id)
        .all()
    )
    for assignment in assignments:
        presence = db.query(models.PresenceState).filter(
            models.PresenceState.user_id == assignment.nurse_user_id
        ).first()
        if presence:
            presence.state = models.PresenceStateEnum.online_busy
        avail = db.query(models.NurseAvailability).filter(
            models.NurseAvailability.user_id == assignment.nurse_user_id
        ).first()
        if avail:
            avail.is_available = False
            avail.updated_at = now

    if assignments:
        shift.status = models.ShiftRequestStatus.filled
        shift.filled_at = now
    elif shift.status == models.ShiftRequestStatus.dispatching:
        shift.status = models.ShiftRequestStatus.open

    session = db.query(models.DispatchSession).filter(
        models.DispatchSession.shift_request_id == shift_id,
        models.DispatchSession.status == models.DispatchSessionStatus.active,
    ).first()
    if session:
        session.status = models.DispatchSessionStatus.completed
        session.completed_at = now

    db.add(
        models.ShiftTimelineEvent(
            shift_request_id=shift_id,
            event_type=SHIFT_FILLED if assignments else SHIFT_EXPIRED,
            actor_user_id=None,
            city_id=shift.city_id,
            payload={"reason": reason, "confirmed_count": len(assignments)},
        )
    )
    db.commit()


def _finalize_assignment_sync(
    db, shift_id: int, nurse_id: int, offer_id: int, now: datetime
) -> models.LiveAssignment:
    """Legacy alias — record accept without closing search."""
    return record_nurse_accept_sync(db, shift_id, nurse_id, offer_id, now)


def _update_reliability_on_event_sync(
    db, nurse_id: int, event: str
) -> None:
    """Update reliability score after offer event. Phase 1 simple scoring."""
    rs = db.query(models.ReliabilityScore).filter(
        models.ReliabilityScore.user_id == nurse_id
    ).first()
    if not rs:
        rs = models.ReliabilityScore(user_id=nurse_id)
        db.add(rs)

    rs.total_offers += 1
    if event == "accepted":
        rs.accepted += 1
    elif event == "declined":
        rs.declined += 1
    elif event == "timed_out":
        rs.timed_out += 1

    # Score formula: 100 * accepted/total, penalized by no-shows and timeouts
    if rs.total_offers > 0:
        accept_rate = rs.accepted / rs.total_offers
        timeout_penalty = (rs.timed_out * 0.5) / max(rs.total_offers, 1)
        no_show_penalty = (rs.no_shows * 3.0) / max(rs.total_offers, 1)
        raw = (accept_rate * 100) - (timeout_penalty * 10) - (no_show_penalty * 10)
        rs.score = max(0.0, min(100.0, raw))

    rs.last_calculated_at = datetime.utcnow()
    db.commit()


def _get_device_tokens_sync(db, user_ids: list[int]) -> dict[int, str]:
    """Return {user_id: fcm_token} for a list of user IDs."""
    rows = db.query(models.DeviceToken).filter(
        models.DeviceToken.user_id.in_(user_ids),
        models.DeviceToken.platform == models.DevicePlatform.android,
    ).all()
    return {r.user_id: r.fcm_token for r in rows}


def _delete_device_token_sync(db, fcm_token: str) -> None:
    """
    Remove an invalid/unregistered FCM token from device_tokens.
    Called after FCM returns 'invalid_token' to stop wasting sends.
    """
    try:
        deleted = db.query(models.DeviceToken).filter(
            models.DeviceToken.fcm_token == fcm_token
        ).delete(synchronize_session=False)
        db.commit()
        if deleted:
            logger.info("[dispatch] Deleted invalid FCM token: %s...", fcm_token[:12])
    except Exception as exc:
        logger.warning("[dispatch] Failed to delete invalid FCM token: %s", exc)
        db.rollback()


def _get_shift_with_hospital_sync(db, shift_id: int):
    """Return (shift, hospital_user) tuple."""
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == shift_id
    ).first()
    hospital = db.query(models.User).filter(
        models.User.id == shift.hospital_user_id
    ).first() if shift else None
    return shift, hospital


# ── Async helpers ─────────────────────────────────────────────────────────────

async def _run_sync(fn, *args):
    """Run a sync function in the thread pool executor. Never blocks event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, partial(fn, *args))


async def _notify_wave(
    db,
    fcm: dict,
    offers: list[models.DispatchOffer],
    shift: models.ShiftRequest,
    wave_timeout_sec: int,
) -> None:
    """
    Deliver dispatch offers to nurses via WebSocket (primary) + FCM (fallback).

    FCM is sent in the thread-pool executor so it never blocks the event loop
    (the Firebase Admin SDK makes a synchronous HTTP call internally).

    Invalid FCM tokens returned by Firebase are immediately deleted from the DB
    so future waves don't waste time sending to dead tokens.
    """
    from ..ws_manager import ws_manager
    from ..utils.fcm import send_dispatch_offer

    loop = asyncio.get_running_loop()
    ws_count = 0
    fcm_count = 0
    fcm_fail = 0
    invalid_tokens: list[str] = []  # collected for cleanup after the loop

    from .offer_policy import seconds_until_shift_start
    from .eligibility import nurse_accept_eligible
    respond_by_sec = seconds_until_shift_start(shift)
    for offer in offers:
        accept_ok, dist_km, block_msg = nurse_accept_eligible(db, shift, offer.nurse_user_id)
        payload = {
            "type": "dispatch_offer",
            "offer_id": offer.id,
            "shift_id": shift.id,
            "hospital_name": shift.hospital_name,
            "role": shift.role_required.value,
            "specialty": shift.specialty,
            "urgency": shift.urgency.value,
            "shift_start": utc_iso(shift.shift_start),
            "shift_end": utc_iso(shift.shift_end),
            "pay_rate": shift.pay_rate,
            "notes": shift.notes,
            "hospital_lat": shift.hospital_latitude,
            "hospital_lng": shift.hospital_longitude,
            "city_id": shift.city_id,
            "expires_at": utc_iso(offer.expires_at),
            "respond_by_sec": respond_by_sec,
            "expires_in_sec": respond_by_sec,
            "wave": offer.wave_number,
            "accept_eligible": accept_ok,
            "distance_km": dist_km,
            "accept_blocked_message": block_msg or None,
        }

        ws_delivered = await ws_manager.send(offer.nurse_user_id, payload)
        if ws_delivered:
            ws_count += 1

        # FCM fallback: nurse not connected via WS (background / killed / offline)
        elif fcm:
            token = fcm.get(offer.nurse_user_id)
            if token:
                # Run in executor — Firebase Admin SDK is blocking HTTP; must not touch event loop
                ok, err_cat = await loop.run_in_executor(
                    _executor,
                    partial(
                        send_dispatch_offer,
                        fcm_token=token,
                        offer_id=offer.id,
                        shift_id=shift.id,
                        hospital_name=shift.hospital_name,
                        role=shift.role_required.value,
                        urgency=shift.urgency.value,
                        expires_in_sec=wave_timeout_sec,
                        pay_rate=shift.pay_rate,
                    ),
                )
                if ok:
                    fcm_count += 1
                else:
                    fcm_fail += 1
                    if err_cat == "invalid_token":
                        invalid_tokens.append(token)

    # Clean up invalid FCM tokens in executor (doesn't block dispatch)
    for bad_token in invalid_tokens:
        await loop.run_in_executor(
            _executor, partial(_delete_device_token_sync, db, bad_token)
        )

    logger.info(
        "[dispatch] shift %d wave notify: ws=%d fcm_ok=%d fcm_fail=%d nurses=%d",
        shift.id, ws_count, fcm_count, fcm_fail, len(offers),
    )


async def _wait_for_acceptance(session_id: int, timeout_sec: int) -> bool:
    """
    Wait for any nurse to accept an offer in this session.
    Returns True if accepted within timeout, False if timed out.

    Phase 1: asyncio.Event (in-process, single instance).
    Phase 2 upgrade: replace body with Redis key polling:
        for _ in range(timeout_sec):
            if await redis.exists(f"dispatch:accepted:{session_id}"):
                return True
            await asyncio.sleep(1)
        return False
    """
    event = dispatch_events.get(session_id)
    if event is None:
        event = asyncio.Event()
        dispatch_events[session_id] = event

    try:
        await asyncio.wait_for(event.wait(), timeout=float(timeout_sec))
        return True
    except asyncio.TimeoutError:
        return False


async def _notify_hospital(
    hospital_user_id: int,
    shift_id: int,
    message_type: str,
    payload: dict,
) -> None:
    """Send real-time status update to hospital via WebSocket."""
    from ..ws_manager import ws_manager
    msg = {"type": message_type, "shift_id": shift_id, **payload}
    is_online = ws_manager.is_connected(hospital_user_id)
    delivered = await ws_manager.send(hospital_user_id, msg)
    if delivered:
        logger.info(
            "[dispatch][ws] ✓ hospital_user=%d shift=%d type=%s — delivered",
            hospital_user_id, shift_id, message_type
        )
    else:
        logger.warning(
            "[dispatch][ws] ✗ hospital_user=%d shift=%d type=%s — NOT delivered (ws_online=%s)",
            hospital_user_id, shift_id, message_type, is_online
        )


# ── Main dispatch entry point ─────────────────────────────────────────────────

async def run_dispatch(shift_id: int) -> None:
    """
    Main dispatch coroutine — run as asyncio.create_task().

    Flow:
      1. Verify hospital + create session
      2. For each wave:
         a. Find candidates in radius (offer-fatigue filtered)
         b. Create offers + notify nurses
         c. Wait wave_timeout_sec for any acceptance
         d. If accepted → finalize assignment → done
         e. If timed_out → expire offers → next wave with larger radius
      3. If all waves exhausted → mark shift expired

    All DB operations use run_in_executor — never blocks the event loop.
    Concurrency is bounded by _get_dispatch_semaphore().
    DB session is opened inside the semaphore — no idle connection held while queuing.
    """
    _metrics["dispatches_started"] += 1
    async with _get_dispatch_semaphore():
        db = SessionLocal()
        try:
            with sentry_sdk.start_transaction(op="dispatch", name=f"dispatch.shift.{shift_id}"):
                try:
                    await _run_dispatch_inner(db, shift_id)
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)
                    logger.error("[dispatch] unhandled error for shift %d: %s", shift_id, exc, exc_info=True)
                    _metrics["dispatches_failed"] += 1
                    try:
                        shift_for_err = await _run_sync(_get_shift_sync, db, shift_id)
                        city_id_for_err = shift_for_err.city_id if shift_for_err else "HYD"
                        session = await _run_sync(
                            lambda d: d.query(models.DispatchSession).filter(
                                models.DispatchSession.shift_request_id == shift_id
                            ).first(),
                            db,
                        )
                        if session:
                            await _run_sync(
                                _update_session_sync, db, session.id,
                                models.DispatchSessionStatus.failed
                            )
                        await _run_sync(
                            _update_shift_status_sync, db, shift_id,
                            models.ShiftRequestStatus.expired
                        )
                        # Emit dispatch.failed timeline event for operational debugging
                        await _run_sync(
                            _write_timeline_event_sync, db, shift_id, DISPATCH_FAILED,
                            city_id_for_err, None, {"error": str(exc)[:200]},
                        )
                    except Exception:
                        pass
        finally:
            db.close()


async def _do_fill(
    db,
    shift_id: int,
    session_id: int,
    wave_num: int,
    dispatch_start: datetime,
    shift,
) -> bool:
    """Notify hospital of new acceptance; return True only when search should end."""
    import functools

    accepted_offer = await asyncio.get_running_loop().run_in_executor(
        _executor,
        functools.partial(
            lambda d, sid: d.query(models.DispatchOffer).filter(
                models.DispatchOffer.session_id == sid,
                models.DispatchOffer.status == models.OfferStatus.accepted,
            ).order_by(models.DispatchOffer.responded_at.desc()).first(),
            db,
            session_id,
        ),
    )
    if not accepted_offer:
        return False

    now = datetime.utcnow()
    confirmed_count = await asyncio.get_running_loop().run_in_executor(
        _executor, functools.partial(_count_assignments_sync, db, shift_id)
    )
    nurses_required = getattr(shift, "nurses_required", None) or 1

    nurse_user = await asyncio.get_running_loop().run_in_executor(
        _executor,
        functools.partial(
            lambda d, uid: d.query(models.User).filter(models.User.id == uid).first(),
            db,
            accepted_offer.nurse_user_id,
        ),
    )
    nurse_name = (nurse_user.name or f"Staff #{accepted_offer.nurse_user_id}") if nurse_user else "Nurse"

    await _notify_hospital(shift.hospital_user_id, shift_id, "nurse_accepted", {
        "nurse_name": nurse_name,
        "nurse_user_id": accepted_offer.nurse_user_id,
        "confirmed_count": confirmed_count,
        "nurses_required": nurses_required,
        "wave": wave_num,
        "message": (
            f"{nurse_name} accepted ({confirmed_count} of {nurses_required} confirmed) "
            "— still searching for more staff."
        ),
    })

    event = dispatch_events.get(session_id)
    if event is not None:
        event.clear()

    if await asyncio.get_running_loop().run_in_executor(
        _executor, functools.partial(_is_search_closed_sync, db, shift_id)
    ):
        await asyncio.get_running_loop().run_in_executor(
            _executor,
            functools.partial(_finalize_search_closed_sync, db, shift_id, now, "auto"),
        )
        if confirmed_count > 0:
            await _notify_hospital(shift.hospital_user_id, shift_id, "shift_search_stopped", {
                "confirmed_count": confirmed_count,
                "message": "Staff search closed.",
            })
            await _notify_hospital(shift.hospital_user_id, shift_id, "shift_filled", {
                "nurse_name": nurse_name,
                "confirmed_count": confirmed_count,
                "message": "Staff finalized for this shift.",
            })
            _metrics["dispatches_filled"] += 1
        logger.info("[dispatch] shift %d search closed (wave %d)", shift_id, wave_num)
        return True

    await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_wave_update", {
        "status": "receiving",
        "confirmed_count": confirmed_count,
        "nurses_required": nurses_required,
        "message": (
            f"{confirmed_count} of {nurses_required} confirmed — still searching for more staff."
        ),
    })
    logger.info(
        "[dispatch] shift %d nurse accepted — search continues (%d/%d)",
        shift_id,
        confirmed_count,
        nurses_required,
    )
    return False


async def _run_dispatch_inner(db, shift_id: int) -> None:
    """Inner dispatch logic — separated for clean error boundary."""
    import functools

    # 1. Load shift
    shift = await asyncio.get_running_loop().run_in_executor(
        _executor, functools.partial(_get_shift_sync, db, shift_id)
    )
    if not shift:
        logger.error("[dispatch] shift %d not found", shift_id)
        return
    if shift.status != models.ShiftRequestStatus.open:
        logger.info("[dispatch] shift %d is not open (status=%s) — skipping", shift_id, shift.status)
        return

    # 2. Hospital verification gate (§21.3)
    verified = await asyncio.get_running_loop().run_in_executor(
        _executor, functools.partial(_check_hospital_verified_sync, db, shift.hospital_user_id)
    )
    if not verified:
        logger.warning("[dispatch] shift %d — hospital user %d not verified, dispatch blocked",
                       shift_id, shift.hospital_user_id)
        await asyncio.get_running_loop().run_in_executor(
            _executor, functools.partial(
                _update_shift_status_sync, db, shift_id, models.ShiftRequestStatus.cancelled
            )
        )
        await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_error", {
            "reason": "hospital_not_verified",
            "message": "Complete hospital verification to post shifts.",
        })
        return

    # 3. Create dispatch session + update shift status
    session = await asyncio.get_running_loop().run_in_executor(
        _executor, functools.partial(_create_session_sync, db, shift_id)
    )
    await asyncio.get_running_loop().run_in_executor(
        _executor, functools.partial(
            _update_shift_status_sync, db, shift_id, models.ShiftRequestStatus.dispatching
        )
    )

    # Register event for this session
    dispatch_events[session.id] = asyncio.Event()

    # Write timeline event
    await asyncio.get_running_loop().run_in_executor(
        _executor, functools.partial(
            _write_timeline_event_sync, db, shift_id, SHIFT_DISPATCHING, shift.city_id,
            shift.hospital_user_id, {"session_id": session.id}
        )
    )

    # Notify hospital: dispatch started
    await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_started", {
        "session_id": session.id,
        "urgency": shift.urgency.value,
        "message": "Finding nurses...",
    })

    cfg = URGENCY_CONFIG.get(shift.urgency.value, URGENCY_CONFIG["standard"])
    wave_sizes = cfg["wave_sizes"]
    wave_timeout = cfg["wave_timeout_sec"]
    base_radius = shift.dispatch_radius_km or cfg["base_radius_km"]
    radius_step = cfg["radius_step_km"]
    max_waves = len(wave_sizes)

    already_offered: list[int] = []
    dispatch_start = datetime.utcnow()
    filled = False

    try:
        for wave_idx, wave_size in enumerate(wave_sizes):
            # External cancellation check — set by cancel_dispatch_session()
            # Checked before any DB work so no new offers are ever sent after cancel.
            if session.id in _cancelled_sessions:
                logger.info(
                    "[dispatch] shift %d session %d: externally cancelled — stopping at wave boundary",
                    shift_id, session.id,
                )
                filled = True  # skip the "all waves exhausted" / shift-expired handling
                break
            if await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(_is_shift_cancelled_in_db_sync, db, shift_id)
            ):
                cancel_dispatch_session(session.id)
                logger.info(
                    "[dispatch] shift %d session %d: shift row cancelled — stopping waves",
                    shift_id, session.id,
                )
                filled = True
                break
            wave_num = wave_idx + 1
            current_radius = base_radius + (wave_idx * radius_step)

            # 4. Find candidates
            candidates = await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(
                    _find_candidates_sync, db, shift, current_radius, already_offered
                )
            )

            # Task 5: Filter offer-fatigued nurses before wave sizing
            candidates = [c for c in candidates if not _is_nurse_fatigued(c[0].id)]

            # Adaptive wave sizing — never exceed available pool
            actual_wave_size = min(wave_size, len(candidates))
            if actual_wave_size == 0:
                logger.info(
                    "[dispatch] shift %d wave %d: no candidates in %.1fkm — skipping wave",
                    shift_id, wave_num, current_radius
                )
                await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_wave_update", {
                    "wave": wave_num,
                    "status": "no_candidates",
                    "radius_km": current_radius,
                    "message": f"No nurses online within {current_radius:.0f}km — expanding search area...",
                })
                # Pause before expanding radius so nurses who just came online
                # have a chance to be picked up in the next radius expansion.
                no_wait = cfg.get("no_candidate_wait_sec", 60)
                _shift_start_naive = (
                    shift.shift_start.astimezone(timezone.utc).replace(tzinfo=None)
                    if shift.shift_start.tzinfo else shift.shift_start
                )
                time_remaining = (_shift_start_naive - datetime.utcnow()).total_seconds()
                if time_remaining > 0:
                    await asyncio.sleep(min(no_wait, time_remaining))
                continue

            wave_candidates = candidates[:actual_wave_size]
            nurse_ids = [u.id for u, _, _ in wave_candidates]
            already_offered.extend(nurse_ids)

            # 5. Create offers (valid for nurse until shift start — wave_timeout is engine-only)
            from .offer_policy import offer_expires_at
            expires_at = offer_expires_at(shift)
            offers = await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(
                    _create_offers_sync, db, session.id, shift_id,
                    nurse_ids, wave_num, expires_at
                )
            )

            # Task 5: Record offers sent for fatigue tracking
            for offer in offers:
                _record_offer_sent(offer.nurse_user_id)

            # Write timeline event for wave
            await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(
                    _write_timeline_event_sync, db, shift_id, OFFER_SENT, shift.city_id,
                    None, {
                        "wave": wave_num,
                        "nurse_count": len(offers),
                        "radius_km": current_radius,
                        "offer_ids": [o.id for o in offers],
                    }
                )
            )

            # 6. Load FCM tokens for fallback delivery
            fcm_tokens = await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(_get_device_tokens_sync, db, nurse_ids)
            )

            # 7. Notify nurses (WS primary, FCM fallback — runs in executor)
            await _notify_wave(db, fcm_tokens, offers, shift, wave_timeout)

            # Notify hospital: wave dispatched
            await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_wave_update", {
                "wave": wave_num,
                "status": "dispatching",
                "nurses_notified": len(offers),
                "radius_km": current_radius,
                "timeout_sec": wave_timeout,
                "message": f"Wave {wave_num}: notified {len(offers)} nurses within {current_radius:.0f}km...",
            })

            # Update session wave counter
            await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(
                    _update_session_sync, db, session.id,
                    models.DispatchSessionStatus.active, wave_num
                )
            )

            # 8. Wait for acceptance
            accepted = await _wait_for_acceptance(session.id, wave_timeout)
            # Session event is also set by cancel_dispatch_session() — distinguish from real accepts
            if session.id in _cancelled_sessions or await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(_is_shift_cancelled_in_db_sync, db, shift_id)
            ):
                cancel_dispatch_session(session.id)
                filled = True
                break

            if accepted:
                # 9. Finalise via shared helper (also used by watchlist loop)
                filled = await _do_fill(db, shift_id, session.id, wave_num, dispatch_start, shift)
                if filled:
                    break

            else:
                # Wave timed out
                expired_count = await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(_expire_wave_offers_sync, db, session.id, wave_num)
                )

                await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(
                        _write_timeline_event_sync, db, shift_id, WAVE_EXHAUSTED, shift.city_id,
                        None, {
                            "wave": wave_num,
                            "timed_out_count": expired_count,
                            "next_radius_km": current_radius + radius_step,
                        }
                    )
                )

                await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_wave_update", {
                    "wave": wave_num,
                    "status": "timed_out",
                    "message": f"Wave {wave_num} timed out — expanding search radius...",
                })

                logger.info(
                    "[dispatch] shift %d wave %d timed out (%d offers)",
                    shift_id, wave_num, expired_count
                )

        # ── Ongoing search / watchlist ────────────────────────────────────────
        # Keep notifying nurses until shift start, manual stop, or search closed —
        # including after one or more nurses have already accepted.
        search_still_open = not await asyncio.get_running_loop().run_in_executor(
            _executor, functools.partial(_is_search_closed_sync, db, shift_id)
        )
        if not filled and search_still_open:
            watchlist_interval = cfg.get("watchlist_interval_sec", 120)
            max_radius = base_radius + ((max_waves - 1) * radius_step)
            watchlist_wave = max_waves + 1

            # Normalise shift_start to naive UTC for safe comparison
            _ss = shift.shift_start
            shift_start_naive = (
                _ss.astimezone(timezone.utc).replace(tzinfo=None) if _ss.tzinfo else _ss
            )

            confirmed_at_entry = await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(_count_assignments_sync, db, shift_id)
            )
            entry_msg = (
                f"{confirmed_at_entry} nurse(s) confirmed — still searching for more staff…"
                if confirmed_at_entry > 0
                else "No nurses online right now — watching for available staff..."
            )
            await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_wave_update", {
                "status": "watching" if confirmed_at_entry == 0 else "receiving",
                "confirmed_count": confirmed_at_entry,
                "message": entry_msg,
            })
            logger.info(
                "[dispatch] shift %d entering ongoing search (interval=%ds, confirmed=%d)",
                shift_id, watchlist_interval, confirmed_at_entry,
            )

            while not filled and datetime.utcnow() < shift_start_naive:
                if await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(_is_search_closed_sync, db, shift_id)
                ):
                    filled = True
                    break
                if session.id in _cancelled_sessions:
                    filled = True  # suppress terminal shift_expired below
                    break
                if await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(_is_shift_cancelled_in_db_sync, db, shift_id)
                ):
                    cancel_dispatch_session(session.id)
                    filled = True
                    break

                time_remaining = (shift_start_naive - datetime.utcnow()).total_seconds()
                if time_remaining <= 0:
                    break

                sleep_for = min(watchlist_interval, time_remaining)
                await asyncio.sleep(sleep_for)

                # Re-check after sleeping
                if datetime.utcnow() >= shift_start_naive or session.id in _cancelled_sessions:
                    if session.id in _cancelled_sessions:
                        filled = True
                    break
                if await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(_is_shift_cancelled_in_db_sync, db, shift_id)
                ):
                    cancel_dispatch_session(session.id)
                    filled = True
                    break

                candidates = await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(
                        _find_candidates_sync, db, shift, max_radius, already_offered
                    )
                )
                candidates = [c for c in candidates if not _is_nurse_fatigued(c[0].id)]

                if not candidates:
                    await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_wave_update", {
                        "status": "watching",
                        "message": "Still searching — no nurses available yet...",
                    })
                    logger.info("[dispatch] shift %d watchlist: no candidates yet", shift_id)
                    continue

                # Nurses came online — dispatch to them
                actual_size = min(cfg["wave_sizes"][0], len(candidates))
                wave_candidates = candidates[:actual_size]
                nurse_ids = [u.id for u, _, _ in wave_candidates]
                already_offered.extend(nurse_ids)

                for nid in nurse_ids:
                    _record_offer_sent(nid)

                from .offer_policy import offer_expires_at as _offer_expires_at
                expires_at = _offer_expires_at(shift)
                offers = await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(
                        _create_offers_sync, db, session.id, shift_id,
                        nurse_ids, watchlist_wave, expires_at
                    )
                )

                fcm_tokens = await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(_get_device_tokens_sync, db, nurse_ids)
                )
                await _notify_wave(db, fcm_tokens, offers, shift, wave_timeout)

                await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_wave_update", {
                    "status": "dispatching",
                    "nurses_notified": len(offers),
                    "wave": watchlist_wave,
                    "message": f"Found {len(offers)} nurses — notifying...",
                })
                logger.info(
                    "[dispatch] shift %d watchlist wave %d: notified %d nurses",
                    shift_id, watchlist_wave, len(offers)
                )

                accepted = await _wait_for_acceptance(session.id, wave_timeout)
                if session.id in _cancelled_sessions or await asyncio.get_running_loop().run_in_executor(
                    _executor, functools.partial(_is_shift_cancelled_in_db_sync, db, shift_id)
                ):
                    cancel_dispatch_session(session.id)
                    filled = True
                    break

                if accepted:
                    filled = await _do_fill(
                        db, shift_id, session.id, watchlist_wave, dispatch_start, shift
                    )
                    if filled:
                        break
                else:
                    # Watchlist wave timed out — expire offers, keep watching
                    await asyncio.get_running_loop().run_in_executor(
                        _executor, functools.partial(
                            _expire_wave_offers_sync, db, session.id, watchlist_wave
                        )
                    )
                    await _notify_hospital(shift.hospital_user_id, shift_id, "dispatch_wave_update", {
                        "status": "watching",
                        "wave": watchlist_wave,
                        "message": "Nurses didn't respond — continuing to search...",
                    })
                    watchlist_wave += 1

        if not filled:
            now = datetime.utcnow()
            confirmed_count = await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(_count_assignments_sync, db, shift_id)
            )
            search_closed = await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(_is_search_closed_sync, db, shift_id)
            )
            _ss = shift.shift_start
            shift_start_naive = (
                _ss.astimezone(timezone.utc).replace(tzinfo=None) if _ss.tzinfo else _ss
            )
            if confirmed_count > 0 and not search_closed and now >= shift_start_naive:
                await asyncio.get_running_loop().run_in_executor(
                    _executor,
                    functools.partial(_finalize_search_closed_sync, db, shift_id, now, "shift_start"),
                )
                await _notify_hospital(shift.hospital_user_id, shift_id, "shift_search_stopped", {
                    "confirmed_count": confirmed_count,
                    "message": "Shift started — staff finalized.",
                })
                await _notify_hospital(shift.hospital_user_id, shift_id, "shift_filled", {
                    "confirmed_count": confirmed_count,
                    "message": "Staff finalized for this shift.",
                })
                logger.info(
                    "[dispatch] shift %d auto-closed at shift start (%d confirmed)",
                    shift_id, confirmed_count,
                )
                return
            if confirmed_count > 0 and not search_closed:
                logger.info(
                    "[dispatch] shift %d: %d confirmed — search stays open until close",
                    shift_id,
                    confirmed_count,
                )
                return

            # All waves exhausted with no confirmed staff (or search already closed)
            await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(
                    _update_shift_status_sync, db, shift_id, models.ShiftRequestStatus.expired
                )
            )
            await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(
                    _update_session_sync, db, session.id,
                    models.DispatchSessionStatus.failed, None, True
                )
            )
            await asyncio.get_running_loop().run_in_executor(
                _executor, functools.partial(
                    _write_timeline_event_sync, db, shift_id, SHIFT_EXPIRED, shift.city_id,
                    None, {
                        "waves_tried": len(wave_sizes),
                        "nurses_offered": len(already_offered),
                    }
                )
            )
            # Differentiate: "no nurses at all" vs "nurses found but none accepted"
            if len(already_offered) == 0:
                expired_msg = "Shift window passed with no nurses online. Re-post to try again."
            else:
                expired_msg = "No nurse accepted before the shift window closed."
            await _notify_hospital(shift.hospital_user_id, shift_id, "shift_expired", {
                "message": expired_msg,
                "nurses_notified": len(already_offered),
            })
            # Task 14: Metrics — dispatch expired
            _metrics["dispatches_expired"] += 1

            sentry_sdk.set_tag("dispatch.outcome", "expired")
            logger.warning("[dispatch] shift %d EXPIRED after %d waves", shift_id, max_waves)

    finally:
        # Always clean up dispatch event and cancellation signal
        dispatch_events.pop(session.id, None)
        _cancelled_sessions.discard(session.id)


async def start_dispatch(shift_id: int) -> asyncio.Task:
    """Launch dispatch as a background asyncio task. Non-blocking."""
    if not is_dispatch_enabled():
        _dispatch_log("warning", "dispatch.kill_switch",
                      shift_id=shift_id,
                      reason="DISPATCH_ENABLED=false")
        # Return a dummy completed task so callers don't need to handle None
        async def _noop(): return None
        return asyncio.create_task(_noop())
    task = asyncio.create_task(run_dispatch(shift_id))
    _dispatch_log("info", "dispatch.task_created", shift_id=shift_id)
    return task
