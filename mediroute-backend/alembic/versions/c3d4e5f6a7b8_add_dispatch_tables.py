"""Add real-time dispatch tables for Phase 1 staffing engine

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-11

New tables:
  nurse_availability       — availability toggle + last-known location
  presence_state           — fine-grained online state (offline/available/busy/background)
  device_tokens            — FCM tokens, one per user per platform
  shift_requests           — hospital shift demand with urgency + idempotency_key
  dispatch_sessions        — one dispatch run per shift (wave tracking)
  dispatch_offers          — individual nurse offers within a dispatch session
  live_assignments         — confirmed nurse ↔ shift assignment (post-accept)
  reliability_scores       — nurse reliability score for dispatch ranking
  shift_timeline_events    — immutable audit log (sacred infrastructure)
  dispatch_zones           — hyperlocal zones (Density > Geography principle)
  supply_demand_snapshots  — FUTURE: zone stress tracking placeholder

IMPORTANT — PostGIS:
  This migration does NOT enable PostGIS.
  Phase 1 uses Float lat/lng + Python Haversine for dispatch geo queries.
  To enable PostGIS for future ST_DWithin queries:
    Supabase Dashboard → Database → Extensions → postgis → Enable
  Do NOT run 'CREATE EXTENSION postgis' via Alembic — requires superuser.

All tables are additive — no existing table is modified.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enums (PostgreSQL requires explicit enum type creation) ──────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE presencestateenum AS ENUM
                ('offline', 'online_available', 'online_busy', 'background');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE deviceplatform AS ENUM ('android', 'ios', 'web');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE shifturgency AS ENUM
                ('emergency', 'urgent', 'standard', 'planned');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE shiftrequeststatuss AS ENUM
                ('open', 'dispatching', 'filled', 'expired', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE dispatchsessionstatus AS ENUM
                ('active', 'completed', 'failed', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE offerstatus AS ENUM
                ('pending', 'accepted', 'declined', 'timed_out', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE offerdeliverymethod AS ENUM ('websocket', 'fcm', 'both');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE assignmentstatus AS ENUM
                ('confirmed', 'checked_in', 'completed', 'no_show', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ── nurse_availability ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS nurse_availability (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            is_available BOOLEAN NOT NULL DEFAULT FALSE,
            latitude    DOUBLE PRECISION,
            longitude   DOUBLE PRECISION,
            city_id     VARCHAR(10) NOT NULL DEFAULT 'HYD',
            last_seen   TIMESTAMPTZ,
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_avail_city_available ON nurse_availability (city_id, is_available)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_avail_last_seen ON nurse_availability (last_seen)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_avail_user ON nurse_availability (user_id)")

    # ── presence_state ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS presence_state (
            id                    SERIAL PRIMARY KEY,
            user_id               INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            state                 presencestateenum NOT NULL DEFAULT 'offline',
            latitude              DOUBLE PRECISION,
            longitude             DOUBLE PRECISION,
            city_id               VARCHAR(10) NOT NULL DEFAULT 'HYD',
            last_heartbeat        TIMESTAMPTZ,
            last_location_at      TIMESTAMPTZ,
            historical_preferences JSONB,
            preferred_shift_types  JSONB,
            preferred_radius_km    DOUBLE PRECISION
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_presence_city_state ON presence_state (city_id, state)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_presence_heartbeat ON presence_state (last_heartbeat)")

    # ── device_tokens ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS device_tokens (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            fcm_token  TEXT NOT NULL,
            platform   deviceplatform NOT NULL DEFAULT 'android',
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (user_id, platform)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_device_tokens_user ON device_tokens (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_device_tokens_fcm ON device_tokens (fcm_token)")

    # ── shift_requests ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS shift_requests (
            id                  SERIAL PRIMARY KEY,
            city_id             VARCHAR(10) NOT NULL DEFAULT 'HYD',
            hospital_user_id    INTEGER NOT NULL REFERENCES users(id),
            role_required       userrole NOT NULL,
            specialty           VARCHAR,
            hospital_name       VARCHAR NOT NULL,
            hospital_latitude   DOUBLE PRECISION NOT NULL,
            hospital_longitude  DOUBLE PRECISION NOT NULL,
            shift_start         TIMESTAMPTZ NOT NULL,
            shift_end           TIMESTAMPTZ,
            status              shiftrequeststatuss NOT NULL DEFAULT 'open',
            urgency             shifturgency NOT NULL DEFAULT 'standard',
            pay_rate            VARCHAR,
            notes               TEXT,
            idempotency_key     TEXT UNIQUE,
            dispatch_radius_km  DOUBLE PRECISION NOT NULL DEFAULT 10.0,
            filled_at           TIMESTAMPTZ,
            created_at          TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_shift_city_status ON shift_requests (city_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_shift_hospital_user ON shift_requests (hospital_user_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_shift_idempotency ON shift_requests (idempotency_key)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_shift_status_created ON shift_requests (status, created_at DESC)")

    # ── dispatch_sessions ────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS dispatch_sessions (
            id                SERIAL PRIMARY KEY,
            shift_request_id  INTEGER NOT NULL UNIQUE REFERENCES shift_requests(id),
            status            dispatchsessionstatus NOT NULL DEFAULT 'active',
            current_wave      INTEGER NOT NULL DEFAULT 1,
            waves_exhausted   BOOLEAN NOT NULL DEFAULT FALSE,
            started_at        TIMESTAMPTZ DEFAULT NOW(),
            completed_at      TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_dsession_shift ON dispatch_sessions (shift_request_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_dsession_status ON dispatch_sessions (status)")

    # ── dispatch_offers ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS dispatch_offers (
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
    op.execute("CREATE INDEX IF NOT EXISTS idx_offer_nurse_pending ON dispatch_offers (nurse_user_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_offer_session ON dispatch_offers (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_offer_shift ON dispatch_offers (shift_request_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_offer_expires_status ON dispatch_offers (expires_at, status)")

    # ── live_assignments ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS live_assignments (
            id                  SERIAL PRIMARY KEY,
            shift_request_id    INTEGER NOT NULL UNIQUE REFERENCES shift_requests(id),
            nurse_user_id       INTEGER NOT NULL REFERENCES users(id),
            offer_id            INTEGER NOT NULL REFERENCES dispatch_offers(id),
            status              assignmentstatus NOT NULL DEFAULT 'confirmed',
            confirmed_at        TIMESTAMPTZ DEFAULT NOW(),
            check_in_at         TIMESTAMPTZ,
            check_out_at        TIMESTAMPTZ,
            check_in_latitude   DOUBLE PRECISION,
            check_in_longitude  DOUBLE PRECISION
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_assignment_nurse_status ON live_assignments (nurse_user_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_assignment_shift ON live_assignments (shift_request_id)")

    # ── reliability_scores ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS reliability_scores (
            id                   SERIAL PRIMARY KEY,
            user_id              INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            score                DOUBLE PRECISION NOT NULL DEFAULT 100.0,
            total_offers         INTEGER NOT NULL DEFAULT 0,
            accepted             INTEGER NOT NULL DEFAULT 0,
            declined             INTEGER NOT NULL DEFAULT 0,
            timed_out            INTEGER NOT NULL DEFAULT 0,
            no_shows             INTEGER NOT NULL DEFAULT 0,
            completed_shifts     INTEGER NOT NULL DEFAULT 0,
            last_calculated_at   TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_reliability_user ON reliability_scores (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reliability_score ON reliability_scores (score DESC)")

    # ── shift_timeline_events ────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS shift_timeline_events (
            id                SERIAL PRIMARY KEY,
            shift_request_id  INTEGER NOT NULL REFERENCES shift_requests(id),
            event_type        VARCHAR(64) NOT NULL,
            actor_user_id     INTEGER,
            city_id           VARCHAR(10) NOT NULL DEFAULT 'HYD',
            payload           JSONB,
            occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_timeline_shift_time ON shift_timeline_events (shift_request_id, occurred_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_timeline_city_type ON shift_timeline_events (city_id, event_type)")

    # ── dispatch_zones ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS dispatch_zones (
            id                SERIAL PRIMARY KEY,
            city_id           VARCHAR(10) NOT NULL,
            zone_code         VARCHAR(20) NOT NULL UNIQUE,
            zone_name         VARCHAR NOT NULL,
            center_latitude   DOUBLE PRECISION NOT NULL,
            center_longitude  DOUBLE PRECISION NOT NULL,
            radius_km         DOUBLE PRECISION NOT NULL DEFAULT 10.0,
            is_active         BOOLEAN NOT NULL DEFAULT TRUE,
            dispatch_paused   BOOLEAN NOT NULL DEFAULT FALSE,
            max_radius_km     DOUBLE PRECISION,
            created_at        TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_zone_city_active ON dispatch_zones (city_id, is_active)")

    # ── supply_demand_snapshots (FUTURE hook — §24.2) ─────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS supply_demand_snapshots (
            id                SERIAL PRIMARY KEY,
            zone_code         VARCHAR(20) NOT NULL,
            city_id           VARCHAR(10) NOT NULL,
            snapshot_at       TIMESTAMPTZ NOT NULL,
            online_nurses     INTEGER NOT NULL DEFAULT 0,
            pending_shifts    INTEGER NOT NULL DEFAULT 0,
            avg_fill_time_sec DOUBLE PRECISION
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_sds_zone_time ON supply_demand_snapshots (zone_code, snapshot_at)")

    # ── Seed default Hyderabad zone for launch ───────────────────────────────
    op.execute("""
        INSERT INTO dispatch_zones (
            city_id, zone_code, zone_name,
            center_latitude, center_longitude, radius_km,
            is_active, dispatch_paused
        )
        VALUES (
            'HYD', 'HYD-BH', 'Banjara Hills / Jubilee Hills',
            17.4126, 78.4471, 8.0,
            TRUE, FALSE
        )
        ON CONFLICT (zone_code) DO NOTHING
    """)


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.execute("DROP TABLE IF EXISTS supply_demand_snapshots")
    op.execute("DROP TABLE IF EXISTS dispatch_zones")
    op.execute("DROP TABLE IF EXISTS shift_timeline_events")
    op.execute("DROP TABLE IF EXISTS reliability_scores")
    op.execute("DROP TABLE IF EXISTS live_assignments")
    op.execute("DROP TABLE IF EXISTS dispatch_offers")
    op.execute("DROP TABLE IF EXISTS dispatch_sessions")
    op.execute("DROP TABLE IF EXISTS shift_requests")
    op.execute("DROP TABLE IF EXISTS device_tokens")
    op.execute("DROP TABLE IF EXISTS presence_state")
    op.execute("DROP TABLE IF EXISTS nurse_availability")
    op.execute("DROP TYPE IF EXISTS assignmentstatus")
    op.execute("DROP TYPE IF EXISTS offerdeliverymethod")
    op.execute("DROP TYPE IF EXISTS offerstatus")
    op.execute("DROP TYPE IF EXISTS dispatchsessionstatus")
    op.execute("DROP TYPE IF EXISTS shiftrequeststatuss")
    op.execute("DROP TYPE IF EXISTS shifturgency")
    op.execute("DROP TYPE IF EXISTS deviceplatform")
    op.execute("DROP TYPE IF EXISTS presencestateenum")
