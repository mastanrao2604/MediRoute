#!/usr/bin/env python3
"""
Reset isolated test DB and seed deterministic fixtures.

TEST INFRASTRUCTURE ONLY — uses create_all for SQLite bootstrap.
Production app must use Alembic; this script never runs in production.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "mediroute-backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from tests.helpers.config import (  # noqa: E402
    CITY_ID,
    DEFAULT_DB_URL,
    FIXTURES,
    HOSP_LAT,
    HOSP_LNG,
    HOSP_PIN,
    MANIFEST_PATH,
    NURSE_PHONE,
    RECRUITER_PHONE,
    TEST_DATA,
    TEST_SECRET,
)

# Must set env before importing app modules
os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", TEST_SECRET)
os.environ.setdefault("DATABASE_URL", DEFAULT_DB_URL)
os.environ.setdefault("SMS_PROVIDER", "log")
os.environ.setdefault("OTP_FORCE_DEV", "1")
os.environ.setdefault("DISPATCH_ENABLED", "true")


def _engine_and_session(db_url: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.models  # noqa: F401 — register all tables
    from app.database import Base
    from tests.helpers.pg_migrate import is_postgres_url, reset_postgres_schema

    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)

    if is_postgres_url(db_url):
        reset_postgres_schema(db_url)
    else:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session()


def _seed_users(db):
    from app import models

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recruiter = models.User(
        name="Test Recruiter",
        phone=RECRUITER_PHONE,
        role=models.UserRole.recruiter,
        is_verified=True,
        phone_verified=True,
    )
    nurse = models.User(
        name="Test Nurse",
        phone=NURSE_PHONE,
        role=models.UserRole.nurse,
        phone_verified=True,
    )
    db.add(recruiter)
    db.add(nurse)
    db.flush()

    db.add(
        models.Profile(
            user_id=recruiter.id,
            current_location="Hyderabad",
            service_pincode=HOSP_PIN,
            service_locality="Test Hospital Area",
        )
    )
    db.add(
        models.Profile(
            user_id=nurse.id,
            current_location="Hyderabad",
            service_pincode=HOSP_PIN,
            service_locality="Nurse Service Area",
            location_source="gps",
        )
    )

    db.add(
        models.NurseAvailability(
            user_id=nurse.id,
            is_available=True,
            latitude=HOSP_LAT,
            longitude=HOSP_LNG,
            city_id=CITY_ID,
            last_seen=now,
            updated_at=now,
        )
    )
    db.add(
        models.PresenceState(
            user_id=nurse.id,
            state=models.PresenceStateEnum.online_available,
            last_heartbeat=now,
            city_id=CITY_ID,
        )
    )
    db.add(models.ReliabilityScore(user_id=nurse.id, score=100.0, total_offers=0))
    db.commit()
    db.refresh(recruiter)
    db.refresh(nurse)
    return recruiter, nurse


def _seed_lifecycle_fixtures(db, recruiter, nurse):
    """Pre-built shift states for dashboard / reconcile tests."""
    from app import models

    now = datetime.utcnow()
    fixtures = {}

    def _session(shift):
        s = models.DispatchSession(
            shift_request_id=shift.id,
            status=models.DispatchSessionStatus.completed,
        )
        db.add(s)
        db.flush()
        return s

    # Expired shift with stale offer
    expired = models.ShiftRequest(
        hospital_user_id=recruiter.id,
        role_required=models.UserRole.nurse,
        hospital_name="Fixture Expired",
        hospital_latitude=HOSP_LAT,
        hospital_longitude=HOSP_LNG,
        hospital_pincode=HOSP_PIN,
        shift_start=now - timedelta(hours=2),
        urgency=models.ShiftUrgency.standard,
        status=models.ShiftRequestStatus.expired,
        city_id=CITY_ID,
    )
    db.add(expired)
    db.flush()
    es = _session(expired)
    db.add(
        models.DispatchOffer(
            session_id=es.id,
            shift_request_id=expired.id,
            nurse_user_id=nurse.id,
            status=models.OfferStatus.timed_out,
            wave_number=1,
            expires_at=now - timedelta(hours=1),
        )
    )
    fixtures["expired_shift_id"] = expired.id

    # Cancelled shift
    cancelled = models.ShiftRequest(
        hospital_user_id=recruiter.id,
        role_required=models.UserRole.nurse,
        hospital_name="Fixture Cancelled",
        hospital_latitude=HOSP_LAT,
        hospital_longitude=HOSP_LNG,
        hospital_pincode=HOSP_PIN,
        shift_start=now + timedelta(hours=4),
        urgency=models.ShiftUrgency.standard,
        status=models.ShiftRequestStatus.cancelled,
        city_id=CITY_ID,
    )
    db.add(cancelled)
    db.flush()
    cs = _session(cancelled)
    db.add(
        models.DispatchOffer(
            session_id=cs.id,
            shift_request_id=cancelled.id,
            nurse_user_id=nurse.id,
            status=models.OfferStatus.cancelled,
            wave_number=1,
            expires_at=now + timedelta(hours=2),
        )
    )
    fixtures["cancelled_shift_id"] = cancelled.id

    # Under review (applied, not confirmed)
    review = models.ShiftRequest(
        hospital_user_id=recruiter.id,
        role_required=models.UserRole.nurse,
        hospital_name="Fixture Under Review",
        hospital_latitude=HOSP_LAT,
        hospital_longitude=HOSP_LNG,
        hospital_pincode=HOSP_PIN,
        shift_start=now + timedelta(hours=3),
        urgency=models.ShiftUrgency.standard,
        status=models.ShiftRequestStatus.dispatching,
        city_id=CITY_ID,
    )
    db.add(review)
    db.flush()
    rs = _session(review)
    ro = models.DispatchOffer(
        session_id=rs.id,
        shift_request_id=review.id,
        nurse_user_id=nurse.id,
        status=models.OfferStatus.accepted,
        wave_number=1,
        expires_at=now + timedelta(hours=3),
        responded_at=now,
    )
    db.add(ro)
    db.flush()
    ra = models.LiveAssignment(
        shift_request_id=review.id,
        nurse_user_id=nurse.id,
        offer_id=ro.id,
        status=models.AssignmentStatus.applied,
        confirmed_at=now,
    )
    db.add(ra)
    db.flush()
    fixtures["under_review_shift_id"] = review.id
    fixtures["under_review_assignment_id"] = ra.id
    fixtures["under_review_offer_id"] = ro.id

    # Historical recruiter-confirmed shift (completed — must NOT block new accepts)
    confirmed = models.ShiftRequest(
        hospital_user_id=recruiter.id,
        role_required=models.UserRole.nurse,
        hospital_name="Fixture Confirmed",
        hospital_latitude=HOSP_LAT,
        hospital_longitude=HOSP_LNG,
        hospital_pincode=HOSP_PIN,
        shift_start=now - timedelta(hours=4),
        shift_end=now - timedelta(hours=1),
        urgency=models.ShiftUrgency.standard,
        status=models.ShiftRequestStatus.filled,
        city_id=CITY_ID,
        search_closed_at=now - timedelta(hours=3),
        filled_at=now - timedelta(hours=3),
    )
    db.add(confirmed)
    db.flush()
    cfs = _session(confirmed)
    cfo = models.DispatchOffer(
        session_id=cfs.id,
        shift_request_id=confirmed.id,
        nurse_user_id=nurse.id,
        status=models.OfferStatus.accepted,
        wave_number=1,
        expires_at=now - timedelta(hours=2),
        responded_at=now - timedelta(hours=3),
    )
    db.add(cfo)
    db.flush()
    cfa = models.LiveAssignment(
        shift_request_id=confirmed.id,
        nurse_user_id=nurse.id,
        offer_id=cfo.id,
        status=models.AssignmentStatus.completed,
        recruiter_confirmed_at=now - timedelta(hours=3),
        confirmed_at=now - timedelta(hours=3),
        check_in_at=now - timedelta(hours=2),
        check_out_at=now - timedelta(hours=1),
    )
    db.add(cfa)
    db.flush()
    fixtures["confirmed_shift_id"] = confirmed.id
    fixtures["confirmed_assignment_id"] = cfa.id

    # No-show assignment (shift reopened)
    noshow = models.ShiftRequest(
        hospital_user_id=recruiter.id,
        role_required=models.UserRole.nurse,
        hospital_name="Fixture No Show",
        hospital_latitude=HOSP_LAT,
        hospital_longitude=HOSP_LNG,
        hospital_pincode=HOSP_PIN,
        shift_start=now - timedelta(minutes=20),
        urgency=models.ShiftUrgency.standard,
        status=models.ShiftRequestStatus.open,
        city_id=CITY_ID,
    )
    db.add(noshow)
    db.flush()
    ns = _session(noshow)
    nso = models.DispatchOffer(
        session_id=ns.id,
        shift_request_id=noshow.id,
        nurse_user_id=nurse.id,
        status=models.OfferStatus.accepted,
        wave_number=1,
        expires_at=now + timedelta(hours=1),
    )
    db.add(nso)
    db.flush()
    nsa = models.LiveAssignment(
        shift_request_id=noshow.id,
        nurse_user_id=nurse.id,
        offer_id=nso.id,
        status=models.AssignmentStatus.no_show,
        recruiter_confirmed_at=now - timedelta(hours=1),
        confirmed_at=now - timedelta(hours=1),
    )
    db.add(nsa)
    db.flush()
    fixtures["no_show_shift_id"] = noshow.id
    fixtures["no_show_assignment_id"] = nsa.id

    db.commit()
    return fixtures


def bootstrap(db_url: str | None = None) -> dict:
    url = db_url or DEFAULT_DB_URL
    TEST_DATA.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)

    _, db = _engine_and_session(url)
    try:
        recruiter, nurse = _seed_users(db)
        lifecycle = _seed_lifecycle_fixtures(db, recruiter, nurse)
        manifest = {
            "database_url": url,
            "recruiter_id": recruiter.id,
            "nurse_id": nurse.id,
            "recruiter_phone": RECRUITER_PHONE,
            "nurse_phone": NURSE_PHONE,
            "city_id": CITY_ID,
            "hospital": {"lat": HOSP_LAT, "lng": HOSP_LNG, "pincode": HOSP_PIN},
            "lifecycle_fixtures": lifecycle,
            "seeded_at": datetime.now(timezone.utc).isoformat(),
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
    finally:
        db.close()


def main() -> int:
    try:
        manifest = bootstrap()
        print(f"OK test DB seeded — recruiter={manifest['recruiter_id']} nurse={manifest['nurse_id']}")
        print(f"Manifest: {MANIFEST_PATH}")
        return 0
    except Exception as exc:
        print(f"FAIL bootstrap: {exc}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
