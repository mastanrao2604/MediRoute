from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, Enum,
    Text, Index, UniqueConstraint, Boolean, JSON, Float,
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from .database import Base


class UserRole(str, enum.Enum):
    nurse = "nurse"
    staff_nurse = "staff_nurse"
    icu_nurse = "icu_nurse"
    ot_nurse = "ot_nurse"
    emergency_nurse = "emergency_nurse"
    home_care_nurse = "home_care_nurse"
    doctor = "doctor"
    lab_tech = "lab_tech"
    pharmacist = "pharmacist"
    driver = "driver"
    front_office = "front_office"
    recruiter = "recruiter"


class JobType(str, enum.Enum):
    india = "india"
    abroad = "abroad"
    both = "both"


class JobStatus(str, enum.Enum):
    open = "open"
    closed = "closed"
    draft = "draft"
    pending = "pending"


class PassportStatus(str, enum.Enum):
    yes = "yes"
    no = "no"
    unknown = "unknown"


class ApplicationStatus(str, enum.Enum):
    applied = "applied"
    shortlisted = "shortlisted"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=True)
    phone = Column(String, unique=True, nullable=True)
    role = Column(Enum(UserRole), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Google OAuth fields
    email = Column(String, unique=True, nullable=True, index=True)
    google_id = Column(String, unique=True, nullable=True)
    phone_verified = Column(Boolean, default=False, nullable=False)

    # Recruiter verification fields
    company_name = Column(String, nullable=True)
    official_email = Column(String, nullable=True)
    is_verified = Column(Boolean, default=False, nullable=False)

    # Uploaded resume
    resume_url = Column(String, nullable=True)

    profile = relationship(
        "Profile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    preferences = relationship(
        "UserPreference", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    applications = relationship(
        "Application", back_populates="user", cascade="all, delete-orphan"
    )
    resumes = relationship(
        "Resume", back_populates="user", cascade="all, delete-orphan"
    )
    resume_data = relationship(
        "ResumeData", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_users_phone", "phone"),
        Index("idx_users_google_id", "google_id"),
        # Admin pending-recruiter query: WHERE role='recruiter' AND is_verified=false
        Index("idx_users_role_verified", "role", "is_verified"),
    )


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    experience_years = Column(Integer, nullable=True)
    education = Column(String, nullable=True)
    skills = Column(Text, nullable=True)
    current_location = Column(String, nullable=True)
    # Nurse service area for dispatch prioritisation (6-digit postal code, India).
    service_pincode = Column(String(10), nullable=True)
    service_locality = Column(String(255), nullable=True)
    location_source = Column(String(32), nullable=True)  # 'gps' | 'manual' | None
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="profile")


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    job_type = Column(Enum(JobType), nullable=False)
    preferred_country = Column(String, nullable=True)
    passport_status = Column(Enum(PassportStatus), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="preferences")


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    location = Column(String, nullable=True)
    type = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    recruiters = relationship("Recruiter", back_populates="company")
    jobs = relationship("Job", back_populates="company")


class Recruiter(Base):
    __tablename__ = "recruiters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="recruiters")
    jobs = relationship("Job", back_populates="recruiter")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    role_required = Column(Enum(UserRole), nullable=True)
    hospital_name = Column(String, nullable=True)
    location = Column(String, nullable=True)
    country = Column(String, nullable=True)
    job_type = Column(Enum(JobType), nullable=True)
    salary = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(Enum(JobStatus), nullable=False, default=JobStatus.open, server_default="open")
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    recruiter_id = Column(Integer, ForeignKey("recruiters.id"), nullable=True)
    posted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="jobs")
    recruiter = relationship("Recruiter", back_populates="jobs")
    applications = relationship("Application", back_populates="job")

    __table_args__ = (
        Index("idx_job_search", "role_required", "location", "job_type"),
        # get_jobs() always filters WHERE status='open' — most selective column first
        Index("idx_job_status", "status"),
        # get_recruiter_jobs() filters by posted_by_user_id ORDER BY created_at
        Index("idx_job_posted_by", "posted_by_user_id", "created_at"),
    )


class JobRecruiterArchive(Base):
    """Recruiter-hidden jobs (dashboard list only; job row kept for applicants)."""

    __tablename__ = "job_recruiter_archives"

    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    archived_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    archived_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    status = Column(Enum(ApplicationStatus), default=ApplicationStatus.applied, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="applications")
    job = relationship("Job", back_populates="applications")

    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_job"),
        Index("idx_application_user_job", "user_id", "job_id"),
    )


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    file_url = Column(String, nullable=True)
    resume_data = Column(JSON, nullable=True)
    is_generated = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="resumes")


