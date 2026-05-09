"""
SMS utility — production-ready OTP delivery.

Provider selection (set SMS_PROVIDER env var):
  msg91   → MSG91 HTTP API  (default when MSG91_AUTH_KEY is set)
  twilio  → Twilio REST API (set SMS_PROVIDER=twilio)
  log     → DEV only: writes to otp_dev.log, never sends a real SMS

Required env vars:
  MSG91:
    MSG91_AUTH_KEY      — API key from MSG91 dashboard
    MSG91_TEMPLATE_ID   — DLT-approved template ID
    MSG91_SENDER_ID     — 6-char sender ID (default: MEDIRT)

  Twilio:
    TWILIO_ACCOUNT_SID  — Account SID
    TWILIO_AUTH_TOKEN   — Auth token
    TWILIO_FROM_NUMBER  — Twilio phone number (e.g. +14155552671)
"""

import logging
import os
from datetime import datetime

logger = logging.getLogger("uvicorn.error")

_SMS_PROVIDER = os.getenv("SMS_PROVIDER", "log").lower()


# ── MSG91 ─────────────────────────────────────────────────────────────────────

def _send_msg91(phone: str, otp: str) -> None:
    """Send OTP via MSG91 Flow API."""
    import requests  # lazily imported so missing package fails loudly only when used

    auth_key = os.environ["MSG91_AUTH_KEY"]
    template_id = os.environ["MSG91_TEMPLATE_ID"]
    sender_id = os.getenv("MSG91_SENDER_ID", "MEDIRT")

    # Normalise to E.164 (MSG91 expects country code, no +)
    phone_e164 = phone.lstrip("+")
    if not phone_e164.startswith("91") and len(phone_e164) == 10:
        phone_e164 = "91" + phone_e164

    payload = {
        "template_id": template_id,
        "short_url": "0",
        "realTimeResponse": "1",
        "recipients": [
            {
                "mobiles": phone_e164,
                "otp": otp,
            }
        ],
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authkey": auth_key,
    }
    resp = requests.post(
        "https://control.msg91.com/api/v5/flow/",
        json=payload,
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("MSG91 OTP sent to %s | response: %s", phone, resp.text[:120])


# ── Twilio ────────────────────────────────────────────────────────────────────

def _send_twilio(phone: str, otp: str) -> None:
    """Send OTP via Twilio Verify / Messaging."""
    import requests
    from requests.auth import HTTPBasicAuth

    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_FROM_NUMBER"]

    # Normalise phone to E.164
    if not phone.startswith("+"):
        phone = "+91" + phone.lstrip("0")

    body = f"Your MediRoute OTP is {otp}. Valid for 5 minutes. Do not share it."
    resp = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data={"From": from_number, "To": phone, "Body": body},
        auth=HTTPBasicAuth(account_sid, auth_token),
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Twilio OTP sent to %s | SID: %s", phone, resp.json().get("sid"))


# ── Dev log fallback ──────────────────────────────────────────────────────────

def _send_log(phone: str, otp: str) -> None:
    """Write OTP to otp_dev.log. For development only — never use in production."""
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "otp_dev.log"
    )
    with open(log_path, "a") as fh:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        fh.write(f"{ts} | [DEV] Phone: {phone}  OTP: {otp}  (expires in 5 min)\n")
        fh.flush()
    logger.warning("DEV MODE: OTP for %s written to otp_dev.log (not sent via SMS)", phone)


# ── Public interface ──────────────────────────────────────────────────────────

def send_otp_sms(phone: str, otp: str) -> None:
    """
    Dispatch OTP to the configured SMS provider.
    Raises an exception if delivery fails — caller should handle it.
    """
    provider = _SMS_PROVIDER

    # Auto-detect: if MSG91_AUTH_KEY is set but SMS_PROVIDER not explicitly set,
    # prefer MSG91 over the default 'log'.
    if provider == "log" and os.getenv("MSG91_AUTH_KEY"):
        provider = "msg91"

    if provider == "msg91":
        _send_msg91(phone, otp)
    elif provider == "twilio":
        _send_twilio(phone, otp)
    else:
        _send_log(phone, otp)
