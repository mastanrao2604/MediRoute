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
# StaticFiles intentionally NOT imported — uploads are served via protected
# /resume/download/{user_id} endpoint only, never as public static files.

from sqlalchemy.orm import Session

from .database import engine, SessionLocal, get_db
from . import models, crud
from .routes import auth, profile, preferences, jobs, applications, resume, user, recruiter, admin

app = FastAPI(
    title="MediRoute API",
    description=(
        "Healthcare job platform — connecting medical professionals "
        "with opportunities in India and abroad."
    ),
    version="2.0.0",
    # Disable interactive docs in production — never expose schema to the public
    docs_url=None if _IS_PRODUCTION else "/docs",
    redoc_url=None if _IS_PRODUCTION else "/redoc",
    openapi_url=None if _IS_PRODUCTION else "/openapi.json",
)

# ─── Security headers middleware ──────────────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if _IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
_origins = os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Database ─────────────────────────────────────────────────────────────────
# create_all is wrapped in try-except so the backend starts even when the DB
# is temporarily unreachable (e.g., Supabase paused, network outage).
# In production, Alembic handles all schema changes (alembic upgrade head).
import logging as _logging
_startup_log = _logging.getLogger("uvicorn.error")
try:
    models.Base.metadata.create_all(bind=engine)
except Exception as _db_err:
    _startup_log.warning(
        "DB not reachable at startup — create_all skipped. "
        "Backend will start; DB-dependent routes will return 503 until DB recovers. "
        "Error: %s", _db_err
    )

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


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "service": "MediRoute API", "version": "2.0.0"}


# ─── Stats ────────────────────────────────────────────────────────────────────

@app.get("/stats", tags=["Stats"])
def get_stats(db: Session = Depends(get_db)):
    return {
        "total_users":        db.query(models.User).count(),
        "total_jobs":         db.query(models.Job).count(),
        "total_applications": db.query(models.Application).count(),
    }


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}


@app.get("/health/db", tags=["Health"])
def health_db(db: Session = Depends(get_db)):
    """Verify database connectivity and return pool + table stats."""
    import time
    from sqlalchemy import text, inspect
    from .database import engine, DATABASE_URL

    t0 = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}

    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())

    pool = engine.pool
    pool_info = {
        "size": getattr(pool, "size", lambda: "n/a")(),
        "checked_in": getattr(pool, "checkedin", lambda: "n/a")(),
        "checked_out": getattr(pool, "checkedout", lambda: "n/a")(),
        "overflow": getattr(pool, "overflow", lambda: "n/a")(),
    }

    db_host = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "local"

    return {
        "status": "ok",
        "latency_ms": latency_ms,
        "db_host": db_host,
        "tables": tables,
        "table_count": len(tables),
        "pool": pool_info,
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