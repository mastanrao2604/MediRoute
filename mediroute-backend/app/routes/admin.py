"""
Admin endpoints — dual-protected: JWT (ADMIN_PHONE) + X-Admin-Secret header.

Recruiter control:
    GET   /admin/recruiters/pending
    PATCH /admin/verify-recruiter/{user_id}
    PATCH /admin/unverify-recruiter/{user_id}

Job approval (JOB_APPROVAL_REQUIRED=true):
    GET   /admin/jobs/pending
    PATCH /admin/jobs/{job_id}/approve
    PATCH /admin/jobs/{job_id}/reject

Required env vars: ADMIN_PHONE, ADMIN_SECRET
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from .. import models, schemas
from ..dependencies import require_admin

router = APIRouter(prefix="/admin", tags=["Admin"])


# ─── Recruiter listing ────────────────────────────────────────────────────────

@router.get("/recruiters/pending", response_model=List[schemas.RecruiterProfileResponse])
def list_pending_recruiters(
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    """List recruiters who have not yet been verified."""
    return (
        db.query(models.User)
        .filter(
            models.User.role == models.UserRole.recruiter,
            models.User.is_verified == False,  # noqa: E712
        )
        .all()
    )


@router.patch(
    "/verify-recruiter/{user_id}",
    response_model=schemas.RecruiterProfileResponse,
)
def verify_recruiter(
    user_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    """Mark a recruiter as verified."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.role != models.UserRole.recruiter:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a recruiter",
        )
    user.is_verified = True
    db.commit()
    db.refresh(user)
    return user


@router.patch(
    "/unverify-recruiter/{user_id}",
    response_model=schemas.RecruiterProfileResponse,
)
def unverify_recruiter(
    user_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    """Revoke recruiter verification."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_verified = False
    db.commit()
    db.refresh(user)
    return user


# ─── Job approval ─────────────────────────────────────────────────────────────

@router.get("/jobs/pending", response_model=List[schemas.JobResponse])
def list_pending_jobs(
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    """List jobs awaiting approval (status=pending)."""
    return (
        db.query(models.Job)
        .filter(models.Job.status == models.JobStatus.pending)
        .all()
    )


@router.patch("/jobs/{job_id}/approve", response_model=schemas.JobResponse)
def approve_job(
    job_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    """Approve a pending job — sets status to open."""
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job.status = models.JobStatus.open
    db.commit()
    db.refresh(job)
    return job


@router.patch("/jobs/{job_id}/reject", response_model=schemas.JobResponse)
def reject_job(
    job_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    """Reject a pending job — sets status to closed."""
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job.status = models.JobStatus.closed
    db.commit()
    db.refresh(job)
    return job
