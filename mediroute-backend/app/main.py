import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from mediroute-backend/ regardless of where uvicorn is launched from
load_dotenv(Path(__file__).parent.parent / ".env")

# ─── Sentry (init BEFORE anything else so it captures startup errors) ─────────
import sentry_sdk
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
_IS_PRODUCTION = os.getenv("ENV", "development").lower() == "production"


def _sentry_before_send(event, hint):
    """
    Strip PII / secrets before forwarding captured events to Sentry.
    Called synchronously for every event — must be fast and never raise.
    Runs even when send_default_pii=False as a belt-and-suspenders guard.
    """
    _REDACT = frozenset({
        "otp", "password", "token", "access_token", "refresh_token",
        "authorization", "auth_key", "phone", "x-admin-secret",
    })
    try:
        body = event.get("request", {}).get("data")
        if isinstance(body, dict):
            for k in list(body):
                if k.lower() in _REDACT:
                    body[k] = "[REDACTED]"
    except Exception:
        pass
    try:
        hdrs = event.get("request", {}).get("headers", {})
        for h in ("Authorization", "X-Admin-Secret", "Cookie"):
            if h in hdrs:
                hdrs[h] = "[REDACTED]"
    except Exception:
        pass
    return event


if _SENTRY_DSN and _IS_PRODUCTION:
    from starlette.exceptions import HTTPException as _StarletteHTTPException
    from fastapi.exceptions import RequestValidationError as _RequestValidationError
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        traces_sample_rate=0.1,          # 10 % of requests traced — adjust as needed
        environment="production",
        release=os.getenv("RENDER_GIT_COMMIT", "unknown"),
        send_default_pii=False,          # never send IPs, cookies, or raw user data
        ignore_errors=[_StarletteHTTPException, _RequestValidationError],
        before_send=_sentry_before_send,
    )

from fastapi import FastAPI, Request, Response, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import asyncio

from sqlalchemy.orm import Session

from .database import engine, SessionLocal, get_db
from . import models, crud
from .routes import auth, profile, preferences, jobs, applications, resume, user, recruiter, admin, dashboard, share, legal
from .routes.availability import router as availability_router, device_router
from .routes.shifts import router as shifts_router
from .routes.dispatch_routes import router as ws_router, offer_router
from .routes.ops import router as ops_router
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

# ─── Dispatch janitor background task ─────────────────────────────────────────
# Started once at app startup. Expires stale offers every 30s.
_janitor_task = None

@app.on_event("startup")
async def start_janitor():
    global _janitor_task
    try:
        from .dispatch.janitor import run_janitor
        _janitor_task = asyncio.create_task(run_janitor())
        _startup_log.info("[startup] dispatch janitor started")
    except Exception as exc:
        _startup_log.warning("[startup] janitor failed to start: %s", exc)

@app.on_event("shutdown")
async def stop_janitor():
    if _janitor_task and not _janitor_task.done():
        _janitor_task.cancel()
        try:
            await _janitor_task
        except asyncio.CancelledError:
            pass

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
    "http://localhost,"         # Capacitor Android (androidScheme=http, dev)
    "https://localhost,"        # Capacitor Android (androidScheme=https, prod)
    "capacitor://localhost"     # Capacitor Android fallback / iOS
)
_configured_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]
# Always include Capacitor Android origins regardless of the ALLOWED_ORIGINS env var.
# androidScheme="http"  → origin is http://localhost  (current dev APK)
# androidScheme="https" → origin is https://localhost (production APK)
# capacitor://localhost is the iOS / older Capacitor fallback.
_capacitor_origins = ["http://localhost", "https://localhost", "capacitor://localhost"]
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
# ─── Dispatch / Real-time Staffing routes ─────────────────────────────────────
app.include_router(availability_router)
app.include_router(device_router)
app.include_router(shifts_router)
app.include_router(ws_router)        # WebSocket: /ws/{user_id}
app.include_router(offer_router)     # /dispatch/offers/...
app.include_router(ops_router)       # /admin/ops/...
from .routes import geo as geo_router  # noqa: E402
app.include_router(geo_router.router)


# ─── SPA static assets ────────────────────────────────────────────────────────
# Resolve the Vite build output directory relative to this file so it works
# both on Render (/opt/render/project/src/frontend/dist/) and locally.
_FRONTEND_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    _startup_log.info("[SPA] Serving frontend from %s", _FRONTEND_DIST)
    # Serve hashed JS/CSS asset bundles (content-addressed, long-lived cache)
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="vite-assets")
    # Serve PWA icon sprites
    _icons_dir = _FRONTEND_DIST / "icons"
    if _icons_dir.exists():
        app.mount("/icons", StaticFiles(directory=str(_icons_dir)), name="vite-icons")
else:
    _startup_log.warning("[SPA] frontend/dist not found — web UI unavailable (run: cd frontend && npm run build)")


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


# ─── SPA catch-all (MUST be last) ─────────────────────────────────────────────
# All backend API routes are registered above and take precedence.
# This handler fires only for paths that match NO registered route — i.e.
# React Router paths like /login, /profile, /dashboard, /jobs, /resume, etc.
#
# It also serves root-level Vite output files (sw.js, favicon.ico,
# manifest.webmanifest, workbox-*.js) by checking if the file exists on disk
# before falling back to index.html.
#
# Mobile APK: Capacitor bundles the dist/ folder directly — this route is
# never hit from the APK (APK makes API calls, not page navigations).
@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    if not _FRONTEND_DIST.exists():
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Frontend not available"}, status_code=404)

    # Serve real files that live at the dist/ root (favicon, sw.js, manifest, etc.)
    if full_path:
        candidate = _FRONTEND_DIST / full_path
        try:
            # Resolve prevents path traversal attacks
            resolved = candidate.resolve()
            dist_resolved = _FRONTEND_DIST.resolve()
            if resolved.is_relative_to(dist_resolved) and resolved.is_file():
                return FileResponse(str(resolved))
        except (ValueError, OSError):
            pass

    # SPA fallback: serve index.html so React Router handles the path
    return FileResponse(str(_FRONTEND_DIST / "index.html"))