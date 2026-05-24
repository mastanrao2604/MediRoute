#!/usr/bin/env python3
"""Smoke-test shift list/create against live DB after schema reset."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mediroute-backend"))

from dotenv import load_dotenv
load_dotenv(ROOT / "mediroute-backend" / ".env")

from app.database import SessionLocal
from app import models
from app.routes.shifts import list_shifts, create_shift, ShiftCreateRequest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock


def main():
    db = SessionLocal()
    try:
        recruiter = (
            db.query(models.User)
            .filter(models.User.role == models.UserRole.recruiter)
            .first()
        )
        if not recruiter:
            print("FAIL: no recruiter user in DB")
            return 1
        print(f"Recruiter uid={recruiter.id} role={recruiter.role.value} verified={recruiter.is_verified}")

        result = list_shifts(current_user=recruiter, db=db)
        print(f"GET /shifts/ OK — count={len(result['shifts'])}")

        if not recruiter.is_verified:
            print("SKIP POST: recruiter not verified")
            return 0

        req = ShiftCreateRequest(
            role_required="nurse",
            hospital_name="Schema Reset Test Hospital",
            hospital_latitude=17.4126,
            hospital_longitude=78.4471,
            hospital_pincode="500072",
            shift_start=datetime.now(timezone.utc) + timedelta(hours=2),
            urgency="standard",
            idempotency_key=f"schema-reset-test-{datetime.utcnow().timestamp()}",
        )
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        import asyncio
        out = asyncio.run(create_shift(req, bg, recruiter, db))
        print(f"POST /shifts/ OK — shift_id={out['shift']['id']} created={out['created']}")

        result2 = list_shifts(current_user=recruiter, db=db)
        print(f"GET /shifts/ after post OK — count={len(result2['shifts'])}")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
