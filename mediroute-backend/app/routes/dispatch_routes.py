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
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..dependencies import get_current_user
from ..utils.security import decode_access_token
from .. import models
from ..ws_manager import ws_manager
from ..dispatch.engine import dispatch_events, _metrics, record_nurse_accept_sync
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
        if token_user_id is None or token_user_id != user_id:
            await websocket.close(code=4001)
            return
    except Exception:
        await websocket.close(code=4001)
        return

    # Register connection
    await ws_manager.connect(user_id, websocket)
    logger.info("[ws] user %d connected (total: %d)", user_id, ws_manager.connection_count)

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
        logger.info("[ws] user %d disconnected (total: %d)", user_id, ws_manager.connection_count)


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
            raise HTTPException(status_code=409, detail="You are already confirmed for this shift.")
        raise HTTPException(status_code=404, detail="Offer not found or no longer available.")

    active_shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == offer.shift_request_id,
    ).first()
    if not active_shift or not shift_search_open(active_shift):
        db.rollback()
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
    ).first()
    if same_shift:
        raise HTTPException(status_code=409, detail="You are already confirmed for this shift.")

    active_assignment = db.query(models.LiveAssignment).filter(
        models.LiveAssignment.nurse_user_id == current_user.id,
        models.LiveAssignment.shift_request_id != offer.shift_request_id,
        models.LiveAssignment.status.in_([
            models.AssignmentStatus.confirmed,
            models.AssignmentStatus.checked_in,
        ]),
    ).first()
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

    confirmed_count = (
        db.query(models.LiveAssignment)
        .filter(models.LiveAssignment.shift_request_id == offer.shift_request_id)
        .count()
    )
    nurses_required = getattr(shift, "nurses_required", None) or 1
    nurse_name = current_user.name or f"Staff #{current_user.id}"

    async def _notify_accept() -> None:
        await ws_manager.send(current_user.id, {
            "type": "assignment_confirmed",
            "assignment_id": assignment.id,
            "shift_id": offer.shift_request_id,
            "hospital_name": shift.hospital_name if shift else "",
            "shift_start": utc_iso(shift.shift_start) if shift else None,
            "message": "Shift confirmed — get ready for your shift.",
        })
        await ws_manager.send(shift.hospital_user_id, {
            "type": "nurse_accepted",
            "shift_id": offer.shift_request_id,
            "nurse_name": nurse_name,
            "nurse_user_id": current_user.id,
            "confirmed_count": confirmed_count,
            "nurses_required": nurses_required,
            "message": (
                f"{nurse_name} accepted ({confirmed_count} of {nurses_required} confirmed) "
                "— still searching for more staff."
            ),
        })

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
        "message": "Shift confirmed — the hospital can still add more staff until search closes.",
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
        if not offer_respondable(offer, shift):
            continue
        if shift.id in seen_shift_ids:
            continue
        seen_shift_ids.add(shift.id)
        payload.append(_offer_payload(offer, shift, now, db))

    return {"offers": payload}
