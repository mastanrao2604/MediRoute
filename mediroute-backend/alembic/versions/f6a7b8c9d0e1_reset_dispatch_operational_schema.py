"""Reset dispatch operational schema — Alembic revision (copy to alembic/versions/ if folder locked).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-24

Pre-production controlled reset: drops dispatch transactional tables and recreates
clean schema matching app/models.py. Preserves users, profiles, auth, availability.
"""
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.execute("DROP TABLE IF EXISTS supply_demand_snapshots CASCADE")
  op.execute("DROP TABLE IF EXISTS shift_timeline_events CASCADE")
  op.execute("DROP TABLE IF EXISTS live_assignments CASCADE")
  op.execute("DROP TABLE IF EXISTS dispatch_offers CASCADE")
  op.execute("DROP TABLE IF EXISTS dispatch_sessions CASCADE")
  op.execute("DROP TABLE IF EXISTS shift_requests CASCADE")

  op.execute("DROP TYPE IF EXISTS assignmentstatus CASCADE")
  op.execute(
    "CREATE TYPE assignmentstatus AS ENUM ("
    "'applied', 'confirmed', 'checked_in', 'completed', 'no_show', 'cancelled'"
    ")"
  )

  op.execute("""
    CREATE TABLE shift_requests (
      id                  SERIAL PRIMARY KEY,
      city_id             VARCHAR(10) NOT NULL DEFAULT 'HYD',
      hospital_user_id    INTEGER NOT NULL REFERENCES users(id),
      role_required       userrole NOT NULL,
      specialty           VARCHAR,
      hospital_name       VARCHAR NOT NULL,
      hospital_latitude   DOUBLE PRECISION NOT NULL,
      hospital_longitude  DOUBLE PRECISION NOT NULL,
      hospital_pincode    VARCHAR(10),
      hospital_locality   VARCHAR(255),
      shift_start         TIMESTAMPTZ NOT NULL,
      shift_end           TIMESTAMPTZ,
      status              shiftrequeststatuss NOT NULL DEFAULT 'open',
      urgency             shifturgency NOT NULL DEFAULT 'standard',
      pay_rate            VARCHAR,
      notes               TEXT,
      idempotency_key     TEXT UNIQUE,
      dispatch_radius_km  DOUBLE PRECISION NOT NULL DEFAULT 10.0,
      nurses_required     INTEGER NOT NULL DEFAULT 1,
      search_closed_at    TIMESTAMPTZ,
      filled_at           TIMESTAMPTZ,
      created_at          TIMESTAMPTZ DEFAULT NOW()
    )
  """)
  op.execute("CREATE INDEX idx_shift_city_status ON shift_requests (city_id, status)")
  op.execute("CREATE INDEX idx_shift_hospital_user ON shift_requests (hospital_user_id, created_at DESC)")
  op.execute("CREATE INDEX idx_shift_idempotency ON shift_requests (idempotency_key)")
  op.execute("CREATE INDEX idx_shift_status_created ON shift_requests (status, created_at DESC)")

  op.execute("""
    CREATE TABLE dispatch_sessions (
      id                SERIAL PRIMARY KEY,
      shift_request_id  INTEGER NOT NULL UNIQUE REFERENCES shift_requests(id),
      status            dispatchsessionstatus NOT NULL DEFAULT 'active',
      current_wave      INTEGER NOT NULL DEFAULT 1,
      waves_exhausted   BOOLEAN NOT NULL DEFAULT FALSE,
      started_at        TIMESTAMPTZ DEFAULT NOW(),
      completed_at      TIMESTAMPTZ
    )
  """)
  op.execute("CREATE INDEX idx_dsession_shift ON dispatch_sessions (shift_request_id)")
  op.execute("CREATE INDEX idx_dsession_status ON dispatch_sessions (status)")

  op.execute("""
    CREATE TABLE dispatch_offers (
      id                SERIAL PRIMARY KEY,
      session_id        INTEGER NOT NULL REFERENCES dispatch_sessions(id),
      shift_request_id  INTEGER NOT NULL REFERENCES shift_requests(id),
      nurse_user_id     INTEGER NOT NULL REFERENCES users(id),
      status            offerstatus NOT NULL DEFAULT 'pending',
      wave_number       INTEGER NOT NULL DEFAULT 1,
      offered_at        TIMESTAMPTZ DEFAULT NOW(),
      expires_at        TIMESTAMPTZ NOT NULL,
      responded_at      TIMESTAMPTZ,
      delivery_method   offerdeliverymethod NOT NULL DEFAULT 'websocket'
    )
  """)
  op.execute("CREATE INDEX idx_offer_nurse_pending ON dispatch_offers (nurse_user_id, status)")
  op.execute("CREATE INDEX idx_offer_session ON dispatch_offers (session_id)")
  op.execute("CREATE INDEX idx_offer_shift ON dispatch_offers (shift_request_id)")
  op.execute("CREATE INDEX idx_offer_expires_status ON dispatch_offers (expires_at, status)")

  op.execute("""
    CREATE TABLE live_assignments (
      id                      SERIAL PRIMARY KEY,
      shift_request_id        INTEGER NOT NULL REFERENCES shift_requests(id),
      nurse_user_id           INTEGER NOT NULL REFERENCES users(id),
      offer_id                INTEGER NOT NULL REFERENCES dispatch_offers(id),
      status                  assignmentstatus NOT NULL DEFAULT 'applied',
      confirmed_at            TIMESTAMPTZ DEFAULT NOW(),
      recruiter_confirmed_at  TIMESTAMPTZ,
      check_in_at             TIMESTAMPTZ,
      check_out_at            TIMESTAMPTZ,
      check_in_latitude       DOUBLE PRECISION,
      check_in_longitude      DOUBLE PRECISION,
      CONSTRAINT uq_assignment_shift_nurse UNIQUE (shift_request_id, nurse_user_id)
    )
  """)
  op.execute("CREATE INDEX idx_assignment_nurse_status ON live_assignments (nurse_user_id, status)")
  op.execute("CREATE INDEX idx_assignment_shift ON live_assignments (shift_request_id)")

  op.execute("""
    CREATE TABLE shift_timeline_events (
      id                SERIAL PRIMARY KEY,
      shift_request_id  INTEGER NOT NULL REFERENCES shift_requests(id),
      event_type        VARCHAR(64) NOT NULL,
      actor_user_id     INTEGER,
      city_id           VARCHAR(10) NOT NULL DEFAULT 'HYD',
      payload           JSONB,
      occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
  """)
  op.execute("CREATE INDEX idx_timeline_shift_time ON shift_timeline_events (shift_request_id, occurred_at)")
  op.execute("CREATE INDEX idx_timeline_city_type ON shift_timeline_events (city_id, event_type)")

  op.execute("""
    UPDATE reliability_scores SET
      score = 100.0, total_offers = 0, accepted = 0, declined = 0,
      timed_out = 0, no_shows = 0, completed_shifts = 0, last_calculated_at = NULL
  """)


def downgrade() -> None:
  pass
