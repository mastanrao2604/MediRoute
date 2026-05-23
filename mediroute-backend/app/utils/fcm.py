"""
FCM push notification utility.

Loads Firebase credentials from FIREBASE_CREDENTIALS_JSON env var
(JSON content string, not a file path — safe for Render / Supabase deployments).

Missing credentials → graceful degradation:
  - Warning logged once at startup
  - All push calls become no-ops
  - WebSocket delivery still works as primary channel

Return type:
    All public send_* functions return tuple[bool, str | None]:
        (success, error_category)
    error_category is None on success, or one of:
        'invalid_token'  — token unregistered/invalid; caller should delete from DB
        'quota'          — FCM quota exceeded; back off
        'server_error'   — transient Firebase error; may retry later
        'network'        — connection/timeout error; may retry
        'unknown'        — unclassified; log and continue

Usage:
    from app.utils.fcm import send_dispatch_offer

    ok, err = send_dispatch_offer(fcm_token=token, ...)
    if not ok and err == 'invalid_token':
        # delete token from DB
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_fcm_app = None
_fcm_enabled = False

# Android notification channel ID — must match usePushNotifications.js createChannel id
DISPATCH_CHANNEL_ID = "dispatch"


def _init_firebase() -> bool:
    """Lazy-init Firebase Admin SDK. Returns True if initialized successfully."""
    global _fcm_app, _fcm_enabled

    if _fcm_enabled:
        return True

    creds_json = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
    if not creds_json:
        logger.warning(
            "[FCM] FIREBASE_CREDENTIALS_JSON not set — push notifications disabled. "
            "WebSocket delivery will be used as primary channel."
        )
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials

        creds_dict = json.loads(creds_json)
        cred = credentials.Certificate(creds_dict)
        _fcm_app = firebase_admin.initialize_app(cred)
        _fcm_enabled = True
        logger.info("[FCM] Firebase Admin SDK initialized successfully.")
        return True
    except Exception as exc:
        logger.error("[FCM] Failed to initialize Firebase Admin SDK: %s", exc)
        return False


def _categorize_fcm_error(exc: Exception) -> str:
    """
    Classify a Firebase messaging exception for operational handling.

    Returns one of: 'invalid_token', 'quota', 'server_error', 'network', 'unknown'
    'invalid_token' means the token should be deleted from the DB — the device
    has unregistered or the token is malformed.
    """
    exc_class = type(exc).__name__
    exc_msg = str(exc).lower()

    # Unregistered / invalid token — must be cleaned up to stop wasted sends
    if exc_class in ("UnregisteredError", "SenderIdMismatchError"):
        return "invalid_token"
    if exc_class == "InvalidArgumentError" and "registration-token" in exc_msg:
        return "invalid_token"
    if any(p in exc_msg for p in (
        "registration-token-not-registered",
        "invalid registration token",
        "not registered",
    )):
        return "invalid_token"

    # Quota exceeded
    if exc_class == "QuotaExceededError" or "quota" in exc_msg:
        return "quota"

    # Transient Firebase server error
    if exc_class in ("InternalError", "UnavailableError"):
        return "server_error"

    # Network / timeout
    if any(p in exc_msg for p in ("timeout", "connection", "socket", "network")):
        return "network"

    return "unknown"


def send_push_notification(
    fcm_token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    android_priority: str = "high",
    channel_id: str = DISPATCH_CHANNEL_ID,
) -> tuple[bool, Optional[str]]:
    """
    Send a push notification via FCM.

    Returns (success, error_category):
      (True, None)               — delivered
      (False, 'invalid_token')   — token should be deleted from DB
      (False, 'quota')           — rate-limited; back off
      (False, 'server_error')    — transient Firebase error
      (False, 'network')         — connection error
      (False, 'unknown')         — unclassified

    Uses HIGH priority for dispatch offers so the OS wakes the app even in
    Doze mode (critical for Android background behavior on Indian mid-range devices).
    """
    if not _init_firebase():
        return False, None  # graceful no-op — WS is primary

    try:
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=fcm_token,
            android=messaging.AndroidConfig(
                priority=android_priority,
                notification=messaging.AndroidNotification(
                    title=title,
                    body=body,
                    channel_id=channel_id,
                    priority="max" if android_priority == "high" else "default",
                    default_vibrate_timings=True,
                ),
            ),
        )
        messaging.send(message, app=_fcm_app)
        logger.debug("[FCM] Delivered to token %s...", fcm_token[:12])
        return True, None

    except Exception as exc:
        category = _categorize_fcm_error(exc)
        if category == "invalid_token":
            logger.warning(
                "[FCM] Invalid/unregistered token (token=%s...) — will be cleaned up: %s",
                fcm_token[:12], exc,
            )
        else:
            logger.warning(
                "[FCM] Send failed [%s] (token=%s...): %s",
                category, fcm_token[:12], exc,
            )
        return False, category


def send_dispatch_offer(
    fcm_token: str,
    offer_id: int,
    shift_id: int,
    hospital_name: str,
    role: str,
    urgency: str,
    expires_in_sec: int,
    pay_rate: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send a full-screen dispatch offer notification.

    Android HIGH priority + DISPATCH_CHANNEL_ID = IMPORTANCE_HIGH heads-up banner.
    Wakes the app immediately even in Doze / battery-saver mode.

    Returns (success, error_category) — see send_push_notification for error values.
    Call on: nurse not connected via WebSocket (WS is primary).
    """
    urgency_emoji = {"emergency": "\U0001f6a8", "urgent": "\u26a1", "standard": "\U0001f4cb", "planned": "\U0001f4c5"}.get(urgency, "\U0001f4cb")
    title = f"{urgency_emoji} Shift Offer \u2014 {hospital_name}"
    body = f"{role.replace('_', ' ').title()} needed"
    if pay_rate:
        body += f" \u2022 {pay_rate}"
    body += f" \u2022 Respond in {expires_in_sec}s"

    return send_push_notification(
        fcm_token=fcm_token,
        title=title,
        body=body,
        data={
            "type": "dispatch_offer",
            "offer_id": offer_id,
            "shift_id": shift_id,
            "urgency": urgency,
            "expires_in_sec": expires_in_sec,
        },
        android_priority="high",
        channel_id=DISPATCH_CHANNEL_ID,
    )


def send_assignment_confirmed(
    fcm_token: str,
    shift_id: int,
    hospital_name: str,
    shift_start: str,
) -> tuple[bool, Optional[str]]:
    """Notify nurse that their assignment is confirmed."""
    return send_push_notification(
        fcm_token=fcm_token,
        title="\u2705 Assignment Confirmed",
        body=f"{hospital_name} \u2014 {shift_start}",
        data={"type": "assignment_confirmed", "shift_id": shift_id},
        android_priority="high",
        channel_id=DISPATCH_CHANNEL_ID,
    )


def send_shift_filled_to_hospital(
    fcm_token: str,
    shift_id: int,
    nurse_name: str,
    fill_time_sec: int,
) -> tuple[bool, Optional[str]]:
    """Notify hospital recruiter that their shift was filled."""
    return send_push_notification(
        fcm_token=fcm_token,
        title="\u2705 Shift Filled",
        body=f"{nurse_name} confirmed for your shift.",
        data={"type": "shift_filled", "shift_id": shift_id},
        android_priority="normal",
        channel_id="general",
    )
