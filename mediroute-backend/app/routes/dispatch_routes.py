"""
Dispatch WebSocket + Offer accept/decline routes.

Endpoints:
  WS   /ws/{user_id}                         — real-time dispatch + hospital updates
  POST /dispatch/offers/{offer_id}/accept     — nurse accepts dispatch offer
  POST /dispatch/offers/{offer_id}/decline    — nurse declines dispatch offer
  GET  /dispatch/offers/pending               — nurse gets pending offer (reconnect recovery)
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..dependencies import get_current_user
from ..utils.security import decode_access_token
from .. import models
from ..ws_manager import ws_manager
from ..dispatch.engine import dispatch_events, _metrics, record_nurse_accept_sync, deliver_nurse_message
from ..dispatch.events import OFFER_ACCEPTED, OFFER_DECLINED, OFFER_TIMED_OUT
from ..utils.datetime_util import utc_iso
from ..dispatch.eligibility import nurse_accept_eligible
from ..dispatch.offer_policy import (
    RESPONDABLE_OFFER_STATUSES,
    offer_respondable,
    seconds_until_shift_start,
    shift_accepting_staff,
    shift_search_open,
    shift_start_utc_naive,
)
from ..ops_trace import shift_lifecycle, assignment_lifecycle, reconcile_trace, ws_trace, op_failure, api_timing_trace

logger = logging.getLogger(__name__)


def _offer_payload(
    offer: models.DispatchOffer,
    shift: models.ShiftRequest,
    now: datetime,
    db: Session,
) -> dict:
    accept_ok, dist_km, block_msg = nurse_accept_eligible(db, shift, offer.nurse_user_id)
    return {
        "offer_id": offer.id,
        "shift_id": shift.id,
        "hospital_name": shift.hospital_name,
        "role": shift.role_required.value,
        "urgency": shift.urgency.value,
        "shift_start": utc_iso(shift.shift_start),
        "pay_rate": shift.pay_rate,
        "offer_status": offer.status.value,
        "respond_by_sec": seconds_until_shift_start(shift, now),
        "accept_eligible": accept_ok,
        "distance_km": dist_km,
        "accept_blocked_message": block_msg or None,
    }

router = APIRouter(tags=["Dispatch"])
offer_router = APIRouter(prefix="/dispatch", tags=["Dispatch"])

# ── WebSocket handler ─────────────────────────────────────────────────────────

@router.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: int,
    token: str = Query(..., description="JWT access token"),
):
    """
    Real-time WebSocket connection.

    Authentication: token query param (WS headers not reliably supported on Android).
    Receives: dispatch offers, hospital shift status updates, assignment confirmations.
    Sends: ping/pong keepalive (client sends {"type":"ping"}, server replies {"type":"pong"}).

    Reconnect: client should reconnect with exponential backoff (1s→2s→4s→8s→30s max).
    On reconnect: call GET /dispatch/offers/pending to recover any missed offers.
    """
    # Authenticate via JWT
    try:
        from jose import JWTError
        payload = decode_access_token(token)
        token_user_id: int = payload.get("user_id")
        if token_user_id is None:
            logger.warning("[ws] auth rejected path_uid=%s reason=missing_user_id", user_id)
            await websocket.close(code=4001)
            return
        if token_user_id != user_id:
            logger.warning(
                "[ws] auth rejected path_uid=%s token_uid=%s reason=user_mismatch",
                user_id,
                token_user_id,
            )
            await websocket.close(code=4001)
            return
    except JWTError as exc:
        logger.warning("[ws] auth rejected path_uid=%s reason=jwt_invalid err=%s", user_id, exc)
        await websocket.close(code=4001)
        return
    except Exception as exc:
        logger.warning("[ws] auth rejected path_uid=%s reason=unexpected err=%s", user_id, exc)
        await websocket.close(code=4001)
        return

    # Register connection
    await ws_manager.connect(user_id, websocket)
    ws_trace("connected", uid=user_id, total=ws_manager.connection_count)

    try:
        while True:
            try:
                text = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                break

            # Handle client messages
            try:
                msg = json.loads(text)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                    ws_manager.record_pong(user_id)  # Task 2: update stale-eviction timestamp
                elif msg.get("type") == "heartbeat":
                    # Client-side heartbeat acknowledgement
                    pass
            except (json.JSONDecodeError, Exception):
                pass  # non-JSON or unexpected message — ignore

    except Exception as exc:
        logger.debug("[ws] user %d connection error: %s", user_id, exc)
    finally:
        ws_manager.disconnect(user_id)
        ws_trace("disconnected", uid=user_id, total=ws_manager.connection_count)


# ── Offer routes ──────────────────────────────────────────────────────────────

@offer_router.post("/offers/{offer_id}/accept")
def accept_offer(
    offer_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Nurse accepts a dispatch offer. First-accept-wins.

    SELECT FOR UPDATE SKIP LOCKED ensures only one nurse wins
    even with concurrent accepts from the same wave.
    Fires the asyncio.Event for the dispatch engine to stop waiting.
    """
    offer = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.id == offer_id,
            models.DispatchOffer.nurse_user_id == current_user.id,
            models.DispatchOffer.status.in_(RESPONDABLE_OFFER_STATUSES),
        )
        .with_for_update(skip_locked=True)
        .first()
    )

    if not offer:
        existing = db.query(models.DispatchOffer).filter(
            models.DispatchOffer.id == offer_id,
            models.DispatchOffer.nurse_user_id == current_user.id,
        ).first()
        if existing and existing.status == models.OfferStatus.accepted:
            raise HTTPException(status_code=409, detail="You have already applied for this shift.")
        raise HTTPException(status_code=404, detail="Offer not found or no longer available.")

    active_shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == offer.shift_request_id,
    ).first()
    if not active_shift:
        raise HTTPException(status_code=404, detail="Shift not found.")

    from ..routes.shifts import _expire_shift_if_past_start_unfilled

    if _expire_shift_if_past_start_unfilled(db, active_shift):
        db.refresh(active_shift)
        db.refresh(offer)

    if active_shift.status in (
        models.ShiftRequestStatus.cancelled,
        models.ShiftRequestStatus.expired,
        models.ShiftRequestStatus.filled,
    ):
        raise HTTPException(
            status_code=410,
            detail="This shift is no longer available.",
        )
    if not shift_search_open(active_shift):
        raise HTTPException(
            status_code=410,
            detail="This shift has already started or staff search was closed.",
        )

    if shift_start_utc_naive(active_shift.shift_start) <= datetime.utcnow():
        raise HTTPException(
            status_code=410,
            detail="This shift has already started.",
        )

    same_shift = db.query(models.LiveAssignment).filter(
        models.LiveAssignment.shift_request_id == offer.shift_request_id,
        models.LiveAssignment.nurse_user_id == current_user.id,
        models.LiveAssignment.status.notin_([
            models.AssignmentStatus.cancelled,
            models.AssignmentStatus.completed,
            models.AssignmentStatus.no_show,
        ]),
    ).first()
    if same_shift:
        raise HTTPException(status_code=409, detail="You have already applied for this shift.")

    from ..dispatch.offer_policy import nurse_blocks_other_acceptances

    active_assignment = nurse_blocks_other_acceptances(
        db, current_user.id, exclude_shift_id=offer.shift_request_id
    )
    if active_assignment:
        raise HTTPException(
            status_code=409,
            detail="You already have an active assignment. Complete it before accepting new offers.",
        )

    accept_ok, dist_km, block_msg = nurse_accept_eligible(db, active_shift, current_user.id)
    if not accept_ok:
        logger.info(
            "[dispatch] accept blocked nurse=%d shift=%d dist_km=%s",
            current_user.id, active_shift.id, dist_km,
        )
        raise HTTPException(
            status_code=403,
            detail=block_msg or "This shift is currently available only for nearby staff.",
        )

    now = datetime.utcnow()
    offer.status = models.OfferStatus.accepted
    offer.responded_at = now
    offer.expires_at = shift_start_utc_naive(active_shift.shift_start)

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == offer.shift_request_id
    ).first()
    try:
        assignment = record_nurse_accept_sync(
            db,
            offer.shift_request_id,
            current_user.id,
            offer_id,
            now,
            commit=False,
        )
    except ValueError:
        raise HTTPException(status_code=410, detail="Staff search is closed for this shift.")

    assignment.status = models.AssignmentStatus.applied
    assignment.recruiter_confirmed_at = None

    event = models.ShiftTimelineEvent(
        shift_request_id=offer.shift_request_id,
        event_type=OFFER_ACCEPTED,
        actor_user_id=current_user.id,
        city_id=shift.city_id if shift else "HYD",
        payload={"offer_id": offer_id, "session_id": offer.session_id},
    )
    db.add(event)
    db.commit()

    # Update reliability score
    rs = db.query(models.ReliabilityScore).filter(
        models.ReliabilityScore.user_id == current_user.id
    ).first()
    if rs:
        rs.total_offers += 1
        rs.accepted += 1
        rs.last_calculated_at = now
    else:
        rs = models.ReliabilityScore(
            user_id=current_user.id, total_offers=1, accepted=1, score=100.0,
        )
        db.add(rs)
    db.commit()

    _metrics["offers_accepted"] += 1  # keep dispatch metrics in sync

    applied_count = (
        db.query(models.LiveAssignment)
        .filter(
            models.LiveAssignment.shift_request_id == offer.shift_request_id,
            models.LiveAssignment.recruiter_confirmed_at.is_(None),
        )
        .count()
    )
    nurses_required = getattr(shift, "nurses_required", None) or 1
    nurse_name = current_user.name or f"Staff #{current_user.id}"

    async def _notify_accept() -> None:
        db_notify = SessionLocal()
        try:
            await deliver_nurse_message(db_notify, current_user.id, {
                "type": "application_submitted",
                "assignment_id": assignment.id,
                "shift_id": offer.shift_request_id,
                "hospital_name": shift.hospital_name if shift else "",
                "shift_start": utc_iso(shift.shift_start) if shift else None,
                "application_status": "applied",
                "lifecycle_stage": "applied",
                "message": "Application submitted — the hospital is reviewing your profile.",
            })
            await ws_manager.send(shift.hospital_user_id, {
                "type": "nurse_applied",
                "shift_id": offer.shift_request_id,
                "nurse_name": nurse_name,
                "nurse_user_id": current_user.id,
                "applied_count": applied_count,
                "nurses_required": nurses_required,
                "message": (
                    f"{nurse_name} applied ({applied_count} applicant"
                    f"{'' if applied_count == 1 else 's'}) — review and confirm."
                ),
            })
        finally:
            db_notify.close()

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_notify_accept())
    except RuntimeError:
        pass

    # Signal dispatch engine (asyncio.Event.set())
    event_signal = dispatch_events.get(offer.session_id)
    if event_signal:
        event_signal.set()
        logger.info(
            "[dispatch] offer %d accepted by user %d — session %d signaled",
            offer_id, current_user.id, offer.session_id
        )
    else:
        logger.warning(
            "[dispatch] offer %d accepted but no event found for session %d "
            "(dispatch may have already completed or this is a reconnect)",
            offer_id, offer.session_id
        )

    return {
        "accepted": True,
        "offer_id": offer_id,
        "shift_id": offer.shift_request_id,
        "assignment_id": assignment.id,
        "message": "Application submitted — waiting for the hospital to confirm.",
        "application_status": "applied",
        "lifecycle_stage": "applied",
    }


