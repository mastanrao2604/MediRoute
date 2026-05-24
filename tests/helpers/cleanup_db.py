"""Test-harness DB cleanup when API cannot cancel filled/confirmed shifts."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "mediroute-backend"
sys.path.insert(0, str(BACKEND))


def backdate_shift_start(shift_id: int, *, minutes_ago: int = 10, db_url: str | None = None) -> None:
    """TEST INFRASTRUCTURE ONLY — move shift_start into the past for no-show tests."""
    if db_url:
        os.environ["DATABASE_URL"] = db_url

    from datetime import datetime, timedelta
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.models  # noqa: F401
    from app import models

    url = os.environ.get("DATABASE_URL", "")
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        shift = db.query(models.ShiftRequest).filter(models.ShiftRequest.id == shift_id).first()
        if shift:
            shift.shift_start = datetime.utcnow() - timedelta(minutes=minutes_ago)
            db.commit()
    finally:
        db.close()


def force_clear_blocking_assignments(nurse_id: int, db_url: str | None = None) -> int:
    """
    TEST INFRASTRUCTURE ONLY — cancels blocking live_assignments rows directly.
    Used when recruiter cancel returns 409 on filled shifts during test teardown.
    """
    if db_url:
        os.environ["DATABASE_URL"] = db_url

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.models  # noqa: F401
    from app import models

    url = os.environ.get("DATABASE_URL", "")
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    Session = sessionmaker(bind=engine)
    db = Session()
    cleared = 0
    try:
        rows = (
            db.query(models.LiveAssignment)
            .join(
                models.ShiftRequest,
                models.LiveAssignment.shift_request_id == models.ShiftRequest.id,
            )
            .filter(
                models.LiveAssignment.nurse_user_id == nurse_id,
                models.LiveAssignment.status.in_(
                    (
                        models.AssignmentStatus.confirmed,
                        models.AssignmentStatus.checked_in,
                        models.AssignmentStatus.applied,
                    )
                ),
                models.ShiftRequest.status.notin_(
                    (
                        models.ShiftRequestStatus.cancelled,
                        models.ShiftRequestStatus.expired,
                    )
                ),
            )
            .all()
        )
        for assignment in rows:
            assignment.status = models.AssignmentStatus.cancelled
            cleared += 1
        if cleared:
            db.commit()
    finally:
        db.close()
    return cleared
