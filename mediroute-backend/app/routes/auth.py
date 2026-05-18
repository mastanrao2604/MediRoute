import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from ..database import get_db
from .. import crud, schemas, models
from ..utils.security import create_access_token, create_refresh_token, decode_access_token
from ..dependencies import get_current_user
from ..services import otp_service

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/auth", tags=["Auth"])

_SERVICE_AREA_ROLES = frozenset({
    models.UserRole.nurse,
    models.UserRole.staff_nurse,
    models.UserRole.icu_nurse,
    models.UserRole.ot_nurse,
    models.UserRole.emergency_nurse,
    models.UserRole.home_care_nurse,
    models.UserRole.doctor,
    models.UserRole.lab_tech,
    models.UserRole.pharmacist,
    models.UserRole.driver,
    models.UserRole.front_office,
})


# ── Token issuance helper ─────────────────────────────────────────────────────

def _issue_tokens(user: models.User, db: Session) -> dict:
    """Issue access + refresh tokens and persist the refresh token in DB."""
    access = create_access_token({"user_id": user.id})
    refresh, expires_at = create_refresh_token({"user_id": user.id})
    crud.save_refresh_token(db, user_id=user.id, token=refresh, expires_at=expires_at)
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}

# ── Google token helpers ──────────────────────────────────────────────────────

def _verify_google_token(token: str) -> dict:
    """Verify a Google ID token and return the payload. Raises HTTP 401 on failure."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google login is not configured on this server.",
        )
    try:
        payload = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), client_id
        )
    except Exception as exc:
        logger.warning("Google token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token.",
        )
    return payload


def _make_google_session_token(google_id: str, email: str) -> str:
    """Create a short-lived JWT for a pending Google sign-in awaiting phone verification."""
    return create_access_token(
        {"google_pending": True, "google_id": google_id, "email": email},
        expires_minutes=15,
    )


def _decode_google_session_token(token: str) -> dict:
    """Decode and validate a google_session_token. Raises HTTP 400 on failure."""
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired session token. Please sign in with Google again.",
        )
    if not payload.get("google_pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session token type.",
        )
    return payload


@router.post("/send-otp", status_code=status.HTTP_200_OK)
def send_otp(data: schemas.OTPRequest, db: Session = Depends(get_db)):
    """
    Send a 6-digit OTP to the given phone number.

    Production (MSG91_AUTH_KEY set):
      • OTP generated and managed entirely by MSG91 — never stored in our DB.
      • Rate-limited to 3 requests per 5 minutes per phone.

    Development (no MSG91_AUTH_KEY):
      • OTP stored in DB and written to otp_dev.log.
      • `dev_otp` field returned in response for test convenience.
    """
    dev_otp = otp_service.send_otp(phone=data.phone, db=db)

    if dev_otp is not None:
        # Dev mode — safe to surface for testing
        return {"message": "OTP sent (dev mode)", "dev_otp": dev_otp}

    return {"message": "OTP sent successfully"}


@router.post("/verify-otp", response_model=schemas.TokenResponse)
def verify_otp(data: schemas.OTPVerify, db: Session = Depends(get_db)):
    """
    Verify OTP and issue a JWT access token.

    On success: creates the user account if it does not yet exist.
    On failure: returns HTTP 401 (wrong / expired OTP).
    """
    is_valid = otp_service.verify_otp(phone=data.phone, otp=data.otp, db=db)

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP. Please request a new one.",
        )

    # Normalise phone so user lookup is consistent
    phone = otp_service.normalise_phone(data.phone)
    user = crud.create_or_get_user(db, phone=phone)

    logger.info("User %s authenticated (phone=%s)", user.id, phone)
    return _issue_tokens(user, db)


@router.get("/me", response_model=schemas.UserResponse)
def get_me(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the authenticated user's profile, with computed profile_complete flag."""
    profile = crud.get_profile(db, current_user.id)
    row = schemas.UserResponse.model_validate(current_user).model_dump()
    row["service_pincode"] = getattr(profile, "service_pincode", None) if profile else None
    row["service_locality"] = getattr(profile, "service_locality", None) if profile else None

    if current_user.role == models.UserRole.recruiter:
        row["profile_complete"] = True
    else:
        complete = bool(
            profile
            and profile.experience_years is not None
            and profile.skills
            and profile.current_location
        )
        if complete and current_user.role in _SERVICE_AREA_ROLES:
            clean_pc = "".join(c for c in str(profile.service_pincode or "") if c.isdigit())
            complete = len(clean_pc) == 6
        row["profile_complete"] = complete
    return row


# ── Google OAuth ──────────────────────────────────────────────────────────────

