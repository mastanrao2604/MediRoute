"""
Best-effort lifecycle WebSocket delivery.

DB transitions commit first; WS/FCM are mirrors. Failures are logged and never
roll back or block lifecycle completion. Sync code paths (executor threads) schedule
notifications via the registered main event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Iterable, Optional, Set

logger = logging.getLogger(__name__)

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def register_dispatch_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def schedule_lifecycle_ws(
    coro_factory: Callable[[], Awaitable[None]],
    *,
    label: str,
    shift_id: Optional[int] = None,
) -> None:
    """Fire-and-forget lifecycle WS notify from sync or async callers."""

    async def _wrapped() -> None:
        try:
            await coro_factory()
        except Exception as exc:
            logger.warning(
                "[lifecycle.ws] %s sid=%s failed: %s",
                label,
                shift_id,
                exc,
                exc_info=True,
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_wrapped())
        return
    except RuntimeError:
        pass

    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(_wrapped(), _main_loop)
        return

    logger.warning(
        "[lifecycle.ws] %s sid=%s skipped — no event loop (DB truth preserved)",
        label,
        shift_id,
    )


async def _send_shift_expired(
    shift_id: int,
    hospital_user_id: int,
    nurse_user_ids: Iterable[int],
) -> None:
    from ..ws_manager import ws_manager

    hospital_ok = await ws_manager.send(
        hospital_user_id,
        {
            "type": "shift_expired",
            "shift_id": shift_id,
            "message": "No staff confirmed before the shift start time.",
            "lifecycle_stage": "expired",
        },
    )
    nurse_ids = list(set(nurse_user_ids))
    revoked = {
        "type": "offer_revoked",
        "shift_id": shift_id,
        "message": "This shift expired and is no longer available.",
        "lifecycle_stage": "expired",
    }
    nurse_ok = await ws_manager.broadcast(nurse_ids, revoked) if nurse_ids else 0
    logger.info(
        "[lifecycle.ws] shift_expired sid=%s hospital_delivered=%s nurses=%d/%d",
        shift_id,
        hospital_ok,
        nurse_ok,
        len(nurse_ids),
    )


async def _send_shift_search_stopped(
    shift_id: int,
    hospital_user_id: int,
    message: str,
) -> None:
    from ..ws_manager import ws_manager

    ok = await ws_manager.send(
        hospital_user_id,
        {
            "type": "shift_search_stopped",
            "shift_id": shift_id,
            "message": message,
        },
    )
    logger.info(
        "[lifecycle.ws] shift_search_stopped sid=%s hospital_delivered=%s",
        shift_id,
        ok,
    )


async def _send_shift_cancelled_nurses(
    shift_id: int,
    nurse_user_ids: Iterable[int],
    payload: dict,
) -> None:
    from ..ws_manager import ws_manager

    ids = list(set(nurse_user_ids))
    if not ids:
        return
    delivered = await ws_manager.broadcast(ids, payload)
    logger.info(
        "[lifecycle.ws] shift_cancelled sid=%s nurses=%d/%d",
        shift_id,
        delivered,
        len(ids),
    )


def notify_shift_expired(
    shift_id: int,
    hospital_user_id: int,
    nurse_user_ids: Iterable[int],
) -> None:
    ids = set(nurse_user_ids)
    schedule_lifecycle_ws(
        lambda: _send_shift_expired(shift_id, hospital_user_id, ids),
        label="shift_expired",
        shift_id=shift_id,
    )


def notify_shift_search_stopped(
    shift_id: int,
    hospital_user_id: int,
    message: str,
) -> None:
    schedule_lifecycle_ws(
        lambda: _send_shift_search_stopped(shift_id, hospital_user_id, message),
        label="shift_search_stopped",
        shift_id=shift_id,
    )


def notify_shift_cancelled_nurses(
    shift_id: int,
    nurse_user_ids: Iterable[int],
    payload: dict,
) -> None:
    schedule_lifecycle_ws(
        lambda: _send_shift_cancelled_nurses(shift_id, nurse_user_ids, payload),
        label="shift_cancelled_nurses",
        shift_id=shift_id,
    )
