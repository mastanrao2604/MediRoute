"""
Dispatch janitor — periodic cleanup background task.

Runs as a long-running asyncio task started in app lifespan.
Responsibilities:
  1. Expire offers whose expires_at has passed but status is still 'pending'
     (nurse never responded — WS disconnect, FCM not received, etc.)
  2. Clean up dispatch_events dict for sessions that are no longer active
     (prevents memory growth over time)
  3. Update reliability scores for timed-out nurses

Run interval: 30 seconds. Light enough to be always-on on Render Standard.
"""
import asyncio
import logging
import time
from datetime import datetime
from functools import partial

import sentry_sdk

from ..database import SessionLocal
from .. import models
from .engine import dispatch_events, _executor, _update_reliability_on_event_sync, _offer_fatigue, _OFFER_FATIGUE_WINDOW_SEC, _expire_shift_past_start_unfilled_sync
from .events import OFFER_TIMED_OUT
from ..ws_manager import ws_manager

logger = logging.getLogger(__name__)

# How often the janitor wakes up (seconds)
JANITOR_INTERVAL_SEC = 30

# ── Janitor health tracking ────────────────────────────────────────────────────
# Monotonic timestamp of last successful tick. 0.0 = never ran.
_janitor_last_tick_at: float = 0.0
_janitor_tick_count: int = 0
_janitor_error_count: int = 0


def get_janitor_health() -> dict:
    """
    Return lightweight janitor liveness info.
    Called by GET /admin/ops/health-snapshot — no DB query, pure in-memory.
    """
    now = time.monotonic()
    last = _janitor_last_tick_at
    age_sec = round(now - last) if last > 0 else None
    # Janitor is considered "stale" if it hasn’t ticked within 3 × its interval
    stale = (age_sec is None) or (age_sec > JANITOR_INTERVAL_SEC * 3)
    return {
        "alive": not stale,
        "last_tick_age_sec": age_sec,
        "tick_count": _janitor_tick_count,
        "error_count": _janitor_error_count,
        "interval_sec": JANITOR_INTERVAL_SEC,
    }