@router.post("/google", response_model=schemas.GoogleLoginResponse)
def google_login(data: schemas.GoogleLoginRequest, db: Session = Depends(get_db)):
    """
    Verify a Google ID token from the frontend and either:
    - Issue a JWT immediately (user has a verified phone), or
    - Return a short-lived google_session_token for the phone-link flow.
    """
    payload = _verify_google_token(data.token)

    google_id = payload["sub"]
    email = payload.get("email", "")
    name = payload.get("name", "")

    # Check for existing user by google_id first, then by email
    user = crud.get_user_by_google_id(db, google_id)
    if not user:
        user = crud.get_user_by_email(db, email)
        if user:
            # Existing phone-OTP user — link their Google account
            user.google_id = google_id
            db.commit()
            db.refresh(user)
        else:
            # Brand new user — create with Google info only
            user = crud.create_google_user(db, email=email, name=name, google_id=google_id)

    if user.phone_verified and user.phone:
        # Fully set up — issue tokens
        tokens = _issue_tokens(user, db)
        logger.info("Google user %s logged in (email=%s)", user.id, email)
        return schemas.GoogleLoginResponse(**tokens)

    # Needs phone verification before full access
    session_token = _make_google_session_token(google_id=google_id, email=email)
    logger.info("Google user requires phone verification (email=%s)", email)
    return schemas.GoogleLoginResponse(
        phone_verification_required=True,
        google_session_token=session_token,
    )


@router.post("/google/send-otp")
def google_link_send_otp(
    data: schemas.GoogleLinkPhoneRequest, db: Session = Depends(get_db)
):
    """
    For Google-authenticated users who still need to verify their phone.
    Validates the google_session_token and sends an OTP to the given phone.
    """
    _decode_google_session_token(data.google_session_token)  # validates token

    # Check phone is not already taken by a different account
    existing = crud.get_user_by_phone(db, data.phone)
    if existing:
        # If it belongs to a different google_id, reject
        payload = _decode_google_session_token(data.google_session_token)
        if existing.google_id and existing.google_id != payload["google_id"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This phone number is already registered to another account.",
            )

    dev_otp = otp_service.send_otp(phone=data.phone, db=db)
    if dev_otp is not None:
        return {"message": "OTP sent (dev mode)", "dev_otp": dev_otp}
    return {"message": "OTP sent successfully"}


@router.post("/google/verify-phone", response_model=schemas.TokenResponse)
def google_link_verify_phone(
    data: schemas.GoogleVerifyPhoneRequest, db: Session = Depends(get_db)
):
    """
    Verify the OTP, link the phone to the Google account, and issue a JWT.
    """
    session_payload = _decode_google_session_token(data.google_session_token)
    google_id = session_payload["google_id"]

    is_valid = otp_service.verify_otp(phone=data.phone, otp=data.otp, db=db)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP. Please request a new one.",
        )

    phone = otp_service.normalise_phone(data.phone)

    user = crud.get_user_by_google_id(db, google_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google account not found. Please start the sign-in flow again.",
        )

    # Check for phone conflict with another user
    existing_phone_user = crud.get_user_by_phone(db, phone)
    if existing_phone_user and existing_phone_user.id != user.id:
        # Merge: copy phone to current user, remove from old user
        existing_phone_user.phone = None
        existing_phone_user.phone_verified = False
        db.commit()

    crud.link_phone_to_user(db, user, phone)
    logger.info("Google user %s phone verified and linked (phone=%s)", user.id, phone)
    return _issue_tokens(user, db)


# ── Session management ────────────────────────────────────────────────────────

@router.post("/refresh", response_model=schemas.RefreshResponse)
def refresh(data: schemas.RefreshRequest, db: Session = Depends(get_db)):
    """
    Exchange a valid refresh token for a new access token.
    No bearer auth required — the refresh token is the credential.
    """
    # 1. Validate JWT signature + expiry
    try:
        payload = decode_access_token(data.refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type.",
        )

    user_id: int = payload.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed refresh token.",
        )

    # 2. Check the token is still in DB (not revoked by logout)
    stored = crud.get_refresh_token(db, data.refresh_token)
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked. Please log in again.",
        )

    # 3. Verify DB expiry (defensive — JWT exp already checked above)
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        crud.delete_refresh_token(db, data.refresh_token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired. Please log in again.",
        )

    new_access = create_access_token({"user_id": user_id})
    logger.info("Access token refreshed for user %s", user_id)
    return {"access_token": new_access, "token_type": "bearer"}


@router.post("/logout")
def logout(data: schemas.LogoutRequest, db: Session = Depends(get_db)):
    """
    Invalidate the refresh token.
    No bearer auth required — works even when the access token has expired.
    """
    crud.delete_refresh_token(db, data.refresh_token)
    return {"message": "Logged out successfully"}