class ResumeData(Base):
    __tablename__ = "resume_data"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    full_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    location = Column(String, nullable=True)
    profile_summary = Column(Text, nullable=True)
    education = Column(Text, nullable=True)
    experience = Column(Text, nullable=True)
    skills = Column(Text, nullable=True)
    languages = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="resume_data")


class OTPCode(Base):
    __tablename__ = "otp_codes"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, nullable=False)
    otp = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_otp_phone", "phone"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_refresh_tokens_user", "user_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch / Real-Time Staffing Models
# These are entirely additive — no existing table is modified.
# Phase 1: asyncio.Event dispatch, Haversine geo, single Render instance.
# Phase 2+: swap asyncio.Event → Redis pub/sub, add Redis GEO. Zero model changes.
# ─────────────────────────────────────────────────────────────────────────────

class PresenceStateEnum(str, enum.Enum):
    """Fine-grained online state. Maps to Redis TTL key at Stage 2."""
    offline = "offline"
    online_available = "online_available"   # visible to dispatch
    online_busy = "online_busy"             # on assignment — excluded from dispatch
    background = "background"               # app open but backgrounded


class DevicePlatform(str, enum.Enum):
    android = "android"
    ios = "ios"
    web = "web"


class ShiftUrgency(str, enum.Enum):
    """Controls wave timeout, radius expansion, and notification priority."""
    emergency = "emergency"   # P0: 30s waves, 3km start radius
    urgent = "urgent"          # P1: 90s waves, 5km start radius
    standard = "standard"      # P2: 300s waves, 10km start radius
    planned = "planned"        # P3: next-day, 15km radius


class ShiftRequestStatus(str, enum.Enum):
    open = "open"             # posted, not yet dispatching
    dispatching = "dispatching"
    filled = "filled"
    expired = "expired"       # all waves exhausted, no acceptance
    cancelled = "cancelled"


class DispatchSessionStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class OfferStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    timed_out = "timed_out"
    cancelled = "cancelled"


class OfferDeliveryMethod(str, enum.Enum):
    websocket = "websocket"
    fcm = "fcm"
    both = "both"


class AssignmentStatus(str, enum.Enum):
    confirmed = "confirmed"
    checked_in = "checked_in"
    completed = "completed"
    no_show = "no_show"
    cancelled = "cancelled"


class NurseAvailability(Base):
    """
    Current availability and last-known location for dispatch candidate selection.
    One row per nurse (upserted on toggle + heartbeat).
    Phase 1: PostGIS disabled — lat/lng stored as Float, Haversine used for distance.
    Phase 2: Redis GEO replaces hot-path queries; this table retained for writes + analytics.
    """
    __tablename__ = "nurse_availability"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    is_available = Column(Boolean, nullable=False, default=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    city_id = Column(String(10), nullable=False, default="HYD")
    last_seen = Column(DateTime, nullable=True)  # updated by heartbeat
    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # Dispatch candidate query: city + available + last_seen freshness check
        Index("idx_avail_city_available", "city_id", "is_available"),
        Index("idx_avail_last_seen", "last_seen"),
        Index("idx_avail_user", "user_id"),
    )


class PresenceState(Base):
    """
    Fine-grained presence tracking. The 'supply inventory' of the platform.
    Stale last_heartbeat (>5 min) = nurse is offline for dispatch purposes.

    FUTURE hooks (columns present but not consumed by dispatch engine yet):
    - historical_preferences: JSONB for ML training data
    - preferred_shift_types: nurse-set or ML-inferred preferences
    - preferred_radius_km: dispatch suppression if shift is beyond this
    """
    __tablename__ = "presence_state"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    state = Column(
        Enum(PresenceStateEnum),
        nullable=False,
        default=PresenceStateEnum.offline,
        server_default="offline",
    )
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    city_id = Column(String(10), nullable=False, default="HYD")
    last_heartbeat = Column(DateTime, nullable=True)
    last_location_at = Column(DateTime, nullable=True)

    # FUTURE: ML dispatch ranking hooks (§24.1) — populated by future preference pipeline
    historical_preferences = Column(JSON, nullable=True)
    preferred_shift_types = Column(JSON, nullable=True)
    preferred_radius_km = Column(Float, nullable=True)  # None = no restriction

    __table_args__ = (
        Index("idx_presence_city_state", "city_id", "state"),
        Index("idx_presence_heartbeat", "last_heartbeat"),
    )