async def _janitor_tick() -> None:
    """Single janitor sweep. All DB ops in executor — never blocks event loop."""
    loop = asyncio.get_running_loop()
    db = None  # guard: SessionLocal() may fail; finally checks before closing
    try:
        db = SessionLocal()
        now = datetime.utcnow()

        # 0. Prune stale WebSocket connections (Task 2)
        pruned = await ws_manager.prune_stale()
        if pruned:
            logger.info("[janitor] pruned %d stale WebSocket connections", pruned)

        # 1. Close pending invitations only after shift start has passed
        expired_offers = await loop.run_in_executor(
            _executor,
            partial(
                lambda d, t: d.query(models.DispatchOffer)
                .join(
                    models.ShiftRequest,
                    models.DispatchOffer.shift_request_id == models.ShiftRequest.id,
                )
                .filter(
                    models.DispatchOffer.status.in_(
                        (
                            models.OfferStatus.pending,
                            models.OfferStatus.declined,
                            models.OfferStatus.timed_out,
                        )
                    ),
                    models.ShiftRequest.shift_start <= t,
                )
                .all(),
                db, now
            )
        )

        if expired_offers:
            logger.info("[janitor] expiring %d stale offers", len(expired_offers))
            for offer in expired_offers:
                if offer.status == models.OfferStatus.pending:
                    offer.status = models.OfferStatus.timed_out
                    offer.responded_at = now

            await loop.run_in_executor(_executor, partial(lambda d: d.commit(), db))

            # Update reliability for each nurse
            for offer in expired_offers:
                await loop.run_in_executor(
                    _executor,
                    partial(_update_reliability_on_event_sync, db, offer.nurse_user_id, "timed_out")
                )

        # 2. Clean up dispatch_events — DB-aware (Task 12)
        if dispatch_events:
            active_session_ids = await loop.run_in_executor(
                _executor,
                partial(
                    lambda d: set(
                        row[0] for row in d.query(models.DispatchSession.id).filter(
                            models.DispatchSession.status == models.DispatchSessionStatus.active
                        ).all()
                    ),
                    db
                )
            )
            stale_ids = [sid for sid in list(dispatch_events.keys()) if sid not in active_session_ids]
            for sid in stale_ids:
                dispatch_events.pop(sid, None)
            if stale_ids:
                logger.debug("[janitor] cleaned %d stale dispatch events", len(stale_ids))

        # 3. Prune offer fatigue dict for expired windows (Task 5)
        if _offer_fatigue:
            cutoff = time.monotonic() - _OFFER_FATIGUE_WINDOW_SEC
            for uid in list(_offer_fatigue.keys()):
                _offer_fatigue[uid] = [t for t in _offer_fatigue[uid] if t > cutoff]
                if not _offer_fatigue[uid]:
                    del _offer_fatigue[uid]

        # 4. Mark dispatch sessions as failed if shift is expired but session is still active
        stale_sessions = await loop.run_in_executor(
            _executor,
            partial(
                lambda d: d.query(models.DispatchSession).join(
                    models.ShiftRequest,
                    models.DispatchSession.shift_request_id == models.ShiftRequest.id
                ).filter(
                    models.DispatchSession.status == models.DispatchSessionStatus.active,
                    models.ShiftRequest.status.in_([
                        models.ShiftRequestStatus.expired,
                        models.ShiftRequestStatus.cancelled,
                    ]),
                ).all(),
                db
            )
        )
        if stale_sessions:
            for session in stale_sessions:
                session.status = models.DispatchSessionStatus.failed
                session.completed_at = now
            await loop.run_in_executor(_executor, partial(lambda d: d.commit(), db))
            logger.info("[janitor] marked %d stale sessions as failed", len(stale_sessions))

        # 5. Expire open/dispatching shifts past start without recruiter-confirmed staff
        past_start_shifts = await loop.run_in_executor(
            _executor,
            partial(
                lambda d, t: d.query(models.ShiftRequest)
                .filter(
                    models.ShiftRequest.status.in_([
                        models.ShiftRequestStatus.open,
                        models.ShiftRequestStatus.dispatching,
                    ]),
                    models.ShiftRequest.shift_start <= t,
                )
                .all(),
                db, now
            )
        )
        if past_start_shifts:
            expired_count = 0
            for shift in past_start_shifts:
                did = await loop.run_in_executor(
                    _executor,
                    partial(_expire_shift_past_start_unfilled_sync, db, shift),
                )
                if did:
                    expired_count += 1
            if expired_count:
                logger.info(
                    "[janitor] reconciled %d past-start shift(s) (fill or expire)",
                    expired_count,
                )

    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.error("[janitor] tick error: %s", exc, exc_info=True)
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
    else:
        # Only update heartbeat timestamp on a clean tick (no exception)
        global _janitor_last_tick_at, _janitor_tick_count
        _janitor_last_tick_at = time.monotonic()
        _janitor_tick_count += 1
    finally:
        if db is not None:
            db.close()


async def run_janitor() -> None:
    """
    Long-running janitor loop. Started once in app lifespan.
    Wakes every JANITOR_INTERVAL_SEC seconds.

    IMPORTANT: each _janitor_tick() call is wrapped in its own try/except so
    that a single tick failure (DB pool exhausted, unexpected exception, etc.)
    can never kill the loop. The janitor MUST run forever.
    """
    logger.info("[janitor] started (interval: %ds)", JANITOR_INTERVAL_SEC)
    while True:
        await asyncio.sleep(JANITOR_INTERVAL_SEC)
        try:
            await _janitor_tick()
        except Exception as exc:
            # Log and continue — one bad tick must never stop the janitor
            global _janitor_error_count
            _janitor_error_count += 1
            sentry_sdk.capture_exception(exc)
            logger.error("[janitor] unhandled tick exception (loop continues): %s", exc, exc_info=True)
