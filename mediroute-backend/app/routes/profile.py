from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.post("/", response_model=schemas.ProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(
    data: schemas.ProfileCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if crud.get_profile(db, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Profile already exists"
        )
    return crud.create_profile(
        db=db,
        user_id=current_user.id,
        experience_years=data.experience_years,
        education=data.education,
        skills=data.skills,
        current_location=data.current_location,
        service_pincode=data.service_pincode,
        service_locality=data.service_locality,
        location_source=data.location_source,
    )


@router.put("/me", response_model=schemas.ProfileResponse)
def update_profile(
    data: schemas.ProfileCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    profile = crud.get_profile(db, current_user.id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return crud.update_profile(db, profile, **data.model_dump(exclude_none=True))


@router.get("/me", response_model=schemas.ProfileResponse)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    profile = crud.get_profile(db, current_user.id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return profile