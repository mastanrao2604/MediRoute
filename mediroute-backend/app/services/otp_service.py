"""
OTP Service — production-ready MSG91 integration.

Modes (controlled by environment):
  Production  MSG91_AUTH_KEY is set  → MSG91 OTP API (no DB OTP storage)
  Development  not set               → DB-stored OTP + dev log

Public API:
  send_otp(phone, db?)  → Optional[str]  (returns OTP in dev only)
  verify_otp(phone, otp, db?) → bool

Phone normalisation: strips +91 / 91 prefix, validates 10-digit Indian number.
Rate limit:  3 requests per 5 minutes per phone (in-memory, per-process).
"""

import os
import re
import logging
import secrets
from collections import deque
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

logger = logging.getLogger("uvicorn.error")

# ── Phone validation ──────────────────────────────────────────────────────────

_PHONE_RE = re.compile(r"^[6-9]\d{9}$")  # Indian mobile: starts with 6-9, 10 digits

# Lightweight fake-number detection — all-same-digit, sequential, test patterns.
# These pass format validation but are obviously not real numbers.
_FAKE_PATTERNS = [
    re.compile(r"^(\d)\1{9}$"),   # 9999999999, 8888888888, 7777777777, etc.
    re.compile(r"^1234567890$"),
    re.compile(r"^0987654321$"),
    re.compile(r"^9876543210$"),
]


def _is_fake_number(phone: str) -> bool:
    return any(p.match(phone) for p in _FAKE_PATTERNS)


def normalise_phone(raw: str) -> str:
    """
    Strip country code / leading zero, validate, and return bare 10-digit number.
    Raises HTTP 400 on invalid input.
    """
    cleaned = raw.strip()
    if cleaned.startswith("+91"):
        cleaned = cleaned[3:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    elif cleaned.startswith("0") and len(cleaned) == 11:
        cleaned = cleaned[1:]

    if not _PHONE_RE.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid phone number. "
                "Provide a 10-digit Indian mobile number (e.g. 9876543210)."
            ),
        )
    if _is_fake_number(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number. Please enter a real mobile number.",
        )
    return cleaned


# ── In-memory rate limiting ───────────────────────────────────────────────────
# Maps phone → deque of UTC datetimes for requests in the current window.
# In-process only — resets on restart (acceptable for single-process deployments).

_rate_store: dict[str, deque] = {}
_rate_lock = Lock()

_RL_MAX: int = int(os.getenv("OTP_RATE_LIMIT_MAX", "3"))          # max requests
_RL_WINDOW: int = int(os.getenv("OTP_RATE_LIMIT_WINDOW_SEC", "300"))  # 5 minutes
_RL_MIN_INTERVAL: int = int(os.getenv("OTP_MIN_INTERVAL_SEC", "30"))  # cooldown between requests


def _check_rate_limit(phone: str) -> None:
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=_RL_WINDOW)
    with _rate_lock:
        history = _rate_store.setdefault(phone, deque())
        # Expire entries outside the window
        while history and history[0] < window_start:
            history.popleft()
        # Enforce minimum interval between consecutive requests
        if history:
            seconds_since_last = (now - history[-1]).total_seconds()
            if seconds_since_last < _RL_MIN_INTERVAL:
                wait = int(_RL_MIN_INTERVAL - seconds_since_last) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Please wait {wait} seconds before requesting another OTP.",
                )
        if len(history) >= _RL_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Too many OTP requests. "
                    f"Maximum {_RL_MAX} per {_RL_WINDOW // 60} minutes. "
                    "Please wait and try again."
                ),
            )
        history.append(now)


# ── MSG91 OTP API ─────────────────────────────────────────────────────────────
# Uses /api/v5/otp (MSG91 generates and manages the OTP — never touches our DB).

def _msg91_send(phone_e164: str) -> None:
    """POST /api/v5/otp — ask MSG91 to generate and send the OTP."""
    import requests as _requests

    auth_key = os.environ["MSG91_AUTH_KEY"]
    template_id = os.environ["MSG91_TEMPLATE_ID"]

    payload = {
        "template_id": template_id,
        "mobile": phone_e164,
        "otp_length": 6,
    }
    headers = {
        "authkey": auth_key,
        "accept": "application/json",
        "content-type": "application/json",
    }

    try:
        resp = _requests.post(
            "https://api.msg91.com/api/v5/otp",
            json=payload,
            headers=headers,
            timeout=10,
        )
    except _requests.exceptions.Timeout:
        logger.error("MSG91 send-otp timed out for %s", phone_e164)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="OTP service timed out. Please try again.",
        )
    except _requests.exceptions.RequestException as exc:
        logger.error("MSG91 send-otp request error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OTP service unavailable. Please try again.",
        )

    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.status_code not in (200, 201) or data.get("type") == "error":
        logger.error(
            "MSG91 send-otp failed | status=%s | body=%s",
            resp.status_code,
            resp.text[:200],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send OTP. Please try again later.",
        )

    logger.info("MSG91 OTP dispatched to %s", phone_e164)


