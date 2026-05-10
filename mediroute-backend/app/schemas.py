from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime
import re

from .models import UserRole, JobType, JobStatus, PassportStatus, ApplicationStatus

# ── Shared validator ──────────────────────────────────────────────────────────

_PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def _clean_phone(v: str) -> str:
    """Strip country-code prefixes and validate 10-digit Indian number."""
    cleaned = v.strip()
    if cleaned.startswith("+91"):
        cleaned = cleaned[3:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    elif cleaned.startswith("0") and len(cleaned) == 11:
        cleaned = cleaned[1:]
    if not _PHONE_RE.match(cleaned):
        raise ValueError(
            "Invalid phone number. Provide a 10-digit Indian mobile number."
        )
    return cleaned


# ─── Auth ─────────────────────────────────────────────────────────────────────

class OTPRequest(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _clean_phone(v)


class OTPVerify(BaseModel):
    phone: str
    otp: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _clean_phone(v)

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned.isdigit() or len(cleaned) != 6:
            raise ValueError("OTP must be exactly 6 digits.")
        return cleaned


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: str


class GoogleLoginRequest(BaseModel):
    token: str  # Google ID token from frontend


class GoogleLoginResponse(BaseModel):
    # Set when login is complete
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    # Set when phone verification is still required
    phone_verification_required: bool = False
    google_session_token: Optional[str] = None  # short-lived token to link phone


class GoogleLinkPhoneRequest(BaseModel):
    google_session_token: str
    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _clean_phone(v)


class GoogleVerifyPhoneRequest(BaseModel):
    google_session_token: str
    phone: str
    otp: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _clean_phone(v)

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned.isdigit() or len(cleaned) != 6:
            raise ValueError("OTP must be exactly 6 digits.")
        return cleaned


# ─── User ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    name: str
    phone: str
    role: Optional[UserRole] = None


class UserOnboarding(BaseModel):
    name: str
    role: UserRole


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: Optional[str]
    phone: Optional[str]
    email: Optional[str] = None
    role: Optional[UserRole]
    created_at: datetime
    phone_verified: bool = False
    # Recruiter verification fields
    company_name: Optional[str] = None
    official_email: Optional[str] = None
    is_verified: bool = False
    # Computed: True when the user has filled essential candidate profile fields
    profile_complete: bool = False


# ─── Profile ──────────────────────────────────────────────────────────────────

class ProfileCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    experience_years: int
    education: Optional[str] = None
    skills: str
    current_location: Optional[str] = None

    @field_validator("experience_years")
    @classmethod
    def experience_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("experience_years must be 0 or greater")
        return v

    @field_validator("skills")
    @classmethod
    def skills_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("skills must not be empty")
        return v.strip()


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    experience_years: Optional[int] = None
    education: Optional[str] = None
    skills: Optional[str] = None
    current_location: Optional[str] = None
    created_at: datetime


# ─── Preferences ──────────────────────────────────────────────────────────────

class PreferenceCreate(BaseModel):
    job_type: JobType
    preferred_country: Optional[str] = None
    passport_status: PassportStatus


class PreferenceResponse(PreferenceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime


# ─── Job ──────────────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    title: str
    hospital_name: str
    location: str
    job_type: JobType
    role_required: Optional[UserRole] = None
    country: Optional[str] = None
    salary: Optional[str] = None
    description: Optional[str] = None
    status: JobStatus = JobStatus.open
    company_id: Optional[int] = None
    recruiter_id: Optional[int] = None

    @field_validator("title", "hospital_name", "location")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("This field must not be blank")
        return v.strip()

    @field_validator("salary", "description", "country", mode="before")
    @classmethod
    def _strip_optional(cls, v):
        if isinstance(v, str):
            return v.strip() or None
        return v


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    hospital_name: Optional[str] = None
    location: Optional[str] = None
    job_type: Optional[JobType] = None
    role_required: Optional[UserRole] = None
    country: Optional[str] = None
    salary: Optional[str] = None
    description: Optional[str] = None
    status: JobStatus = JobStatus.open
    company_id: Optional[int] = None
    recruiter_id: Optional[int] = None
    created_at: datetime


class JobMatchResponse(BaseModel):
    job_id: int
    title: str
    hospital: Optional[str] = None
    location: Optional[str] = None
    score: int


# ─── Dashboard aggregate ──────────────────────────────────────────────────────

class DashboardResponse(BaseModel):
    """Single-request payload for the candidate dashboard.
    Replaces three separate API calls (/profile/me, /preferences/me, /applications/me).
    """
    model_config = ConfigDict(from_attributes=True)

    profile: Optional["ProfileResponse"] = None
    preferences: Optional["PreferenceResponse"] = None
    applications: List["ApplicationResponse"] = []


# ─── Application ──────────────────────────────────────────────────────────────

class ApplicationCreate(BaseModel):
    job_id: int


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    job_id: int
    status: ApplicationStatus
    created_at: datetime


# ─── Recruiter ────────────────────────────────────────────────────────────────

class ApplicantSummary(BaseModel):
    application_id: int
    status: ApplicationStatus
    applied_at: datetime
    candidate_name: Optional[str] = None
    experience: Optional[int] = None
    skills: Optional[str] = None
    location: Optional[str] = None
    candidate_user_id: Optional[int] = None
    has_resume: bool = False


class CandidateDetail(BaseModel):
    application_id: int
    job_id: int
    status: ApplicationStatus
    applied_at: datetime
    candidate_name: Optional[str] = None
    phone: Optional[str] = None
    experience_years: Optional[int] = None
    skills: Optional[str] = None
    education: Optional[str] = None
    location: Optional[str] = None
    resume_skills: Optional[str] = None
    resume_experience: Optional[str] = None
    candidate_user_id: Optional[int] = None
    has_resume: bool = False


# ─── Recruiter Profile ────────────────────────────────────────────────────────

class RecruiterProfileCreate(BaseModel):
    company_name: str
    official_email: str

    @field_validator("company_name")
    @classmethod
    def company_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("company_name must not be empty")
        return v.strip()

    @field_validator("official_email")
    @classmethod
    def email_not_empty(cls, v: str) -> str:
        if not v.strip() or "@" not in v:
            raise ValueError("A valid official_email is required")
        return v.strip().lower()


class RecruiterProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: Optional[str] = None
    phone: str
    company_name: Optional[str] = None
    official_email: Optional[str] = None
    is_verified: bool = False


# ─── Resume (file-based) ──────────────────────────────────────────────────────

class ResumeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    file_url: Optional[str] = None
    resume_data: Optional[dict] = None
    is_generated: bool = False
    created_at: datetime


# ─── Resume Builder (structured) ──────────────────────────────────────────────

class ResumeBuilderCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Required fields
    full_name: str
    skills: str
    # Optional fields
    phone: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    profile_summary: Optional[str] = None
    education: Optional[str] = None
    experience: Optional[str] = None
    languages: Optional[str] = None
    photo_url: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def full_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("full_name must not be empty")
        return v.strip()

    @field_validator("phone")
    @classmethod
    def phone_min_digits(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) < 10:
            raise ValueError("phone must contain at least 10 digits")
        return v.strip()

    @field_validator("skills")
    @classmethod
    def skills_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("skills must not be empty")
        return v.strip()


class ResumeBuilderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    profile_summary: Optional[str] = None
    education: Optional[str] = None
    experience: Optional[str] = None
    skills: Optional[str] = None
    languages: Optional[str] = None
    photo_url: Optional[str] = None
    created_at: datetime


class PDFParseResponse(BaseModel):
    message: str
    parsed_data: dict
