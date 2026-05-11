import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from mediroute-backend/ regardless of where uvicorn is launched from
load_dotenv(Path(__file__).parent.parent / ".env")

# ─── Sentry (init BEFORE anything else so it captures startup errors) ─────────
import sentry_sdk
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
_IS_PRODUCTION = os.getenv("ENV", "development").lower() == "production"
if _SENTRY_DSN and _IS_PRODUCTION:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        traces_sample_rate=0.1,      # 10 % of requests traced — adjust as needed
        environment="production",
    )

from fastapi import FastAPI, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from sqlalchemy.orm import Session

from .database import engine, SessionLocal, get_db
from . import models, crud
from .routes import auth, profile, preferences, jobs, applications, resume, user, recruiter, admin, dashboard, share, legal
from .services import otp_service as _otp_service

app = FastAPI(
    title="MediRoute API",
    description=(
        "Healthcare job platform — connecting medical professionals "
        "with opportunities in India and abroad."
    ),
    version="2.1.0",
    # Disable interactive docs in production — never expose schema to the public
    docs_url=None if _IS_PRODUCTION else "/docs",
    redoc_url=None if _IS_PRODUCTION else "/redoc",
    openapi_url=None if _IS_PRODUCTION else "/openapi.json",
)

# ─── GZip compression ────────────────────────────────────────────────────────
# Compress responses >= 1 KB. Reduces API payload sizes by 60-80% on mobile.
# Must be added BEFORE CORS middleware so the compressed response still
# carries correct CORS headers (middleware stack runs in reverse order).
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ─── Security + timing middleware ───────────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    global _warm
    import time
    t0 = time.perf_counter()
    is_cold = not _warm
    response: Response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
    if _IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Log slow requests so we can distinguish cold-start vs warm inefficiency
    label = "COLD" if is_cold else "WARM"
    path = request.url.path
    if elapsed_ms > 500 and path not in ("/health", "/health/db"):
        _startup_log.warning(
            "[PERF][%s] %s %s → %.0fms",
            label, request.method, path, elapsed_ms
        )
    elif elapsed_ms > 100 and path not in ("/health", "/health/db"):
        _startup_log.info(
            "[PERF][%s] %s %s → %.0fms",
            label, request.method, path, elapsed_ms
        )
    if not _warm:
        _warm = True
    return response

# ─── CORS ─────────────────────────────────────────────────────────────────────
# Set ALLOWED_ORIGINS env var to a comma-separated list for production.
# Capacitor Android uses https://localhost (androidScheme: "https").
_default_origins = (
    "http://localhost:5173,"
    "http://localhost:5174,"
    "http://localhost:3000,"
    "https://localhost,"        # Capacitor Android (androidScheme=https)
    "capacitor://localhost"     # Capacitor Android fallback / iOS
)
_configured_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]
# Always include Capacitor Android origins regardless of the ALLOWED_ORIGINS env var.
# The APK's WebView uses https://localhost (androidScheme=https) as its origin.
# Without these, every non-simple request (e.g. GET with Authorization header)
# fails the CORS preflight and the user sees "Failed to load jobs."
_capacitor_origins = ["https://localhost", "capacitor://localhost"]
_origins = list({*_configured_origins, *_capacitor_origins})

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Database ─────────────────────────────────────────────────────────────────
# create_all is wrapped in try-except so the backend starts even when the DB
# is temporarily unreachable (e.g., Supabase paused, network outage).
# In production, Alembic handles all schema changes (alembic upgrade head).
import logging as _logging
import time as _time
_startup_log = _logging.getLogger("uvicorn.error")
_startup_time = _time.time()   # used by /health uptime + cold-start detection
_warm = False                  # flips to True after first successful request
try:
    models.Base.metadata.create_all(bind=engine)
except Exception as _db_err:
    _startup_log.warning(
        "DB not reachable at startup — create_all skipped. "
        "Backend will start; DB-dependent routes will return 503 until DB recovers. "
        "Error: %s", _db_err
    )

