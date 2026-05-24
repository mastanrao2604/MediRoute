#!/usr/bin/env python3
"""
One-time pre-production dispatch schema reset.

Drops and recreates dispatch transactional tables to match app/models.py.
Preserves users, profiles, auth, device_tokens, nurse_availability, presence_state, jobs.

Usage (from repo root):
  python scripts/apply_dispatch_schema_reset.py
  python scripts/apply_dispatch_schema_reset.py --stamp-only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "mediroute-backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from sqlalchemy import create_engine, text  # noqa: E402

from app.database import DATABASE_URL  # noqa: E402

RESET_SQL = (ROOT / "scripts" / "reset_dispatch_operational_schema.sql").read_text()
PROFILE_ALTER_SQL = """
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS service_pincode VARCHAR(10);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS service_locality VARCHAR(255);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS location_source VARCHAR(32);
"""
ALEMBIC_HEAD = "f6a7b8c9d0e1"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stamp-only", action="store_true", help="Only update alembic_version")
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:
        if not args.stamp_only:
            print("[reset] Applying profile column guards (e5)...")
            for stmt in PROFILE_ALTER_SQL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))
            print("[reset] Running dispatch operational schema reset...")
            conn.execute(text(RESET_SQL))
            print("[reset] Dispatch tables rebuilt.")

        row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
        if row:
            conn.execute(
                text("UPDATE alembic_version SET version_num = :v"),
                {"v": ALEMBIC_HEAD},
            )
        else:
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": ALEMBIC_HEAD},
            )
        print(f"[reset] alembic_version stamped to {ALEMBIC_HEAD}")

    # Verify ORM-critical columns exist
    with engine.connect() as conn:
        cols = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'live_assignments' ORDER BY 1"
            )
        ).fetchall()
        col_names = [r[0] for r in cols]
        print(f"[verify] live_assignments columns: {col_names}")
        enum_vals = conn.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = 'assignmentstatus' ORDER BY e.enumsortorder"
            )
        ).fetchall()
        print(f"[verify] assignmentstatus values: {[r[0] for r in enum_vals]}")


if __name__ == "__main__":
    main()
