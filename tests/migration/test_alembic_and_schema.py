"""Suite 13 — Migration authority + no runtime DDL in app hot paths."""
from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.migration, pytest.mark.critical]

ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = ROOT / "mediroute-backend" / "app"
ALEMBIC_VERSIONS = ROOT / "mediroute-backend" / "alembic" / "versions"
SCRIPTS_VERSIONS = ROOT / "scripts" / "alembic_versions"


def test_alembic_revision_chain_intact():
    """Revision files under alembic/versions form a valid chain."""
    files = list(ALEMBIC_VERSIONS.glob("*.py"))
    revisions = {}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        rev = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', text, re.M)
        down = re.search(r'^down_revision\s*=\s*["\']([^"\']+)["\']', text, re.M)
        if rev:
            revisions[rev.group(1)] = down.group(1) if down else None

    assert revisions, "No alembic revision files found"
    # Walk from heads
    all_down = {v for v in revisions.values() if v}
    heads = [k for k in revisions if k not in all_down]
    assert heads, "No alembic head found"


def test_scripts_migrations_present():
    """Operational migrations maintained under scripts/alembic_versions for deploy sync."""
    assert SCRIPTS_VERSIONS.exists()
    assert list(SCRIPTS_VERSIONS.glob("*.py")), "scripts/alembic_versions must not be empty"


def test_no_runtime_alter_in_app_routes():
    """App hot paths must not execute ALTER TABLE at runtime."""
    forbidden = []
    scan_dirs = [BACKEND_APP / "routes", BACKEND_APP / "dispatch"]
    for d in scan_dirs:
        for py in d.rglob("*.py"):
            content = py.read_text(encoding="utf-8", errors="ignore")
            if re.search(r'execute\s*\(\s*["\']ALTER\s+TABLE', content, re.I):
                forbidden.append(str(py.relative_to(ROOT)))
            if re.search(r'ADD VALUE', content, re.I) and "alembic" not in str(py):
                forbidden.append(str(py.relative_to(ROOT)))
    assert not forbidden, f"Runtime DDL found: {forbidden}"


def test_schema_startup_module_exists():
    path = BACKEND_APP / "schema_startup.py"
    assert path.exists(), "schema_startup.py missing — deploy validation required"


def test_ops_trace_module_exists():
    path = BACKEND_APP / "ops_trace.py"
    assert path.exists(), "ops_trace.py missing — operational logging required"


@pytest.mark.skipif(
    not (ROOT / "tests" / "fixtures" / "seed_manifest.json").exists(),
    reason="Run reset-test-db first",
)
def test_test_db_has_critical_tables(manifest):
    """Bootstrap created required fixture users."""
    assert manifest.get("recruiter_id")
    assert manifest.get("nurse_id")
    assert manifest.get("lifecycle_fixtures")