def _msg91_verify(phone_e164: str, otp: str) -> bool:
    """GET /api/v5/otp/verify — delegate verification to MSG91."""
    import requests as _requests

    auth_key = os.environ["MSG91_AUTH_KEY"]

    try:
        resp = _requests.get(
            "https://api.msg91.com/api/v5/otp/verify",
            params={"mobile": phone_e164, "otp": otp},
            headers={"authkey": auth_key, "accept": "application/json"},
            timeout=10,
        )
    except _requests.exceptions.Timeout:
        logger.error("MSG91 verify-otp timed out for %s", phone_e164)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="OTP verification service timed out. Please try again.",
        )
    except _requests.exceptions.RequestException as exc:
        logger.error("MSG91 verify-otp request error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OTP verification service unavailable. Please try again.",
        )

    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.status_code not in (200, 201):
        logger.error(
            "MSG91 verify-otp failed | status=%s | body=%s",
            resp.status_code,
            resp.text[:200],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OTP verification service returned an error. Please try again.",
        )

    # MSG91 returns {"type": "success", "message": "OTP verified successfully."} on match
    return data.get("type") == "success"


# ── Dev mode (no MSG91_AUTH_KEY) ──────────────────────────────────────────────
# Generates OTP locally, stores in OTPCode table, writes to otp_dev.log.
# The OTP value is returned to the caller so the route can include it in the
# response for local testing (NEVER do this in production).

def _dev_send(phone: str, db: Session) -> str:
    from .. import models

    otp_value = f"{secrets.randbelow(1_000_000):06d}"

    # Delete existing OTPs using individual ORM deletes — bulk delete()
    # with synchronize_session='evaluate' can corrupt the session state,
    # causing the subsequent INSERT to silently not persist.
    existing = db.query(models.OTPCode).filter(models.OTPCode.phone == phone).all()
    for rec in existing:
        db.delete(rec)
    db.flush()  # flush deletes before inserting

    record = models.OTPCode(
        phone=phone,
        otp=otp_value,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.add(record)
    db.commit()
    db.refresh(record)  # confirm the row was actually written

    _dev_log(phone, otp_value)
    return otp_value


def _dev_log(phone: str, otp: str) -> None:
    """Write to otp_dev.log — dev only, never called in MSG91 mode."""
    import os as _os

    log_path = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)),
        "..", "..", "otp_dev.log",
    )
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} | [DEV] phone={phone}  otp={otp}  (expires in 5 min)\n")
    except OSError:
        pass
    logger.warning("DEV MODE — OTP for %s written to otp_dev.log (not sent via SMS)", phone)


def _dev_verify(phone: str, otp: str, db: Session) -> bool:
    from .. import models

    record = (
        db.query(models.OTPCode)
        .filter(
            models.OTPCode.phone == phone,
            models.OTPCode.otp == otp,
            models.OTPCode.expires_at > datetime.utcnow(),
        )
        .first()
    )
    if not record:
        return False
    db.delete(record)
    db.commit()
    return True


# ── Public interface ──────────────────────────────────────────────────────────

def _is_production() -> bool:
    """
    Return True when running in production / MSG91 mode.

    Rules (first match wins):
      ENV=production          → production (MSG91)
      ENV=development         → dev (DB + log)
      MSG91_AUTH_KEY is set   → production (MSG91) — backward compat
      anything else           → dev (DB + log)
    """
    env = os.getenv("ENV", "").lower()
    if env == "production":
        return True
    if env == "development":
        return False
    # Fallback: key-based auto-detection
    return bool(os.getenv("MSG91_AUTH_KEY"))


def send_otp(phone: str, db: Optional[Session] = None) -> Optional[str]:
    """
    Validate phone, apply rate limit, and dispatch OTP.

    Returns:
        None         — in production (MSG91 manages the OTP)
        str (OTP)    — in dev mode only, for inclusion in the test response
    Raises:
        HTTP 400  — invalid phone
        HTTP 429  — rate limit exceeded
        HTTP 502  — MSG91 error (production)
        HTTP 504  — MSG91 timeout (production)
    """
    phone = normalise_phone(phone)
    _check_rate_limit(phone)

    if _is_production():
        phone_e164 = "91" + phone
        _msg91_send(phone_e164)
        return None

    # Dev mode
    if db is None:
        raise RuntimeError("send_otp: db session required in dev mode")
    return _dev_send(phone, db)


def verify_otp(phone: str, otp: str, db: Optional[Session] = None) -> bool:
    """
    Verify the OTP for the given phone.

    Returns True on success, False on wrong / expired OTP.
    Raises HTTP 400 on invalid input, HTTP 502/504 on external API errors.
    """
    phone = normalise_phone(phone)

    # Basic OTP format check
    otp_clean = otp.strip()
    if not otp_clean.isdigit() or len(otp_clean) != 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP must be exactly 6 digits.",
        )

    if _is_production():
        phone_e164 = "91" + phone
        return _msg91_verify(phone_e164, otp_clean)

    # Dev mode
    if db is None:
        raise RuntimeError("verify_otp: db session required in dev mode")
    return _dev_verify(phone, otp_clean, db)
