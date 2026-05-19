"""
OTP Service — production-ready MSG91 integration.

Modes (controlled by environment):
  MSG91 path     ENV=production and (SMS_PROVIDER unset legacy, or SMS_PROVIDER=msg91) with MSG91 keys
  Pilot / log    SMS_PROVIDER=log (or empty when set explicitly) — DB OTP + dev_otp even if ENV=production
                 Aligns with root render.yaml default SMS_PROVIDER=log before MSG91 is configured.
  Development    ENV=development — DB OTP + dev_otp
  Legacy         ENV unset — MSG91 if MSG91_AUTH_KEY set (and log rules), else DB OTP
  Optional       OTP_FORCE_DEV=true — DB OTP when ENV is not production (local overrides)

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


# ── Safe phone masking for logs ───────────────────────────────────────────────

def _mask_phone(phone: str) -> str:
    """Return a partially-masked phone number safe for production logs.

    Shows country-code prefix and last 4 digits; masks the middle 6.

    Examples:
        "919876543210"  →  "91XXXXXX3210"
        "9876543210"    →  "XXXXXX3210"
    """
    if len(phone) <= 4:
        return "****"
    # prefix = chars before the last 10 digits (e.g. "91" for E.164 Indian numbers)
    prefix_len = max(0, len(phone) - 10)
    return phone[:prefix_len] + "XXXXXX" + phone[-4:]


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
    sender_id = os.getenv("MSG91_SENDER_ID", "").strip()

    masked = _mask_phone(phone_e164)
    logger.info(
        "[OTP][MSG91] Initiating send | mobile=%s | template=%s | sender=%s",
        masked,
        template_id,
        sender_id or "(not set)",
    )

    payload: dict = {
        "template_id": template_id,
        "mobile": phone_e164,
        "otp_length": 6,
        "otp_expiry": 5,       # minutes — matches our rate-limit window
    }
    if sender_id:
        payload["sender"] = sender_id  # MSG91 v5 uses "sender" key

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
        logger.error("[OTP][MSG91] Send timed out | mobile=%s", masked)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="OTP service timed out. Please try again.",
        )
    except _requests.exceptions.RequestException as exc:
        logger.error("[OTP][MSG91] Send request error | mobile=%s | error=%s", masked, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OTP service unavailable. Please try again.",
        )

    try:
        data = resp.json()
    except Exception:
        data = {}

    logger.info(
        "[OTP][MSG91] Send response | mobile=%s | http_status=%s | type=%r | message=%r",
        masked,
        resp.status_code,
        data.get("type"),
        data.get("message", "")[:100],
    )

    if resp.status_code not in (200, 201) or data.get("type") == "error":
        logger.error(
            "[OTP][MSG91] Send FAILED | mobile=%s | http_status=%s | body=%s",
            masked,
            resp.status_code,
            resp.text[:400],
        )
        # Translate MSG91 error detail into a user-safe message
        msg91_msg = data.get("message", "").lower()
        if "balance" in msg91_msg or "credit" in msg91_msg or "recharge" in msg91_msg:
            detail = "SMS service is temporarily unavailable. Please try again later."
        elif "template" in msg91_msg or "dlt" in msg91_msg:
            detail = "SMS service configuration error. Please contact support."
        elif "sender" in msg91_msg or "header" in msg91_msg:
            detail = "SMS service configuration error. Please contact support."
        elif "block" in msg91_msg or "spam" in msg91_msg:
            detail = "This number cannot receive OTPs. Please use a different number."
        else:
            detail = "Failed to send OTP. Please try again."
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        )

    logger.info("[OTP][MSG91] OTP dispatched successfully | mobile=%s", masked)


def _msg91_verify(phone_e164: str, otp: str) -> bool:
    """GET /api/v5/otp/verify — delegate verification to MSG91."""
    import requests as _requests

    auth_key = os.environ["MSG91_AUTH_KEY"]
    masked = _mask_phone(phone_e164)

    logger.info("[OTP][MSG91] Initiating verify | mobile=%s", masked)

    try:
        resp = _requests.get(
            "https://api.msg91.com/api/v5/otp/verify",
            params={"mobile": phone_e164, "otp": otp},
            headers={"authkey": auth_key, "accept": "application/json"},
            timeout=10,
        )
    except _requests.exceptions.Timeout:
        logger.error("[OTP][MSG91] Verify timed out | mobile=%s", masked)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="OTP verification service timed out. Please try again.",
        )
    except _requests.exceptions.RequestException as exc:
        logger.error("[OTP][MSG91] Verify request error | mobile=%s | error=%s", masked, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OTP verification service unavailable. Please try again.",
        )

    try:
        data = resp.json()
    except Exception:
        data = {}

    logger.info(
        "[OTP][MSG91] Verify response | mobile=%s | http_status=%s | type=%r",
        masked,
        resp.status_code,
        data.get("type"),
    )

    if resp.status_code not in (200, 201):
        logger.error(
            "[OTP][MSG91] Verify API error | mobile=%s | http_status=%s | body=%s",
            masked,
            resp.status_code,
            resp.text[:300],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OTP verification service returned an error. Please try again.",
        )

    # MSG91 returns {"type": "success", "message": "OTP verified successfully."} on match
    success = data.get("type") == "success"
    if not success:
        logger.info("[OTP][MSG91] OTP mismatch | mobile=%s | type=%r", masked, data.get("type"))
    return success


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
    Return True when OTP send/verify should use the MSG91 OTP API (not DB dev_otp).

    Render pilot: dashboard sets SMS_PROVIDER=log — we must NOT call MSG91 even if ENV=production.

    When SMS_PROVIDER is unset, legacy behaviour is preserved: ENV=production → MSG91 path.
    When SMS_PROVIDER is set to anything other than msg91, we use DB OTP (Twilio etc. not wired here).
    """
    sms_raw = os.getenv("SMS_PROVIDER")
    if sms_raw is not None:
        s = sms_raw.strip().lower()
        if not s or s == "log":
            return False
        if s != "msg91":
            logger.warning(
                "[OTP] SMS_PROVIDER=%r is not msg91 — using DB-backed OTP (dev_otp). "
                "Set SMS_PROVIDER=msg91 and MSG91_* for live SMS.",
                sms_raw,
            )
            return False

    env = os.getenv("ENV", "").strip().lower()
    if env == "production":
        return True
    if env == "development":
        return False
    force_dev = os.getenv("OTP_FORCE_DEV", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if force_dev:
        return False
    # Fallback: key-based auto-detection (legacy deployments without ENV)
    return bool(os.getenv("MSG91_AUTH_KEY"))


def validate_production_config() -> None:
    """
    Call at startup to validate MSG91 configuration where required.

    - Logs which OTP mode is active (MSG91 / pilot DB + dev_otp / development)
    - Logs which env vars are set (names only, never values)
    - RAISES RuntimeError only if ENV=production on the MSG91 path but MSG91_* are missing

    Pilot: ENV=production + SMS_PROVIDER=log uses DB OTP; MSG91 keys are not required.
    """
    env = os.getenv("ENV", "").strip().lower()
    sms_raw = os.getenv("SMS_PROVIDER")
    auth_key_set = bool(os.getenv("MSG91_AUTH_KEY"))
    template_id_set = bool(os.getenv("MSG91_TEMPLATE_ID"))
    sender_id_set = bool(os.getenv("MSG91_SENDER_ID"))
    force_dev_requested = os.getenv("OTP_FORCE_DEV", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    logger.info(
        "[OTP] Config | ENV=%r | SMS_PROVIDER=%r | OTP_FORCE_DEV=%s | MSG91_AUTH_KEY=%s | MSG91_TEMPLATE_ID=%s | MSG91_SENDER_ID=%s",
        env,
        sms_raw,
        "requested (ignored on MSG91 path)" if force_dev_requested else "off",
        "SET" if auth_key_set else "MISSING",
        "SET" if template_id_set else "MISSING",
        "SET" if sender_id_set else "not set (optional)",
    )

    if env == "production":
        using_msg91 = True
        if sms_raw is not None:
            s = sms_raw.strip().lower()
            if not s or s == "log" or s != "msg91":
                using_msg91 = False
        else:
            # Unset SMS_PROVIDER preserves legacy behaviour: MSG91 in production when keys exist / required
            using_msg91 = True

        if using_msg91 and force_dev_requested:
            logger.warning(
                "[OTP] OTP_FORCE_DEV is set but ignored — ENV=production SMS path is MSG91 "
                "(SMS_PROVIDER unset or msg91)."
            )

        if not using_msg91:
            logger.warning(
                "[OTP] ENV=production with SMS_PROVIDER=%r — DB-backed OTP with dev_otp "
                "(pilot/log mode). MSG91 vars not required.",
                sms_raw,
            )
            logger.info("[OTP] Mode: PILOT (DB OTP) — no MSG91 API calls")
            return

        missing = [v for v, ok in [
            ("MSG91_AUTH_KEY", auth_key_set),
            ("MSG91_TEMPLATE_ID", template_id_set),
        ] if not ok]
        if missing:
            raise RuntimeError(
                f"[OTP] FATAL: ENV=production (SMS_PROVIDER unset or msg91) but MSG91 env vars missing: "
                f"{', '.join(missing)}. "
                "Set them on Render or use SMS_PROVIDER=log for pilot DB OTP."
            )
        logger.info("[OTP] Mode: PRODUCTION MSG91 — OTP via MSG91 OTP API")
    else:
        mode = "PRODUCTION (key-based)" if _is_production() else "DEVELOPMENT (DB + log)"
        logger.warning(
            "[OTP] Mode: %s — ENV is %r (not 'production'). "
            "Set ENV=production on Render to activate MSG91 SMS delivery.",
            mode,
            env or "(not set)",
        )
        if not _is_production():
            logger.warning(
                "[OTP] DEV MODE ACTIVE — OTPs written to otp_dev.log, NOT sent via SMS. "
                "OTP values are returned in the API response body (dev_otp). "
                "This must NEVER be active on a production deployment."
            )


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
