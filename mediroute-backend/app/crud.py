import logging
import sys
import secrets
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from typing import Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger("uvicorn.error")

from . import models


def delete_user(db: Session, user: models.User) -> None:
    """
    Hard-delete a user and all their owned data in a single transaction.

    Cascade rules (defined on the ORM relationships and FK constraints):
      - Profile, UserPreference, Application, Resume, ResumeData, RefreshToken
        all have ondelete="CASCADE" or cascade="all, delete-orphan" → auto-deleted.

    Jobs posted by this recruiter:
      - We null-out posted_by_user_id so existing job listings are preserved
        (orphaning them is safer than deleting live job posts that applicants
        may have applied to).  Recruiter-owned jobs are kept so candidates
        aren't impacted, but the poster link is removed.

    OTPCode records are keyed by phone — cleared separately for privacy.

    Dispatch tables (dispatch_offers, live_assignments, shift_requests) use FK
    to users without DB-level CASCADE — we remove dependent rows explicitly.
    """
    user_id = user.id
    phone = user.phone

    # Null posted_by_user_id on any jobs this user posted (preserve job listings)
    db.query(models.Job).filter(
        models.Job.posted_by_user_id == user_id
    ).update({"posted_by_user_id": None}, synchronize_session=False)

    # Remove any OTP codes for this phone (privacy cleanup)
    if phone:
        db.query(models.OTPCode).filter(
            models.OTPCode.phone == phone
        ).delete(synchronize_session=False)

    # ── Dispatch graph (FK to users.id without ON DELETE CASCADE) ─────────────
    # Nurse rows: assignments reference offers — delete assignments first.
    db.query(models.LiveAssignment).filter(
        models.LiveAssignment.nurse_user_id == user_id
    ).delete(synchronize_session=False)

    db.query(models.DispatchOffer).filter(
        models.DispatchOffer.nurse_user_id == user_id
    ).delete(synchronize_session=False)

    # Hospital/recruiter-owned shift requests and all dependent dispatch rows
    shift_ids = [
        sid
        for (sid,) in db.query(models.ShiftRequest.id).filter(
            models.ShiftRequest.hospital_user_id == user_id
        ).all()
    ]
    if shift_ids:
        db.query(models.LiveAssignment).filter(
            models.LiveAssignment.shift_request_id.in_(shift_ids)
        ).delete(synchronize_session=False)
        db.query(models.DispatchOffer).filter(
            models.DispatchOffer.shift_request_id.in_(shift_ids)
        ).delete(synchronize_session=False)
        db.query(models.DispatchSession).filter(
            models.DispatchSession.shift_request_id.in_(shift_ids)
        ).delete(synchronize_session=False)
        db.query(models.ShiftTimelineEvent).filter(
            models.ShiftTimelineEvent.shift_request_id.in_(shift_ids)
        ).delete(synchronize_session=False)
        db.query(models.ShiftRequest).filter(
            models.ShiftRequest.id.in_(shift_ids)
        ).delete(synchronize_session=False)

    # Delete the user — cascade handles Profile, Preferences, Applications,
    # Resumes, ResumeData, and RefreshTokens automatically.
    db.delete(user)
    db.commit()
    logger.info("User %s permanently deleted.", user_id)


def create_user(
    db: Session,
    name: str,
    phone: str,
    role: Optional[models.UserRole] = None,
) -> models.User:
    existing = db.query(models.User).filter(models.User.phone == phone).first()
    if existing:
        return existing
    user = models.User(name=name, phone=phone, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_phone(db: Session, phone: str) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.phone == phone).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.id == user_id).first()


def update_user(db: Session, user: models.User, **kwargs) -> models.User:
    for key, value in kwargs.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_email(db: Session, email: str) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.email == email).first()


def get_user_by_google_id(db: Session, google_id: str) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.google_id == google_id).first()