@offer_router.post("/offers/{offer_id}/decline")
def decline_offer(
    offer_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nurse declines — may accept again until shift start."""
    offer = db.query(models.DispatchOffer).filter(
        models.DispatchOffer.id == offer_id,
        models.DispatchOffer.nurse_user_id == current_user.id,
    ).first()

    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found.")

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == offer.shift_request_id
    ).first()
    if not shift or not shift_accepting_staff(shift):
        raise HTTPException(status_code=410, detail="This shift is no longer available.")

    if offer.status == models.OfferStatus.declined:
        return {"declined": True, "offer_id": offer_id}

    if offer.status not in (
        models.OfferStatus.pending,
        models.OfferStatus.timed_out,
    ):
        raise HTTPException(status_code=409, detail="This invitation can no longer be declined.")

    now = datetime.utcnow()
    offer.status = models.OfferStatus.declined
    offer.responded_at = now

    event = models.ShiftTimelineEvent(
        shift_request_id=offer.shift_request_id,
        event_type=OFFER_DECLINED,
        actor_user_id=current_user.id,
        city_id=shift.city_id if shift else "HYD",
        payload={"offer_id": offer_id},
    )
    db.add(event)

    # Reliability score update
    rs = db.query(models.ReliabilityScore).filter(
        models.ReliabilityScore.user_id == current_user.id
    ).first()
    if rs:
        rs.total_offers += 1
        rs.declined += 1
        # Recalculate
        if rs.total_offers > 0:
            accept_rate = rs.accepted / rs.total_offers
            timeout_penalty = (rs.timed_out * 0.5) / max(rs.total_offers, 1)
            no_show_penalty = (rs.no_shows * 3.0) / max(rs.total_offers, 1)
            rs.score = max(0.0, min(100.0, (accept_rate * 100) - (timeout_penalty * 10) - (no_show_penalty * 10)))
        rs.last_calculated_at = now
    else:
        rs = models.ReliabilityScore(
            user_id=current_user.id, total_offers=1, declined=1, score=100.0,
        )
        db.add(rs)

    db.commit()

    logger.info("[dispatch] offer %d declined by user %d", offer_id, current_user.id)
    _metrics["offers_declined"] += 1  # keep dispatch metrics in sync
    return {"declined": True, "offer_id": offer_id}


@offer_router.get("/offers/pending")
def get_pending_offers(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get pending (not yet expired) offers for the authenticated nurse.
    Call on WebSocket reconnect to recover any missed offers.
    """
    from ..routes.shifts import _expire_shift_if_past_start_unfilled

    now = datetime.utcnow()
    rows = (
        db.query(models.DispatchOffer, models.ShiftRequest)
        .join(models.ShiftRequest, models.DispatchOffer.shift_request_id == models.ShiftRequest.id)
        .filter(
            models.DispatchOffer.nurse_user_id == current_user.id,
            models.DispatchOffer.status.in_(RESPONDABLE_OFFER_STATUSES),
        )
        .all()
    )

    payload = []
    seen_shift_ids = set()
    for offer, shift in rows:
        if _expire_shift_if_past_start_unfilled(db, shift):
            db.refresh(shift)
            db.refresh(offer)
        if shift.status in (
            models.ShiftRequestStatus.cancelled,
            models.ShiftRequestStatus.expired,
            models.ShiftRequestStatus.filled,
        ):
            continue
        if not offer_respondable(offer, shift):
            continue
        if shift.id in seen_shift_ids:
            continue
        seen_shift_ids.add(shift.id)
        payload.append(_offer_payload(offer, shift, now, db))

    return {"offers": payload}


@offer_router.get("/reconcile")
def reconcile_dispatch_state(
    trigger: str = Query("unknown", max_length=64),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Authoritative dispatch operational state for reconnect / resume / cold start.

    DB truth wins over WebSocket memory. Lightweight DB reads only — never calls
    GET /shifts/ serialization or dispatch runtime state.
    """
    t0 = time.perf_counter()
    terminal_shift_statuses = ("cancelled", "expired", "filled")

    if current_user.role == models.UserRole.recruiter:
        rows = (
            db.query(models.ShiftRequest.id, models.ShiftRequest.status)
            .filter(models.ShiftRequest.hospital_user_id == current_user.id)
            .order_by(models.ShiftRequest.created_at.desc())
            .limit(50)
            .all()
        )
        terminal_shifts = [
            {"shift_id": sid, "status": st.value if hasattr(st, "value") else st}
            for sid, st in rows
            if (st.value if hasattr(st, "value") else st) in terminal_shift_statuses
        ]
        api_timing_trace(
            "GET /dispatch/reconcile",
            role="recruiter",
            trigger=trigger,
            total_ms=int((time.perf_counter() - t0) * 1000),
            terminal=len(terminal_shifts),
        )
        reconcile_trace("recruiter", uid=current_user.id, trigger=trigger, terminal=len(terminal_shifts))
        return {
            "role": "recruiter",
            "refresh": ["shifts"],
            "terminal_shifts": terminal_shifts,
            "clear_offer_shift_ids": [t["shift_id"] for t in terminal_shifts],
        }

    from ..routes.shifts import _assignment_lifecycle_stage, _nurse_dashboard_archived_ids

    pending = get_pending_offers(current_user=current_user, db=db)
    offers = pending.get("offers") or []

    archived = _nurse_dashboard_archived_ids(db, current_user.id)
    assignment_rows = (
        db.query(models.LiveAssignment, models.ShiftRequest)
        .join(models.ShiftRequest, models.LiveAssignment.shift_request_id == models.ShiftRequest.id)
        .filter(models.LiveAssignment.nurse_user_id == current_user.id)
        .order_by(models.LiveAssignment.confirmed_at.desc())
        .limit(20)
        .all()
    )
    shifts = []
    for assignment, shift in assignment_rows:
        if shift.id in archived:
            continue
        stage = _assignment_lifecycle_stage(assignment, shift)
        assign_status = assignment.status.value if hasattr(assignment.status, "value") else assignment.status
        shifts.append({
            "id": shift.id,
            "status": shift.status.value if hasattr(shift.status, "value") else shift.status,
            "assignment": {
                "lifecycle_stage": stage,
                "status": assign_status,
            },
        })

    clear_offer_shift_ids: list[int] = []
    terminal_shifts: list[dict] = []
    active_shift_id = None
    active_assignment_stage = None

    offer_shift_ids = {o["shift_id"] for o in offers if o.get("shift_id") is not None}
    now = datetime.utcnow()
    offer_cutoff = now - timedelta(days=3)

    recent_offer_rows = (
        db.query(models.DispatchOffer, models.ShiftRequest)
        .join(models.ShiftRequest, models.DispatchOffer.shift_request_id == models.ShiftRequest.id)
        .filter(
            models.DispatchOffer.nurse_user_id == current_user.id,
            models.DispatchOffer.offered_at >= offer_cutoff,
        )
        .all()
    )
    for offer, shift in recent_offer_rows:
        sid = shift.id
        db_status = shift.status.value if hasattr(shift.status, "value") else shift.status
        if db_status in terminal_shift_statuses:
            clear_offer_shift_ids.append(sid)
            terminal_shifts.append({"shift_id": sid, "status": db_status})
            continue
        if offer.status not in RESPONDABLE_OFFER_STATUSES:
            clear_offer_shift_ids.append(sid)
            if offer.status in (models.OfferStatus.cancelled, models.OfferStatus.timed_out):
                terminal_shifts.append({"shift_id": sid, "status": db_status})
            continue
        if not offer_respondable(offer, shift):
            clear_offer_shift_ids.append(sid)

    for shift in shifts:
        sid = shift.get("id")
        if sid is None:
            continue
        db_status = shift.get("status")
        assignment = shift.get("assignment") or {}
        stage = assignment.get("lifecycle_stage")
        assign_status = assignment.get("status")

        if db_status in terminal_shift_statuses:
            clear_offer_shift_ids.append(sid)
            terminal_shifts.append({"shift_id": sid, "status": db_status})
            continue

        if assignment:
            clear_offer_shift_ids.append(sid)
            if stage in (
                "applied",
                "under_review",
                "recruiter_confirmed",
                "checked_in",
            ) or assign_status in ("applied", "confirmed", "checked_in"):
                if active_shift_id is None:
                    active_shift_id = sid
                    active_assignment_stage = stage or assign_status
            elif stage in ("cancelled", "not_selected", "no_show") or assign_status in ("cancelled", "no_show"):
                terminal_shifts.append({"shift_id": sid, "status": assign_status or stage or "cancelled"})
            continue

        if sid not in offer_shift_ids:
            clear_offer_shift_ids.append(sid)

    api_timing_trace(
        "GET /dispatch/reconcile",
        role=current_user.role.value,
        trigger=trigger,
        total_ms=int((time.perf_counter() - t0) * 1000),
        offers=len(offers),
        clear=len(set(clear_offer_shift_ids)),
    )
    reconcile_trace(
        "nurse",
        uid=current_user.id,
        trigger=trigger,
        offers=len(offers),
        clear=len(set(clear_offer_shift_ids)),
        active=active_shift_id,
    )

    return {
        "role": current_user.role.value,
        "pending_offers": offers,
        "valid_offer_ids": [o["offer_id"] for o in offers if o.get("offer_id") is not None],
        "clear_offer_shift_ids": sorted(set(clear_offer_shift_ids)),
        "terminal_shifts": terminal_shifts,
        "active_shift_id": active_shift_id,
        "active_assignment_stage": active_assignment_stage,
    }
