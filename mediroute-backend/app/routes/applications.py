from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user, require_candidate

router = APIRouter(prefix="/applications", tags=["Applications"])


@router.post("/", response_model=schemas.ApplicationResponse, status_code=status.HTTP_201_CREATED)
def apply_to_job(
    data: schemas.ApplicationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_candidate),
):
    if not crud.get_job_by_id(db, data.job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    existing = db.query(models.Application).filter(
        models.Application.user_id == current_user.id,
        models.Application.job_id == data.job_id,
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already applied to this job")
    return crud.apply_to_job(db=db, user_id=current_user.id, job_id=data.job_id)


@router.get("/me", response_model=List[schemas.ApplicationResponse])
def get_my_applications(
    limit: Optional[int] = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_candidate),
):
    apps = db.query(models.Application).filter(
        models.Application.user_id == current_user.id
    ).order_by(models.Application.created_at.desc())
    if limit is not None:
        apps = apps.limit(limit)
    return apps.all()