def create_google_user(
    db: Session, email: str, name: str, google_id: str
) -> models.User:
    user = models.User(
        email=email,
        name=name,
        google_id=google_id,
        phone_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def link_phone_to_user(db: Session, user: models.User, phone: str) -> models.User:
    user.phone = phone
    user.phone_verified = True
    db.commit()
    db.refresh(user)
    return user


def get_or_create_user(db: Session, phone: str) -> models.User:
    user = get_user_by_phone(db, phone)
    if not user:
        user = models.User(name="", phone=phone, phone_verified=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def create_profile(
    db: Session,
    user_id: int,
    experience_years: Optional[int],
    education: Optional[str],
    skills: Optional[str],
    current_location: Optional[str],
    service_pincode: Optional[str] = None,
    service_locality: Optional[str] = None,
    location_source: Optional[str] = None,
) -> models.Profile:
    profile = models.Profile(
        user_id=user_id,
        experience_years=experience_years,
        education=education,
        skills=skills,
        current_location=current_location,
        service_pincode=service_pincode,
        service_locality=service_locality,
        location_source=location_source,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_profile(db: Session, user_id: int) -> Optional[models.Profile]:
    return db.query(models.Profile).filter(models.Profile.user_id == user_id).first()


def update_profile(db: Session, profile: models.Profile, **kwargs) -> models.Profile:
    for key, value in kwargs.items():
        setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    return profile


def create_or_update_preferences(
    db: Session,
    user_id: int,
    job_type: models.JobType,
    preferred_country: Optional[str],
    passport_status: models.PassportStatus,
) -> models.UserPreference:
    pref = db.query(models.UserPreference).filter(
        models.UserPreference.user_id == user_id
    ).first()
    if pref:
        pref.job_type = job_type
        pref.preferred_country = preferred_country
        pref.passport_status = passport_status
    else:
        pref = models.UserPreference(
            user_id=user_id,
            job_type=job_type,
            preferred_country=preferred_country,
            passport_status=passport_status,
        )
        db.add(pref)
    db.commit()
    db.refresh(pref)
    return pref


def get_preferences(db: Session, user_id: int) -> Optional[models.UserPreference]:
    return db.query(models.UserPreference).filter(
        models.UserPreference.user_id == user_id
    ).first()


def create_company(
    db: Session,
    name: str,
    location: Optional[str] = None,
    type: Optional[str] = None,
) -> models.Company:
    company = models.Company(name=name, location=location, type=type)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def get_company(db: Session, company_id: int) -> Optional[models.Company]:
    return db.query(models.Company).filter(models.Company.id == company_id).first()


def create_recruiter(
    db: Session,
    name: Optional[str],
    phone: Optional[str],
    company_id: int,
) -> models.Recruiter:
    recruiter = models.Recruiter(name=name, phone=phone, company_id=company_id)
    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)
    return recruiter


def create_job(db: Session, job_data: dict) -> models.Job:
    job = models.Job(**job_data)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_jobs(
    db: Session,
    role: Optional[models.UserRole] = None,
    location: Optional[str] = None,
    job_type: Optional[models.JobType] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[models.Job]:
    query = db.query(models.Job).filter(models.Job.status == models.JobStatus.open)
    if role:
        query = query.filter(models.Job.role_required == role)
    if location:
        query = query.filter(models.Job.location.ilike(f"%{location}%"))
    if job_type:
        query = query.filter(models.Job.job_type == job_type)
    return query.offset(skip).limit(limit).all()


def get_job_by_id(db: Session, job_id: int) -> Optional[models.Job]:
    return db.query(models.Job).filter(models.Job.id == job_id).first()


# ─── Recruiter CRUD ───────────────────────────────────────────────────────────

def get_recruiter_jobs(db: Session, user_id: int) -> List[models.Job]:
    return (
        db.query(models.Job)
        .filter(
            models.Job.posted_by_user_id == user_id,
            models.Job.recruiter_archived_at.is_(None),
        )
        .order_by(models.Job.created_at.desc())
        .all()
    )


def get_job_applicants(db: Session, job_id: int) -> List[dict]:
    # Fetch applications with users in a single query (no lazy-load per row)
    applications = (
        db.query(models.Application)
        .filter(models.Application.job_id == job_id)
        .options(selectinload(models.Application.user))
        .all()
    )
    if not applications:
        return []

    user_ids = [app.user_id for app in applications]

    # Batch-load profiles: 1 query for all users
    profiles = {
        p.user_id: p
        for p in db.query(models.Profile).filter(models.Profile.user_id.in_(user_ids)).all()
    }

    # Batch-load latest ResumeData per user: 1 query via window function
    subq = (
        db.query(
            models.ResumeData.user_id,
            func.max(models.ResumeData.id).label("max_id"),
        )
        .filter(models.ResumeData.user_id.in_(user_ids))
        .group_by(models.ResumeData.user_id)
        .subquery()
    )
    resumes = {
        r.user_id: r
        for r in db.query(models.ResumeData)
        .join(subq, models.ResumeData.id == subq.c.max_id)
        .all()
    }

    result = []
    for app in applications:
        user = app.user  # already loaded — no extra query
        profile = profiles.get(user.id)
        resume = resumes.get(user.id)
        display_name = (
            user.name
            or (resume.full_name if resume and resume.full_name else None)
            or user.phone
        )
        result.append({
            "application_id": app.id,
            "status": app.status,
            "applied_at": app.created_at,
            "candidate_name": display_name,
            "experience": profile.experience_years if profile else None,
            "skills": profile.skills if profile else None,
            "location": profile.current_location if profile else None,
            "candidate_user_id": user.id,
            "has_resume": bool(user.resume_url),  # already loaded — no extra query
        })
    return result


def get_application_detail(db: Session, application_id: int) -> Optional[dict]:
    app = (
        db.query(models.Application)
        .filter(models.Application.id == application_id)
        .options(selectinload(models.Application.user))
        .first()
    )
    if not app:
        return None
    user = app.user  # already loaded
    profile = db.query(models.Profile).filter(models.Profile.user_id == user.id).first()
    resume = (
        db.query(models.ResumeData)
        .filter(models.ResumeData.user_id == user.id)
        .order_by(models.ResumeData.id.desc())
        .first()
    )
    return {
        "application_id": app.id,
        "job_id": app.job_id,
        "status": app.status,
        "applied_at": app.created_at,
        # Resolve display name: user.name → resume full_name → phone
        "candidate_name": (
            user.name
            or (resume.full_name if resume and resume.full_name else None)
            or user.phone
        ),
        "phone": user.phone,
        "experience_years": profile.experience_years if profile else None,
        "skills": profile.skills if profile else None,
        "education": profile.education if profile else None,
        "location": profile.current_location if profile else None,
        "resume_skills": resume.skills if resume else None,
        "resume_experience": resume.experience if resume else None,
        "candidate_user_id": user.id,
        # user.resume_url is already loaded via selectinload — no extra query needed.
        "has_resume": bool(user.resume_url),
    }


def match_jobs_for_user(db: Session, user_id: int) -> List[dict]:
    user = get_user_by_id(db, user_id)
    profile = get_profile(db, user_id)
    prefs = get_preferences(db, user_id)

    # Only score open jobs — avoids full-table scan across closed/draft/pending rows
    jobs = db.query(models.Job).filter(models.Job.status == models.JobStatus.open).all()
    scored = []

    user_skills: set = set()
    if profile and profile.skills:
        user_skills = {s.strip().lower() for s in profile.skills.split(",")}

    for job in jobs:
        score = 0

        if user and user.role and job.role_required == user.role:
            score += 40

        if prefs:
            if prefs.job_type == models.JobType.both:
                score += 20
            elif job.job_type and prefs.job_type == job.job_type:
                score += 20

        if profile and profile.current_location and job.location:
            if profile.current_location.lower() in job.location.lower():
                score += 20

        if user_skills and job.description:
            desc_lower = job.description.lower()
            matched = sum(1 for skill in user_skills if skill in desc_lower)
            score += min(matched * 5, 20)

        if score > 0:
            scored.append({"job": job, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:10]


def get_dashboard_data(db: Session, user_id: int, app_limit: int = 10) -> dict:
    """Aggregate dashboard query — returns profile, preferences, and recent applications
    in a single DB round-trip per relation (3 queries total) instead of 3 HTTP calls."""
    profile = db.query(models.Profile).filter(models.Profile.user_id == user_id).first()
    preferences = db.query(models.UserPreference).filter(
        models.UserPreference.user_id == user_id
    ).first()
    applications = (
        db.query(models.Application)
        .filter(models.Application.user_id == user_id)
        .order_by(models.Application.created_at.desc())
        .limit(app_limit)
        .all()
    )
    return {
        "profile": profile,
        "preferences": preferences,
        "applications": applications,
    }


def apply_to_job(db: Session, user_id: int, job_id: int) -> models.Application:
    existing = db.query(models.Application).filter(
        models.Application.user_id == user_id,
        models.Application.job_id == job_id,
    ).first()
    if existing:
        return existing
    application = models.Application(user_id=user_id, job_id=job_id)
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def get_user_applications(db: Session, user_id: int) -> List[models.Application]:
    return db.query(models.Application).filter(
        models.Application.user_id == user_id
    ).all()


def update_application_status(
    db: Session,
    application_id: int,
    new_status: models.ApplicationStatus,
) -> Optional[models.Application]:
    app = db.query(models.Application).filter(
        models.Application.id == application_id
    ).first()
    if app:
        app.status = new_status
        db.commit()
        db.refresh(app)
    return app


def has_uploaded_resume(db: Session, user_id: int) -> bool:
    """Return True if the user has a resume_url set on their User record."""
    u = db.query(models.User.resume_url).filter(models.User.id == user_id).first()
    return bool(u and u.resume_url)


def create_resume(db: Session, user_id: int, file_url: str) -> models.Resume:
    resume = models.Resume(user_id=user_id, file_url=file_url, is_generated=False)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def create_uploaded_resume(db: Session, user_id: int, file_url: str) -> models.Resume:
    resume = models.Resume(user_id=user_id, file_url=file_url, is_generated=False)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def create_generated_resume(db: Session, user_id: int, resume_data: dict) -> models.Resume:
    resume = models.Resume(user_id=user_id, resume_data=resume_data, is_generated=True)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def create_resume_from_pdf(
    db: Session,
    user_id: int,
    file_url: str,
    parsed_data: dict,
) -> models.Resume:
    resume = models.Resume(
        user_id=user_id,
        file_url=file_url,
        resume_data=parsed_data,
        is_generated=True,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def update_user_profile_from_resume(
    db: Session,
    user_id: int,
    parsed_data: dict,
) -> models.Profile:
    skills_list = parsed_data.get("skills") or []
    skills_str = ", ".join(skills_list) if isinstance(skills_list, list) else skills_list

    profile = db.query(models.Profile).filter(models.Profile.user_id == user_id).first()
    if profile:
        if parsed_data.get("education"):
            profile.education = parsed_data["education"]
        if skills_str:
            profile.skills = skills_str
        if parsed_data.get("experience_years") is not None:
            profile.experience_years = parsed_data["experience_years"]
    else:
        profile = models.Profile(
            user_id=user_id,
            education=parsed_data.get("education"),
            skills=skills_str or None,
            experience_years=parsed_data.get("experience_years"),
        )
        db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_user_resumes(db: Session, user_id: int) -> List[models.Resume]:
    return db.query(models.Resume).filter(models.Resume.user_id == user_id).all()


def update_profile_from_resume_builder(db: Session, user_id: int, resume_data: dict) -> None:
    """Sync resume builder fields into profile, only filling currently-empty values."""
    import re

    skills = (resume_data.get("skills") or "").strip() or None
    education = (resume_data.get("education") or "").strip() or None

    # Try to extract a numeric year count from free-text experience field
    experience_years: Optional[int] = None
    match = re.search(r"(\d+)\s*(?:year|yr)", resume_data.get("experience") or "", re.IGNORECASE)
    if match:
        experience_years = int(match.group(1))

    profile = db.query(models.Profile).filter(models.Profile.user_id == user_id).first()
    if profile:
        changed = False
        if not profile.skills and skills:
            profile.skills = skills
            changed = True
        if profile.experience_years is None and experience_years is not None:
            profile.experience_years = experience_years
            changed = True
        if not profile.education and education:
            profile.education = education
            changed = True
        if changed:
            db.commit()
            db.refresh(profile)
    elif skills:
        # Only auto-create profile when we at least have skills
        profile = models.Profile(
            user_id=user_id,
            skills=skills,
            experience_years=experience_years,
            education=education,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)


def create_resume_data(db: Session, user_id: int, data: dict) -> models.ResumeData:
    """Upsert: update the existing ResumeData row for this user if one exists,
    otherwise create a new one. This prevents unbounded row growth on every Save."""
    existing = db.query(models.ResumeData).filter(
        models.ResumeData.user_id == user_id
    ).order_by(models.ResumeData.id.desc()).first()
    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        db.commit()
        db.refresh(existing)
        return existing
    resume = models.ResumeData(user_id=user_id, **data)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def get_resume_data(db: Session, user_id: int) -> List[models.ResumeData]:
    return db.query(models.ResumeData).filter(
        models.ResumeData.user_id == user_id
    ).all()


def create_otp(db: Session, phone: str) -> models.OTPCode:
    """
    Generate and store a 6-digit OTP for the given phone number.

    Rate limit: max 3 requests per phone within any 10-minute window.
    OTP expires after 5 minutes.
    Sends the OTP via the configured SMS provider (see utils/sms.py).
    """
    from fastapi import HTTPException, status as http_status

    # ── Rate limit check ─────────────────────────────────────────────────────
    window_start = datetime.utcnow() - timedelta(minutes=10)
    recent_count = (
        db.query(models.OTPCode)
        .filter(
            models.OTPCode.phone == phone,
            models.OTPCode.created_at >= window_start,
        )
        .count()
    )
    if recent_count >= 3:
        raise HTTPException(
            status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Please wait 10 minutes before trying again.",
        )

    # ── Generate OTP ─────────────────────────────────────────────────────────
    otp_value = f"{secrets.randbelow(1_000_000):06d}"

    # Delete any existing (unexpired) OTPs for this phone
    db.query(models.OTPCode).filter(models.OTPCode.phone == phone).delete()

    otp_record = models.OTPCode(
        phone=phone,
        otp=otp_value,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.add(otp_record)
    db.commit()
    db.refresh(otp_record)

    # ── Send SMS ─────────────────────────────────────────────────────────────
    try:
        from .utils.sms import send_otp_sms
        send_otp_sms(phone, otp_value)
    except Exception as exc:
        # Log and re-raise so the route returns a 503 if SMS delivery fails
        logger.error("SMS delivery failed for %s: %s", phone, exc)
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to send OTP. Please try again.",
        ) from exc

    return otp_record, otp_value


def verify_otp(db: Session, phone: str, otp: str) -> bool:
    record = (
        db.query(models.OTPCode)
        .filter(
            models.OTPCode.phone == phone,
            models.OTPCode.otp == otp,
            models.OTPCode.expires_at > datetime.utcnow(),
        )
        .first()
    )
    if not record:
        return False
    db.delete(record)
    db.commit()
    return True


def create_or_get_user(db: Session, phone: str) -> models.User:
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if not user:
        user = models.User(name="", phone=phone, phone_verified=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


# ── Refresh Token ─────────────────────────────────────────────────────────────

def save_refresh_token(
    db: Session, user_id: int, token: str, expires_at: datetime
) -> models.RefreshToken:
    rt = models.RefreshToken(user_id=user_id, token=token, expires_at=expires_at)
    db.add(rt)
    db.commit()
    db.refresh(rt)
    return rt


def get_refresh_token(db: Session, token: str) -> Optional[models.RefreshToken]:
    return (
        db.query(models.RefreshToken)
        .filter(models.RefreshToken.token == token)
        .first()
    )


def delete_refresh_token(db: Session, token: str) -> None:
    record = (
        db.query(models.RefreshToken)
        .filter(models.RefreshToken.token == token)
        .first()
    )
    if record:
        db.delete(record)
        db.commit()


def delete_all_user_refresh_tokens(db: Session, user_id: int) -> None:
    records = (
        db.query(models.RefreshToken)
        .filter(models.RefreshToken.user_id == user_id)
        .all()
    )
    for r in records:
        db.delete(r)
    db.commit()
