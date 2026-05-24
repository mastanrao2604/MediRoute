#!/usr/bin/env python3
"""Verify no-show lifecycle: manual mark + auto janitor path."""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mediroute-backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / "mediroute-backend" / ".env")

from app.database import SessionLocal
from app import models
from app.routes.shifts import (
    mark_no_show,
    MarkNoShowRequest,
    process_auto_no_shows_sync,
    NO_SHOW_GRACE_MINUTES,
)


def _recruiter(db):
    return (
        db.query(models.User)
        .filter(models.User.role == models.UserRole.recruiter, models.User.is_verified.is_(True))
        .first()
    )


def _nurse(db):
    return db.query(models.User).filter(models.User.role == models.UserRole.nurse).first()


async def test_manual_no_show():
    db = SessionLocal()
    try:
        recruiter = _recruiter(db)
        nurse = _nurse(db)
        if not recruiter or not nurse:
            print("SKIP: need recruiter+nurse")
            return True

        shift = models.ShiftRequest(
            hospital_user_id=recruiter.id,
            role_required=models.UserRole.nurse,
            hospital_name="No-show Test",
            hospital_latitude=17.41,
            hospital_longitude=78.44,
            hospital_pincode="500072",
            shift_start=datetime.utcnow() - timedelta(minutes=10),
            urgency=models.ShiftUrgency.standard,
            status=models.ShiftRequestStatus.filled,
            city_id="HYD",
            search_closed_at=datetime.utcnow(),
            filled_at=datetime.utcnow(),
        )
        db.add(shift)
        db.flush()
        session = models.DispatchSession(
            shift_request_id=shift.id,
            status=models.DispatchSessionStatus.completed,
        )
        db.add(session)
        db.flush()
        offer = models.DispatchOffer(
            session_id=session.id,
            shift_request_id=shift.id,
            nurse_user_id=nurse.id,
            status=models.OfferStatus.accepted,
            wave_number=1,
            expires_at=datetime.utcnow() + timedelta(hours=4),
        )
        db.add(offer)
        db.flush()
        now = datetime.utcnow()
        assignment = models.LiveAssignment(
            shift_request_id=shift.id,
            nurse_user_id=nurse.id,
            offer_id=offer.id,
            status=models.AssignmentStatus.confirmed,
            recruiter_confirmed_at=now - timedelta(hours=1),
            confirmed_at=now - timedelta(hours=1),
        )
        db.add(assignment)
        db.commit()
        db.refresh(shift)
        db.refresh(assignment)

        out = await mark_no_show(
            shift.id,
            MarkNoShowRequest(nurse_user_id=nurse.id),
            recruiter,
            db,
        )
        db.refresh(shift)
        db.refresh(assignment)

        assert out["no_show"] is True
        assert assignment.status == models.AssignmentStatus.no_show
        assert shift.status == models.ShiftRequestStatus.open
        assert shift.search_closed_at is None
        print("PASS manual_no_show")
        return True
    except Exception as exc:
        print(f"FAIL manual_no_show: {exc}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        db.close()


def test_auto_no_show():
    db = SessionLocal()
    try:
        recruiter = _recruiter(db)
        nurse = _nurse(db)
        if not recruiter or not nurse:
            print("SKIP auto: need users")
            return True

        shift = models.ShiftRequest(
            hospital_user_id=recruiter.id,
            role_required=models.UserRole.nurse,
            hospital_name="Auto No-show Test",
            hospital_latitude=17.41,
            hospital_longitude=78.44,
            hospital_pincode="500072",
            shift_start=datetime.utcnow() - timedelta(minutes=NO_SHOW_GRACE_MINUTES + 5),
            urgency=models.ShiftUrgency.standard,
            status=models.ShiftRequestStatus.filled,
            city_id="HYD",
            search_closed_at=datetime.utcnow(),
        )
        db.add(shift)
        db.flush()
        session = models.DispatchSession(
            shift_request_id=shift.id,
            status=models.DispatchSessionStatus.completed,
        )
        db.add(session)
        db.flush()
        offer = models.DispatchOffer(
            session_id=session.id,
            shift_request_id=shift.id,
            nurse_user_id=nurse.id,
            status=models.OfferStatus.accepted,
            wave_number=1,
            expires_at=datetime.utcnow() + timedelta(hours=4),
        )
        db.add(offer)
        db.flush()
        now = datetime.utcnow()
        assignment = models.LiveAssignment(
            shift_request_id=shift.id,
            nurse_user_id=nurse.id,
            offer_id=offer.id,
            status=models.AssignmentStatus.confirmed,
            recruiter_confirmed_at=now - timedelta(hours=2),
            confirmed_at=now - timedelta(hours=2),
        )
        db.add(assignment)
        db.commit()

        count = process_auto_no_shows_sync(db, datetime.utcnow())
        db.refresh(assignment)
        assert count >= 1
        assert assignment.status == models.AssignmentStatus.no_show
        print(f"PASS auto_no_show (count={count})")
        return True
    except Exception as exc:
        print(f"FAIL auto_no_show: {exc}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        db.close()


def main():
    results = [asyncio.run(test_manual_no_show()), test_auto_no_show()]
    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} no-show tests passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
