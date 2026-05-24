# MediRoute Operational Regression Framework

Pilot-blocking reliability suite. **One command:**

```powershell
./scripts/run-all-tests.ps1
```

Deployment must not proceed if this command exits non-zero.

## Architecture

```
tests/
  unit/           Pure Python helpers (no server)
  api/            HTTP integration — DB-authoritative lifecycle
  realtime/       WebSocket + reconnect (live test stack :8765)
  mobile/         App-kill / reconcile simulation (no FCM mock)
  stress/         100+ create/reconnect cycles
  migration/      Alembic chain + no runtime DDL in app/
  frontend/       Vitest — reconcile stale-state self-heal
  fixtures/       seed_manifest.json (generated)
  helpers/        api_client, ws_client, db_bootstrap, report
  reports/        junit + operational-report-*.md
```

## Principles

1. **DB lifecycle state is authoritative** — tests never require catching WebSocket events live.
2. **Isolated SQLite DB** at `tests/.data/test_mediroute.db` — never production Supabase.
3. **Deterministic seed** — fixed recruiter/nurse phones + lifecycle fixture shifts.
4. **Fail fast** — `pytest.ini` uses `-x` on first critical failure.
5. **Live stack** — uvicorn on port 8765 with `tests/.env.test`.

## Scripts

| Script | Purpose |
|--------|---------|
| `reset-test-db.ps1` | Drop/create schema + seed fixtures |
| `start-test-stack.ps1` | Uvicorn :8765 with test env |
| `stop-test-stack.ps1` | Kill test server |
| `run-all-tests.ps1` | Full suite + report |

## Environment

Optional overrides:

- `TEST_DATABASE_URL` — PostgreSQL for migration-faithful runs
- `STRESS_CREATE_COUNT` — default 100
- `STRESS_RECONNECT_COUNT` — default 100
- `TEST_BASE_URL` — default `http://127.0.0.1:8765`

## Suites (15)

| # | Suite | Location |
|---|--------|----------|
| 1 | Auth + roles | `api/test_auth_roles.py` |
| 2 | Shift creation | `api/test_shift_creation.py` |
| 3 | Recruiter dashboard | `api/test_recruiter_dashboard.py` |
| 4 | Apply flow | `api/test_apply_flow.py` |
| 5 | Recruiter confirm | `api/test_recruiter_confirm.py` |
| 6 | Check-in/out | `api/test_checkin_checkout.py` |
| 7 | No-show | `api/test_no_show.py` |
| 8 | Expiry/cancel/revoke | `api/test_expiry_cancel_revoke.py` |
| 9 | Reconnect recovery | `realtime/test_reconnect_recovery.py` |
| 10 | WebSocket stability | `realtime/test_websocket_stability.py` |
| 11 | Location | `api/test_location_reliability.py` |
| 12 | Notification recovery | `mobile/test_notification_recovery.py` |
| 13 | Migration | `migration/test_alembic_and_schema.py` |
| 14 | Stress | `stress/test_operational_stress.py` |
| 15 | E2E pilot flow | `api/test_e2e_pilot_flow.py` |

## CI / pre-deploy

```powershell
./scripts/run-all-tests.ps1
if ($LASTEXITCODE -ne 0) { exit 1 }
```

No bypass during pilot.