class DeviceToken(Base):
    """
    FCM device tokens — one per user per platform.
    Upserted on every app launch via PUT /devices/token.
    Stored separately from nurse_availability so tokens survive availability toggle.
    """
    __tablename__ = "device_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fcm_token = Column(String, nullable=False)
    platform = Column(
        Enum(DevicePlatform), nullable=False, default=DevicePlatform.android
    )
    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_device_token_user_platform"),
        Index("idx_device_tokens_user", "user_id"),
        Index("idx_device_tokens_fcm", "fcm_token"),
    )


class ShiftRequest(Base):
    """
    A hospital's request for a healthcare worker — the demand side of the marketplace.

    city_id is the shard key from day 1 (§22). All dispatch queries filter by city_id first.
    idempotency_key prevents duplicate shifts from network retries.
    """
    __tablename__ = "shift_requests"

    id = Column(Integer, primary_key=True)
    city_id = Column(String(10), nullable=False, default="HYD")  # shard key — never rename
    hospital_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role_required = Column(Enum(UserRole), nullable=False)
    specialty = Column(String, nullable=True)
    hospital_name = Column(String, nullable=False)
    hospital_latitude = Column(Float, nullable=False)
    hospital_longitude = Column(Float, nullable=False)
    hospital_pincode = Column(String(10), nullable=True)
    shift_start = Column(DateTime, nullable=False)
    shift_end = Column(DateTime, nullable=True)
    status = Column(
        Enum(ShiftRequestStatus),
        nullable=False,
        default=ShiftRequestStatus.open,
        server_default="open",
    )
    urgency = Column(
        Enum(ShiftUrgency),
        nullable=False,
        default=ShiftUrgency.standard,
        server_default="standard",
    )
    pay_rate = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    idempotency_key = Column(String, nullable=True, unique=True)
    dispatch_radius_km = Column(Float, nullable=False, default=10.0)
    filled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_shift_city_status", "city_id", "status"),
        Index("idx_shift_hospital_user", "hospital_user_id", "created_at"),
        Index("idx_shift_idempotency", "idempotency_key"),
        Index("idx_shift_status_created", "status", "created_at"),
    )


class DispatchSession(Base):
    """
    One dispatch run per ShiftRequest. Tracks wave progression.
    Unique per shift — a shift can only have one active dispatch session.
    """
    __tablename__ = "dispatch_sessions"

    id = Column(Integer, primary_key=True)
    shift_request_id = Column(
        Integer, ForeignKey("shift_requests.id"), nullable=False, unique=True
    )
    status = Column(
        Enum(DispatchSessionStatus),
        nullable=False,
        default=DispatchSessionStatus.active,
    )
    current_wave = Column(Integer, nullable=False, default=1)
    waves_exhausted = Column(Boolean, nullable=False, default=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_dsession_shift", "shift_request_id"),
        Index("idx_dsession_status", "status"),
    )


class DispatchOffer(Base):
    """
    One offer sent to one nurse as part of a dispatch wave.
    First-accept-wins via SELECT FOR UPDATE SKIP LOCKED on acceptance.
    expires_at enforces the wave timeout on the DB side.
    """
    __tablename__ = "dispatch_offers"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("dispatch_sessions.id"), nullable=False)
    shift_request_id = Column(Integer, ForeignKey("shift_requests.id"), nullable=False)
    nurse_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(
        Enum(OfferStatus), nullable=False, default=OfferStatus.pending
    )
    wave_number = Column(Integer, nullable=False, default=1)
    offered_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    responded_at = Column(DateTime, nullable=True)
    delivery_method = Column(
        Enum(OfferDeliveryMethod),
        nullable=False,
        default=OfferDeliveryMethod.websocket,
    )

    __table_args__ = (
        # Hot query: find pending offer for nurse (used in accept/decline handler)
        Index("idx_offer_nurse_pending", "nurse_user_id", "status"),
        Index("idx_offer_session", "session_id"),
        Index("idx_offer_shift", "shift_request_id"),
        # Janitor query: expire stale pending offers
        Index("idx_offer_expires_status", "expires_at", "status"),
    )


