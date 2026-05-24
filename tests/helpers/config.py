"""Test harness configuration — isolated from production."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "mediroute-backend"
TEST_DATA = ROOT / "tests" / ".data"
FIXTURES = ROOT / "tests" / "fixtures"
REPORTS = ROOT / "tests" / "reports"
MANIFEST_PATH = FIXTURES / "seed_manifest.json"
ENV_TEST_PATH = ROOT / "tests" / ".env.test"
STACK_PID_PATH = TEST_DATA / "stack.pid"
STACK_LOG_PATH = TEST_DATA / "stack.log"

DEFAULT_TEST_PORT = int(os.getenv("TEST_API_PORT", "8765"))
DEFAULT_BASE_URL = os.getenv("TEST_BASE_URL", f"http://127.0.0.1:{DEFAULT_TEST_PORT}")

# Isolated SQLite — NEVER production Supabase
DEFAULT_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    f"sqlite:///{(TEST_DATA / 'test_mediroute.db').as_posix()}",
)

STRESS_CREATE_COUNT = int(os.getenv("STRESS_CREATE_COUNT", "100"))
STRESS_RECONNECT_COUNT = int(os.getenv("STRESS_RECONNECT_COUNT", "100"))

# Fixed test identities (seeded by db_bootstrap)
RECRUITER_PHONE = "919999900001"
NURSE_PHONE = "919999900002"
TEST_SECRET = "mediroute-test-secret-key-do-not-use-in-production"

# Hyderabad pilot coordinates
HOSP_LAT = 17.4126
HOSP_LNG = 78.4471
HOSP_PIN = "500072"
CITY_ID = "HYD"
