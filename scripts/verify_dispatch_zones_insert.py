"""Verify dispatch_zones seed INSERT with explicit is_active=TRUE."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parents[1] / "mediroute-backend" / ".env")
url = os.environ["DATABASE_URL"]
engine = create_engine(url)

sql = """
INSERT INTO dispatch_zones (
    city_id, zone_code, zone_name,
    center_latitude, center_longitude, radius_km,
    is_active
)
VALUES (
    'HYD', 'HYD-BH-VERIFY', 'Alembic verify zone',
    17.4126, 78.4471, 8.0,
    TRUE
)
ON CONFLICT (zone_code) DO NOTHING
RETURNING zone_code, is_active;
"""

with engine.begin() as conn:
    exists = conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'dispatch_zones')"
        )
    ).scalar()
    print("dispatch_zones table exists:", exists)
    if not exists:
        raise SystemExit("SKIP: dispatch_zones not created yet — run migration first")

    row = conn.execute(text(sql)).fetchone()
    if row:
        print("INSERT OK:", row[0], "is_active=", row[1])
    else:
        print("INSERT skipped (conflict) — OK")

    conn.execute(text("DELETE FROM dispatch_zones WHERE zone_code = 'HYD-BH-VERIFY'"))
    print("cleanup done")

print("verification passed")
