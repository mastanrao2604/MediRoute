from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user

router = APIRouter(prefix="/preferences", tags=["Preferences"])


@router.post("/", response_model=schemas.PreferenceResponse)
def upsert_preferences(
    data: schemas.PreferenceCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return crud.create_or_update_preferences(
        db=db,
        user_id=current_user.id,
        job_type=data.job_type,
        preferred_country=data.preferred_country,
        passport_status=data.passport_status,
    )


@router.get("/me", response_model=schemas.PreferenceResponse)
def get_my_preferences(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    prefs = crud.get_preferences(db, current_user.id)
    if not prefs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Preferences not found"
        )
    return prefs