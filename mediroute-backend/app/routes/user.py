import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/user", tags=["User"])


@router.post("/onboarding", response_model=schemas.UserResponse)
def complete_onboarding(
    data: schemas.UserOnboarding,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Set name and role for a newly registered user."""
    return crud.update_user(db, current_user, name=data.name, role=data.role)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Permanently delete the authenticated user's account and all personal data.

    - Profile, preferences, applications, resumes, refresh tokens → hard deleted.
    - Jobs posted by this recruiter are preserved (posted_by_user_id set to NULL)
      so active job listings remain visible to candidates.
    - OTP records for this phone number are removed.

    After this call the client must discard all tokens immediately.
    Returns 204 No Content on success.
    """
    logger.info(
        "Account deletion requested by user_id=%s phone=%s",
        current_user.id,
        current_user.phone or "(no phone)",
    )
    try:
        crud.delete_user(db, current_user)
    except Exception as exc:
        logger.error("Account deletion failed for user_id=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account deletion failed. Please try again or contact support.",
        )
