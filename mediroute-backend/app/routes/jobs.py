import os
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user, require_recruiter

logger = logging.getLogger("uvicorn.error")

_JOB_APPROVAL_REQUIRED = os.getenv("JOB_APPROVAL_REQUIRED", "false").lower() == "true"

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("", response_model=List[schemas.JobResponse])
@router.get("/", response_model=List[schemas.JobResponse])
def list_jobs(
    role: Optional[models.UserRole] = Query(None),
    location: Optional[str] = Query(None),
    job_type: Optional[models.JobType] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List jobs with optional filters for role, location, and job type."""
    try:
        jobs = crud.get_jobs(db, role=role, location=location, job_type=job_type, skip=skip, limit=limit)
        logger.info("list_jobs: returned %d jobs (role=%s location=%s job_type=%s)", len(jobs), role, location, job_type)
        return jobs
    except Exception as exc:
        logger.exception("list_jobs: unexpected error — %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job listing temporarily unavailable. Please try again.")


@router.get("/match", response_model=List[schemas.JobMatchResponse])
def match_jobs(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return jobs ranked by relevance to the authenticated user's profile."""
    results = crud.match_jobs_for_user(db, current_user.id)
    return [
        schemas.JobMatchResponse(
            job_id=r["job"].id,
            title=r["job"].title,
            hospital=r["job"].hospital_name,
            location=r["job"].location,
            score=r["score"],
        )
        for r in results
    ]


@router.get("/{job_id}", response_model=schemas.JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = crud.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("/", response_model=schemas.JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    data: schemas.JobCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_recruiter),
):
    """Create a job listing. Caller must be a verified recruiter."""
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your recruiter account must be verified before posting jobs.",
        )
    job_data = data.model_dump()
    job_data["posted_by_user_id"] = current_user.id
    if _JOB_APPROVAL_REQUIRED:
        job_data["status"] = models.JobStatus.pending
    return crud.create_job(db, job_data)
