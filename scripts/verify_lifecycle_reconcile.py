#!/usr/bin/env python3
"""Operational torture tests: DB lifecycle truth survives without WebSocket delivery."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mediroute-backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / "mediroute-backend" / ".env")

from app.database import SessionLocal
from app import models
from app.dispatch.engine import _expire_shift_past_start_unfilled_sync
from app.routes.dispatch_routes import reconcile_dispatch_state, get_pending_offers
from app.routes.shifts import cancel_shift
import asyncio


def _recruiter(db):
    return (
        db.query(models.User)
        .filter(models.User.role == models.UserRole.recruiter, models.User.is_verified.is_(True))
        .first()
    )


def _nurse(db):
    return (
        db.query(models.User)
        .filter(models.User.role == models.UserRole.nurse)
        .first()
    )


def _make_shift_with_offer(db, recruiter, nurse, *, past_start=False):
    shift = models.ShiftRequest(
        hospital_user_id=recruiter.id,
        role_required="nurse",
        hospital_name="Lifecycle Test",
        hospital_latitude=17.41,
        hospital_longitude=78.44,
        hospital_pincode="500072",
        shift_start=(
            datetime.utcnow() - timedelta(minutes=5)
            if past_start
            else datetime.utcnow() + timedelta(hours=3)
        ),
        urgency=models.ShiftUrgency.standard,
        status=models.ShiftRequestStatus.dispatching,
        city_id=1,
    )
    db.add(shift)
    db.flush()
    session = models.DispatchSession(
        shift_request_id=shift.id,
        status=models.DispatchSessionStatus.active,
    )
    db.add(session)
    db.flush()
    offer = models.DispatchOffer(
        session_id=session.id,
        shift_request_id=shift.id,
        nurse_user_id=nurse.id,
        status=models.OfferStatus.pending,
        expires_at=datetime.utcnow() + timedelta(hours=1),
        wave_number=1,
    )
    db.add(offer)
    db.commit()
    db.refresh(shift)
    db.refresh(offer)
    return shift, offer


def test_expire_db_without_ws():
    """A/H: DB expiry commits from sync path; reconcile clears stale offers."""
    db = SessionLocal()
    try:
        recruiter = _recruiter(db)
        nurse = _nurse(db)
        if not recruiter or not nurse:
            print("SKIP expire: need recruiter+nurse")
            return True

        shift, offer = _make_shift_with_offer(db, recruiter, nurse, past_start=True)

        ok = _expire_shift_past_start_unfilled_sync(db, shift)
        db.refresh(shift)
        db.refresh(offer)

        assert ok, "expiry should return True"
        assert shift.status == models.ShiftRequestStatus.expired
        assert offer.status == models.OfferStatus.timed_out

        pending = get_pending_offers(current_user=nurse, db=db)
        offer_ids = [o["offer_id"] for o in pending.get("offers", [])]
        assert offer.id not in offer_ids, "expired offer must not appear in pending"

        recon = reconcile_dispatch_state(current_user=nurse, db=db)
        assert shift.id in recon["clear_offer_shift_ids"]
        assert any(t["shift_id"] == shift.id for t in recon["terminal_shifts"])

        print("PASS expire_db_without_ws")
        return True
    except Exception as exc:
        print(f"FAIL expire_db_without_ws: {exc}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        db.close()


def test_cancel_reconcile():
    """G: Recruiter cancel clears nurse reconcile state."""
    db = SessionLocal()
    try:
        recruiter = _recruiter(db)
        nurse = _nurse(db)
        if not recruiter or not nurse:
            print("SKIP cancel: need recruiter+nurse")
            return True

        shift, offer = _make_shift_with_offer(db, recruiter, nurse, past_start=False)

        asyncio.run(cancel_shift(shift.id, None, recruiter, db))
        db.refresh(shift)
        db.refresh(offer)

        assert shift.status == models.ShiftRequestStatus.cancelled
        assert offer.status == models.OfferStatus.cancelled

        recon = reconcile_dispatch_state(current_user=nurse, db=db)
        assert shift.id in recon["clear_offer_shift_ids"]

        rec_recon = reconcile_dispatch_state(current_user=recruiter, db=db)
        assert any(t["shift_id"] == shift.id for t in rec_recon["terminal_shifts"])

        print("PASS cancel_reconcile")
        return True
    except Exception as exc:
        print(f"FAIL cancel_reconcile: {exc}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        db.close()


def main():
    results = [
        test_expire_db_without_ws(),
        test_cancel_reconcile(),
    ]
    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} torture tests passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
