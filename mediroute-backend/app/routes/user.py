from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user

router = APIRouter(prefix="/user", tags=["User"])


@router.post("/onboarding", response_model=schemas.UserResponse)
def complete_onboarding(
    data: schemas.UserOnboarding,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Set name and role for a newly registered user."""
    return crud.update_user(db, current_user, name=data.name, role=data.role)
