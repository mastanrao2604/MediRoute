#!/usr/bin/env python3
"""Repeated recruiter shift create/list/cancel stress test against live DB."""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mediroute-backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / "mediroute-backend" / ".env")

from fastapi import BackgroundTasks
from app.database import SessionLocal
from app import models
from app.routes.shifts import (
    ShiftCreateRequest,
    create_shift,
    list_shifts,
    cancel_shift,
)


def _recruiter(db):
    return (
        db.query(models.User)
        .filter(models.User.role == models.UserRole.recruiter, models.User.is_verified.is_(True))
        .first()
    )


async def _create(db, recruiter, *, suffix: str):
    req = ShiftCreateRequest(
        role_required="nurse",
        hospital_name=f"Stress Test Hospital {suffix}",
        hospital_latitude=17.4126,
        hospital_longitude=78.4471,
        hospital_pincode="500072",
        shift_start=datetime.now(timezone.utc) + timedelta(hours=2),
        urgency="standard",
        idempotency_key=str(uuid.uuid4()),
    )
    bg = BackgroundTasks()
    return await create_shift(req, bg, recruiter, db)


async def main():
    db = SessionLocal()
    try:
        recruiter = _recruiter(db)
        if not recruiter:
            print("FAIL: no verified recruiter")
            return 1

        create_count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
        errors = 0
        created_ids = []

        print(f"Recruiter uid={recruiter.id} — creating {create_count} shifts…")
        for i in range(create_count):
            try:
                out = await _create(db, recruiter, suffix=str(i))
                sid = out["shift"]["id"]
                created_ids.append(sid)
                if not out.get("created"):
                    print(f"  WARN duplicate at iteration {i}")
            except Exception as exc:
                errors += 1
                print(f"  FAIL create #{i}: {exc}")

        print(f"Created {len(created_ids)} shifts, errors={errors}")

        for _ in range(5):
            result = list_shifts(current_user=recruiter, db=db)
            count = len(result.get("shifts") or [])
            if count == 0 and created_ids:
                print("FAIL: list_shifts returned empty after creates")
                return 1

        print(f"GET /shifts/ OK — count={count}")

        # cancel + idempotent recreate key on last shift
        if created_ids:
            last_id = created_ids[-1]
            await cancel_shift(last_id, None, recruiter, db)
            db.refresh(
                db.query(models.ShiftRequest).filter(models.ShiftRequest.id == last_id).first()
            )
            shift = db.query(models.ShiftRequest).filter(models.ShiftRequest.id == last_id).first()
            assert shift.status == models.ShiftRequestStatus.cancelled
            print(f"Cancel OK shift_id={last_id}")

        result2 = list_shifts(current_user=recruiter, db=db)
        cancelled = [s for s in result2["shifts"] if s.get("status") == "cancelled"]
        print(f"Historical cancelled rows visible: {len(cancelled)}")

        if errors > 0:
            print(f"FAIL: {errors} create errors")
            return 1
        print(f"PASS: {create_count} creates, 5 list refreshes, cancel+historical load")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
