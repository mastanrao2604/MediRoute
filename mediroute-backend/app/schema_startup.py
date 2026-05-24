"""
Startup schema validation — Alembic is the only schema authority.

No create_all, no runtime ALTER TABLE, no enum patching at startup.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .ops_trace import startup_trace

logger = logging.getLogger("uvicorn.error")

# Tables required for dispatch pilot operations
CRITICAL_TABLES = (
    "users",
    "profiles",
    "shift_requests",
    "dispatch_sessions",
    "dispatch_offers",
    "live_assignments",
    "reliability_scores",
)

# Columns that must exist after f6 reset migration chain
CRITICAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "live_assignments": ("recruiter_confirmed_at",),
    "shift_requests": ("nurses_required", "search_closed_at", "hospital_pincode"),
}


def validate_schema_on_startup(engine: Engine) -> dict[str, Any]:
    """
    Read-only validation. Returns a result dict; never mutates schema.
    Logs structured startup.schema events for Render deploy visibility.
    """
    result: dict[str, Any] = {
        "ok": True,
        "alembic_revision": None,
        "missing_tables": [],
        "missing_columns": [],
        "db_reachable": False,
    }

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            result["db_reachable"] = True

            rev = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
            result["alembic_revision"] = rev

            for table in CRITICAL_TABLES:
                exists = conn.execute(
                    text(
                        "SELECT EXISTS ("
                        "  SELECT 1 FROM information_schema.tables "
                        "  WHERE table_schema = 'public' AND table_name = :t"
                        ")"
                    ),
                    {"t": table},
                ).scalar()
                if not exists:
                    result["missing_tables"].append(table)

            for table, columns in CRITICAL_COLUMNS.items():
                if table in result["missing_tables"]:
                    continue
                for col in columns:
                    exists = conn.execute(
                        text(
                            "SELECT EXISTS ("
                            "  SELECT 1 FROM information_schema.columns "
                            "  WHERE table_schema = 'public' "
                            "    AND table_name = :t AND column_name = :c"
                            ")"
                        ),
                        {"t": table, "c": col},
                    ).scalar()
                    if not exists:
                        result["missing_columns"].append(f"{table}.{col}")

    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:200]
        startup_trace(
            "validation_failed",
            level="error",
            reason="db_unreachable",
            err=str(exc)[:120],
        )
        return result

    if result["missing_tables"] or result["missing_columns"]:
        result["ok"] = False
        startup_trace(
            "validation_failed",
            level="error",
            alembic=result["alembic_revision"],
            missing_tables=result["missing_tables"] or None,
            missing_columns=result["missing_columns"] or None,
        )
    else:
        startup_trace(
            "validation_ok",
            alembic=result["alembic_revision"],
            tables=len(CRITICAL_TABLES),
        )

    return result