# ─── OTP config validation ────────────────────────────────────────────────────
# Validates MSG91 env vars at startup. Raises RuntimeError (and aborts startup)
# if ENV=production but MSG91_AUTH_KEY / MSG91_TEMPLATE_ID are missing.
try:
    _otp_service.validate_production_config()
except RuntimeError as _otp_cfg_err:
    # Log the fatal error clearly before aborting
    _startup_log.critical("%s", _otp_cfg_err)
    raise

# ─── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(user.router)
app.include_router(profile.router)
app.include_router(preferences.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(resume.router)
app.include_router(recruiter.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
# Public sharing routes — no auth required, safe fields only
app.include_router(share.router)
# Public legal pages — Privacy Policy + account deletion info (Play Store required)
app.include_router(legal.router)


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
def health_check():
    """Lightweight keep-alive endpoint — safe for uptime monitors and cron pings.
    Returns immediately with no DB query and no auth overhead.
    """
    import time
    uptime_s = int(time.time() - _startup_time)
    return {
        "status": "healthy",
        "service": "MediRoute API",
        "uptime_seconds": uptime_s,
    }


# ─── Stats ────────────────────────────────────────────────────────────────────

@app.get("/stats", tags=["Stats"])
def get_stats(db: Session = Depends(get_db)):
    return {
        "total_users":        db.query(models.User).count(),
        "total_jobs":         db.query(models.Job).count(),
        "total_applications": db.query(models.Application).count(),
    }


@app.get("/health/db", tags=["Health"])
def health_db(db: Session = Depends(get_db)):
    """Lightweight DB connectivity check — SELECT 1 only. Safe for monitoring."""
    import time
    from sqlalchemy import text
    t0 = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "status": "healthy",
            "database": "connected",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        _startup_log.error("[health/db] DB unreachable: %s", exc)
        return {
            "status": "unhealthy",
            "database": "unreachable",
        }


# ─── Seed Data ────────────────────────────────────────────────────────────────

@app.on_event("startup")
def seed_data():
    """Populate sample companies, recruiters, and jobs on first run."""
    db = SessionLocal()
    try:
        if db.query(models.Job).count() > 0:
            return  # already seeded
    except Exception as _seed_err:
        _startup_log.warning("seed_data skipped — DB not reachable: %s", _seed_err)
        return
    try:
        # Re-open a fresh session for the actual seeding work
        db.close()
        db = SessionLocal()
        if db.query(models.Job).count() > 0:
            return  # double-check after re-open

        apollo = crud.create_company(db, name="Apollo Hospitals", location="Chennai", type="hospital")
        aster = crud.create_company(db, name="Aster DM Healthcare", location="Dubai", type="hospital")
        gulf = crud.create_company(db, name="Gulf Medical Consultancy", location="Riyadh", type="consultancy")

        r1 = crud.create_recruiter(db, name="Ravi Kumar", phone="9000000001", company_id=apollo.id)
        r2 = crud.create_recruiter(db, name="Aisha Al-Farsi", phone="9000000002", company_id=aster.id)

        seed_jobs = [
            {
                "title": "Staff Nurse – ICU",
                "role_required": models.UserRole.nurse,
                "hospital_name": "Apollo Hospitals",
                "location": "Chennai",
                "country": "India",
                "job_type": models.JobType.india,
                "salary": "35000",
                "description": (
                    "ICU nurse with 2+ years experience. Skills: critical care, "
                    "IV cannulation, ventilator management, patient monitoring."
                ),
                "company_id": apollo.id,
                "recruiter_id": r1.id,
            },
            {
                "title": "General Physician",
                "role_required": models.UserRole.doctor,
                "hospital_name": "Apollo Hospitals",
                "location": "Bangalore",
                "country": "India",
                "job_type": models.JobType.india,
                "salary": "90000",
                "description": (
                    "MBBS doctor for OPD. Skills: diagnosis, prescription writing, "
                    "patient management, clinical documentation."
                ),
                "company_id": apollo.id,
                "recruiter_id": r1.id,
            },
            {
                "title": "Registered Nurse – Dubai",
                "role_required": models.UserRole.nurse,
                "hospital_name": "Aster DM Healthcare",
                "location": "Dubai",
                "country": "UAE",
                "job_type": models.JobType.abroad,
                "salary": "AED 5000",
                "description": (
                    "DHA-licensed nurse for a multi-specialty hospital. "
                    "Skills: patient care, IV therapy, critical care, wound management."
                ),
                "company_id": aster.id,
                "recruiter_id": r2.id,
            },
            {
                "title": "Lab Technician",
                "role_required": models.UserRole.lab_tech,
                "hospital_name": "Apollo Hospitals",
                "location": "Hyderabad",
                "country": "India",
                "job_type": models.JobType.india,
                "salary": "28000",
                "description": (
                    "DMLT or equivalent. Skills: blood analysis, pathology, "
                    "lab equipment operation, sample processing."
                ),
                "company_id": apollo.id,
                "recruiter_id": r1.id,
            },
            {
                "title": "Pharmacist – Riyadh",
                "role_required": models.UserRole.pharmacist,
                "hospital_name": "Saudi German Hospital",
                "location": "Riyadh",
                "country": "Saudi Arabia",
                "job_type": models.JobType.abroad,
                "salary": "SAR 4500",
                "description": (
                    "B.Pharm with 1+ year experience. Skills: dispensing, "
                    "drug interaction, pharmacy management, inventory control."
                ),
                "company_id": gulf.id,
                "recruiter_id": r2.id,
            },
            {
                "title": "Front Office Executive",
                "role_required": models.UserRole.front_office,
                "hospital_name": "Aster Clinic",
                "location": "Kochi",
                "country": "India",
                "job_type": models.JobType.india,
                "salary": "22000",
                "description": (
                    "Hospital front desk role. Skills: patient coordination, billing, "
                    "communication, computer literacy, customer service."
                ),
                "company_id": aster.id,
            },
            {
                "title": "Medical Driver – Kuwait",
                "role_required": models.UserRole.driver,
                "hospital_name": "Kuwait Hospital",
                "location": "Kuwait City",
                "country": "Kuwait",
                "job_type": models.JobType.abroad,
                "salary": "KWD 200",
                "description": (
                    "Valid driving license required. Skills: driving, "
                    "basic first aid, patient transport, punctuality."
                ),
                "company_id": gulf.id,
            },
            {
                "title": "Staff Nurse – Cardiology",
                "role_required": models.UserRole.nurse,
                "hospital_name": "Medanta Hospital",
                "location": "Delhi",
                "country": "India",
                "job_type": models.JobType.india,
                "salary": "42000",
                "description": (
                    "Cardiology ward nurse. Skills: cardiac monitoring, "
                    "patient care, IV cannulation, ECG interpretation, critical care."
                ),
                "company_id": apollo.id,
                "recruiter_id": r1.id,
            },
            {
                "title": "Doctor – Emergency Medicine",
                "role_required": models.UserRole.doctor,
                "hospital_name": "Aster Hospital",
                "location": "Dubai",
                "country": "UAE",
                "job_type": models.JobType.abroad,
                "salary": "AED 18000",
                "description": (
                    "Emergency physician for busy A&E department. "
                    "Skills: emergency care, trauma management, diagnosis, ACLS."
                ),
                "company_id": aster.id,
                "recruiter_id": r2.id,
            },
            {
                "title": "Pharmacy Technician",
                "role_required": models.UserRole.pharmacist,
                "hospital_name": "Apollo Pharmacy",
                "location": "Mumbai",
                "country": "India",
                "job_type": models.JobType.india,
                "salary": "20000",
                "description": (
                    "Retail pharmacy role. Skills: dispensing, inventory, "
                    "drug labelling, customer service, computer billing."
                ),
                "company_id": apollo.id,
                "recruiter_id": r1.id,
            },
        ]

        for job_data in seed_jobs:
            crud.create_job(db, job_data)

    finally:
        db.close()