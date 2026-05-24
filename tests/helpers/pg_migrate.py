"""Postgres test DB reset via Alembic (migration-faithful path)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "mediroute-backend"


def is_postgres_url(db_url: str) -> bool:
    return db_url.startswith("postgresql")


def reset_postgres_schema(db_url: str) -> None:
    """Drop public schema and re-run Alembic upgrade head."""
    if not is_postgres_url(db_url):
        raise ValueError("reset_postgres_schema requires a postgresql URL")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))

    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )
