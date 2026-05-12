"""
FCM push notification utility.

Loads Firebase credentials from FIREBASE_CREDENTIALS_JSON env var
(JSON content string, not a file path — safe for Render / Supabase deployments).

Missing credentials → graceful degradation:
  - Warning logged once at startup
  - All push calls become no-ops
  - WebSocket delivery still works as primary channel

Usage:
    from app.utils.fcm import send_dispatch_offer, send_assignment_confirmed
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_fcm_app = None
_fcm_enabled = False


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


def send_push_notification(
    fcm_token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    android_priority: str = "high",
) -> bool:
    """
    Send a push notification via FCM. Returns True on success.

    Uses HIGH priority for dispatch offers so the OS wakes the app even in
    Doze mode (critical for Android background behavior on Indian mid-range devices).
    """
    if not _init_firebase():
        return False

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
                    priority="max" if android_priority == "high" else "default",
                    default_vibrate_timings=True,
                ),
            ),
        )
        messaging.send(message, app=_fcm_app)
        return True
    except Exception as exc:
        logger.warning("[FCM] Push send failed (token=%s...): %s", fcm_token[:10], exc)
        return False


def send_dispatch_offer(
    fcm_token: str,
    offer_id: int,
    shift_id: int,
    hospital_name: str,
    role: str,
    urgency: str,
    expires_in_sec: int,
    pay_rate: Optional[str] = None,
) -> bool:
    """
    Send a full-screen dispatch offer notification.
    Android HIGH priority — wakes the app immediately.
    """
    urgency_emoji = {"emergency": "🚨", "urgent": "⚡", "standard": "📋", "planned": "📅"}.get(urgency, "📋")
    title = f"{urgency_emoji} Shift Offer — {hospital_name}"
    body = f"{role.replace('_', ' ').title()} needed"
    if pay_rate:
        body += f" • {pay_rate}"
    body += f" • Respond in {expires_in_sec}s"

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
    )


def send_assignment_confirmed(
    fcm_token: str,
    shift_id: int,
    hospital_name: str,
    shift_start: str,
) -> bool:
    """Notify nurse that their assignment is confirmed."""
    return send_push_notification(
        fcm_token=fcm_token,
        title="✅ Assignment Confirmed",
        body=f"{hospital_name} — {shift_start}",
        data={"type": "assignment_confirmed", "shift_id": shift_id},
        android_priority="high",
    )


def send_shift_filled_to_hospital(
    fcm_token: str,
    shift_id: int,
    nurse_name: str,
    fill_time_sec: int,
) -> bool:
    """Notify hospital recruiter that their shift was filled."""
    return send_push_notification(
        fcm_token=fcm_token,
        title="✅ Shift Filled",
        body=f"{nurse_name} accepted in {fill_time_sec // 60}m {fill_time_sec % 60}s",
        data={"type": "shift_filled", "shift_id": shift_id},
        android_priority="normal",
    )