class LiveAssignment(Base):
    """
    Confirmed nurse ↔ shift assignment. The terminal state of a successful dispatch.
    check_in_latitude/longitude validated against hospital coords for attendance fraud prevention (§21.2).
    """
    __tablename__ = "live_assignments"

    id = Column(Integer, primary_key=True)
    shift_request_id = Column(
        Integer, ForeignKey("shift_requests.id"), nullable=False, unique=True
    )
    nurse_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    offer_id = Column(Integer, ForeignKey("dispatch_offers.id"), nullable=False)
    status = Column(
        Enum(AssignmentStatus), nullable=False, default=AssignmentStatus.confirmed
    )
    confirmed_at = Column(DateTime, default=datetime.utcnow)
    check_in_at = Column(DateTime, nullable=True)
    check_out_at = Column(DateTime, nullable=True)
    check_in_latitude = Column(Float, nullable=True)
    check_in_longitude = Column(Float, nullable=True)

    __table_args__ = (
        Index("idx_assignment_nurse_status", "nurse_user_id", "status"),
        Index("idx_assignment_shift", "shift_request_id"),
    )


class ReliabilityScore(Base):
    """
    Nurse reliability score — core dispatch ranking signal (not just a dashboard metric).
    Score starts at 100.0 and decays on declines/timeouts/no-shows.
    Phase 1: recalculated on each offer event. Phase 3+: background job.
    """
    __tablename__ = "reliability_scores"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    score = Column(Float, nullable=False, default=100.0)
    total_offers = Column(Integer, nullable=False, default=0)
    accepted = Column(Integer, nullable=False, default=0)
    declined = Column(Integer, nullable=False, default=0)
    timed_out = Column(Integer, nullable=False, default=0)
    no_shows = Column(Integer, nullable=False, default=0)
    completed_shifts = Column(Integer, nullable=False, default=0)
    last_calculated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_reliability_user", "user_id"),
        # Dispatch ranking: sort by score DESC
        Index("idx_reliability_score", "score"),
    )


class ShiftTimelineEvent(Base):
    """
    Immutable audit log for every significant dispatch action.
    SACRED INFRASTRUCTURE (§24.8): every state change must emit an event.
    Phase 1: written to PostgreSQL.
    Phase 3: wrapped by Kafka producer — zero model changes needed.
    actor_user_id is not a FK intentionally — allows recording events for deleted users.
    """
    __tablename__ = "shift_timeline_events"

    id = Column(Integer, primary_key=True)
    shift_request_id = Column(Integer, ForeignKey("shift_requests.id"), nullable=False)
    event_type = Column(String(64), nullable=False)  # see dispatch/events.py
    actor_user_id = Column(Integer, nullable=True)   # NOT FK — survives user deletion
    city_id = Column(String(10), nullable=False, default="HYD")
    payload = Column(JSON, nullable=True)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_timeline_shift_time", "shift_request_id", "occurred_at"),
        Index("idx_timeline_city_type", "city_id", "event_type"),
    )


class DispatchZone(Base):
    """
    Hyperlocal geographic zone — the unit of marketplace liquidity (§22).
    Density > Geography: launch 'Banjara Hills zone' not 'Hyderabad'.
    dispatch_paused: ops can halt a zone without a deploy (ZoneOperationalConfig hook, §24.10).
    """
    __tablename__ = "dispatch_zones"

    id = Column(Integer, primary_key=True)
    city_id = Column(String(10), nullable=False)
    zone_code = Column(String(20), unique=True, nullable=False)
    zone_name = Column(String, nullable=False)
    center_latitude = Column(Float, nullable=False)
    center_longitude = Column(Float, nullable=False)
    radius_km = Column(Float, nullable=False, default=10.0)
    is_active = Column(Boolean, nullable=False, default=True)
    # FUTURE: ZoneOperationalConfig (§24.10) — tune per zone without redeploy
    dispatch_paused = Column(Boolean, nullable=False, default=False)
    max_radius_km = Column(Float, nullable=True)   # operational cap on radius expansion
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_zone_city_active", "city_id", "is_active"),
    )


class SupplyDemandSnapshot(Base):
    """
    FUTURE: Zone stress tracking for supply-demand heatmap and surge/incentive systems (§24.2).
    Not wired to dispatch engine in Phase 1. Table exists for future cron writer.
    """
    __tablename__ = "supply_demand_snapshots"

    id = Column(Integer, primary_key=True)
    zone_code = Column(String(20), nullable=False)
    city_id = Column(String(10), nullable=False)
    snapshot_at = Column(DateTime, nullable=False)
    online_nurses = Column(Integer, nullable=False, default=0)
    pending_shifts = Column(Integer, nullable=False, default=0)
    avg_fill_time_sec = Column(Float, nullable=True)

    __table_args__ = (
        Index("idx_sds_zone_time", "zone_code", "snapshot_at"),
    )