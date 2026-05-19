"""
Dispatch WebSocket + Offer accept/decline routes.

Endpoints:
  WS   /ws/{user_id}                         — real-time dispatch + hospital updates
  POST /dispatch/offers/{offer_id}/accept     — nurse accepts dispatch offer
  POST /dispatch/offers/{offer_id}/decline    — nurse declines dispatch offer
  GET  /dispatch/offers/pending               — nurse gets pending offer (reconnect recovery)
"""
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
from ..dispatch.engine import dispatch_events, _metrics
from ..dispatch.events import OFFER_ACCEPTED, OFFER_DECLINED, OFFER_TIMED_OUT

logger = logging.getLogger(__name__)

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
    # Lock the offer row — SKIP LOCKED means only one transaction gets it
    offer = (
        db.query(models.DispatchOffer)
        .filter(
            models.DispatchOffer.id == offer_id,
            models.DispatchOffer.nurse_user_id == current_user.id,
            models.DispatchOffer.status == models.OfferStatus.pending,
        )
        .with_for_update(skip_locked=True)
        .first()
    )

    if not offer:
        # Either already accepted by someone else, expired, or doesn't belong to this user
        existing = db.query(models.DispatchOffer).filter(
            models.DispatchOffer.id == offer_id
        ).first()
        if existing and existing.status == models.OfferStatus.accepted:
            raise HTTPException(status_code=409, detail="This shift has already been filled.")
        if existing and existing.status in (models.OfferStatus.timed_out, models.OfferStatus.cancelled):
            raise HTTPException(status_code=410, detail="This offer has expired.")
        raise HTTPException(status_code=404, detail="Offer not found or no longer available.")

    # Task 9: Validate shift is still accepting assignments (inside same transaction)
    active_shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == offer.shift_request_id,
        models.ShiftRequest.status.in_([
            models.ShiftRequestStatus.dispatching,
            models.ShiftRequestStatus.open,
        ]),
    ).first()
    if not active_shift:
        db.rollback()
        raise HTTPException(status_code=409, detail="This shift is no longer accepting assignments.")

    # Check offer hasn't expired
    if offer.expires_at < datetime.utcnow():
        offer.status = models.OfferStatus.timed_out
        offer.responded_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=410, detail="This offer has expired.")

    # FUTURE: AssignmentConflictValidator (§24.5) — check for overlapping shifts
    # For now: basic check — nurse doesn't have another active assignment
    active_assignment = db.query(models.LiveAssignment).filter(
        models.LiveAssignment.nurse_user_id == current_user.id,
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

    # Accept the offer
    now = datetime.utcnow()
    offer.status = models.OfferStatus.accepted
    offer.responded_at = now

    # Timeline event
    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == offer.shift_request_id
    ).first()
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
        rs = models.ReliabilityScore(user_id=current_user.id, total_offers=1, accepted=1)
        db.add(rs)
    db.commit()

    _metrics["offers_accepted"] += 1  # keep dispatch metrics in sync

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
        "message": "Assignment pending confirmation.",
    }


@offer_router.post("/offers/{offer_id}/decline")
def decline_offer(
    offer_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nurse declines a dispatch offer. Updates reliability score."""
    offer = db.query(models.DispatchOffer).filter(
        models.DispatchOffer.id == offer_id,
        models.DispatchOffer.nurse_user_id == current_user.id,
        models.DispatchOffer.status == models.OfferStatus.pending,
    ).first()

    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found or no longer pending.")

    now = datetime.utcnow()
    offer.status = models.OfferStatus.declined
    offer.responded_at = now

    shift = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.id == offer.shift_request_id
    ).first()

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
        rs = models.ReliabilityScore(user_id=current_user.id, total_offers=1, declined=1, score=80.0)
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
            models.DispatchOffer.status == models.OfferStatus.pending,
            models.DispatchOffer.expires_at > now,
        )
        .all()
    )

    payload = []
    for offer, shift in rows:
        if _expire_shift_if_past_start_unfilled(db, shift):
            db.refresh(shift)
            db.refresh(offer)
        if shift.status not in (
            models.ShiftRequestStatus.open,
            models.ShiftRequestStatus.dispatching,
        ):
            continue
        if offer.status != models.OfferStatus.pending or offer.expires_at <= now:
            continue
        payload.append({
            "offer_id": offer.id,
            "shift_id": shift.id,
            "hospital_name": shift.hospital_name,
            "role": shift.role_required.value,
            "urgency": shift.urgency.value,
            "shift_start": shift.shift_start.isoformat(),
            "pay_rate": shift.pay_rate,
            "expires_at": offer.expires_at.isoformat(),
            "expires_in_sec": max(0, int((offer.expires_at - now).total_seconds())),
        })

    return {"offers": payload}
