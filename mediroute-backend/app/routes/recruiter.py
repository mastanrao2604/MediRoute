from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user, require_recruiter

router = APIRouter(prefix="/recruiter", tags=["Recruiter"])

_NOT_VERIFIED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Recruiter not verified. Please complete your company profile and wait for verification.",
)


# ─── Recruiter Profile ────────────────────────────────────────────────────────

@router.post("/profile", response_model=schemas.RecruiterProfileResponse)
def save_recruiter_profile(
    data: schemas.RecruiterProfileCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_recruiter),
):
    """Save / update company name and official email. Does not set is_verified."""
    current_user.company_name = data.company_name
    current_user.official_email = data.official_email
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/profile", response_model=schemas.RecruiterProfileResponse)
def get_recruiter_profile(
    current_user: models.User = Depends(require_recruiter),
):
    """Return the current recruiter's profile."""
    return current_user


# ─── Jobs ─────────────────────────────────────────────────────────────────────

@router.post("/jobs", response_model=schemas.JobResponse, status_code=status.HTTP_201_CREATED)
def post_job(
    data: schemas.JobCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_recruiter),
):
    """Create a job listing. Recruiter must be verified."""
    if not current_user.is_verified:
        raise _NOT_VERIFIED
    job_data = data.model_dump()
    job_data["posted_by_user_id"] = current_user.id
    return crud.create_job(db, job_data)


@router.get("/jobs", response_model=List[schemas.JobResponse])
def get_my_jobs(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_recruiter),
):
    """List all jobs posted by the current recruiter."""
    return crud.get_recruiter_jobs(db, current_user.id)


# ─── Applicants ───────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/applicants", response_model=List[schemas.ApplicantSummary])
def get_applicants(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_recruiter),
):
    """Return applicants for a specific job. Only the job owner can view."""
    job = crud.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.posted_by_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view applicants for your own jobs",
        )
    return crud.get_job_applicants(db, job_id)


@router.get("/applications/{application_id}", response_model=schemas.CandidateDetail)
def get_candidate_detail(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_recruiter),
):
    """Return full candidate details. Only the job owner can view."""
    detail = crud.get_application_detail(db, application_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    # Verify ownership: recruiter must own the job this application belongs to
    job = crud.get_job_by_id(db, detail["job_id"])
    if not job or job.posted_by_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view applicants for your own jobs",
        )
    return detail
