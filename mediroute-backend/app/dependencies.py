import os

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError

from .database import get_db
from . import models
from .utils.security import decode_access_token

security = HTTPBearer()

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> models.User:
    """Validate JWT and return the authenticated user. DB session via DI — no leaks."""
    try:
        payload = decode_access_token(credentials.credentials)
        user_id: int = payload.get("user_id")
        if user_id is None:
            raise _UNAUTHORIZED
    except JWTError:
        raise _UNAUTHORIZED

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise _UNAUTHORIZED
    return user


def require_recruiter(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Dependency: caller must have role=recruiter. Raises 403 otherwise."""
    if current_user.role != models.UserRole.recruiter:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only recruiters can access this endpoint.",
        )
    return current_user


def require_candidate(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Dependency: block recruiters; all other roles (nurses, doctors, etc.) are candidates."""
    if current_user.role == models.UserRole.recruiter:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recruiters cannot perform this action.",
        )
    return current_user


def require_admin(
    current_user: models.User = Depends(get_current_user),
    x_admin_secret: str = Header(...),
) -> models.User:
    """
    Dual-factor admin guard:
      1. JWT must belong to the phone number set in ADMIN_PHONE env var.
      2. X-Admin-Secret header must match ADMIN_SECRET env var.
    Both checks are mandatory — neither alone is sufficient.
    """
    try:
        admin_phone = os.environ["ADMIN_PHONE"]
        admin_secret = os.environ["ADMIN_SECRET"]
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin configuration missing on server.",
        ) from exc
    if current_user.phone != admin_phone:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    if x_admin_secret != admin_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin secret.")
    return current_user