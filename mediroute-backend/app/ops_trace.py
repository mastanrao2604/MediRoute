"""
Centralized operational trace logging for pilot incident reconstruction.

All lifecycle / reconcile / WS delivery logs use compact JSON lines with
correlation keys: sid, aid, oid, uid, rid, stage, actor, trigger, typ, cid.

No payloads, tokens, phone numbers, or PII.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("mediroute.ops")


def _ts() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _emit(level: str, event: str, **fields: Any) -> None:
    entry: dict[str, Any] = {"event": event, "ts": _ts()}
    entry.update({k: v for k, v in fields.items() if v is not None})
    msg = json.dumps(entry, default=str)
    log_fn = getattr(logger, level if level in ("debug", "info", "warning", "error") else "info")
    log_fn(msg)


def shift_lifecycle(ev: str, **fields: Any) -> None:
    """Shift timeline: created, dispatch_started, cancelled, expired, reposted, …"""
    _emit("info", "shift.lifecycle", ev=ev, **fields)


def assignment_lifecycle(ev: str, **fields: Any) -> None:
    """Assignment timeline: invited, applied, revoked, recruiter_confirmed, no_show, …"""
    _emit("info", "assignment.lifecycle", ev=ev, **fields)


def reconcile_trace(ev: str, **fields: Any) -> None:
    """DB-authoritative reconcile fetch / recovery."""
    _emit("info", "dispatch.reconcile", ev=ev, **fields)


def ws_trace(ev: str, level: str = "info", **fields: Any) -> None:
    """WebSocket connect / send / prune / delivery mirror."""
    _emit(level, "ws.delivery", ev=ev, **fields)


def recovery_trace(ev: str, level: str = "info", **fields: Any) -> None:
    """Reconnect, stale cleanup, hydration, missed-event recovery."""
    _emit(level, "ops.recovery", ev=ev, **fields)


def startup_trace(ev: str, level: str = "info", **fields: Any) -> None:
    """Deploy / schema validation — Alembic authoritative, no runtime DDL."""
    _emit(level, "startup.schema", ev=ev, **fields)


def op_failure(domain: str, ev: str, **fields: Any) -> None:
    """Structured operational failure — never silent lifecycle corruption."""
    _emit("warning", domain, ev=ev, **fields)


def api_timing_trace(endpoint: str, **fields: Any) -> None:
    """Request-path timing breakdown for dashboard / reconcile hot paths."""
    _emit("info", "api.timing", endpoint=endpoint, **fields)


def dispatch_timing_trace(phase: str, **fields: Any) -> None:
    """Dispatch loop phase timing — wave wait, DB hold, notify fanout."""
    _emit("info", "dispatch.timing", phase=phase, **fields)
