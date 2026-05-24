-- Controlled pre-production reset: dispatch transactional data ONLY.
-- PRESERVES: users, profiles, auth, device_tokens, nurse_availability, presence_state, jobs.
-- Manual fallback if Alembic migration f6 cannot run on Render.

BEGIN;

DROP TABLE IF EXISTS supply_demand_snapshots CASCADE;
DROP TABLE IF EXISTS shift_timeline_events CASCADE;
DROP TABLE IF EXISTS live_assignments CASCADE;
DROP TABLE IF EXISTS dispatch_offers CASCADE;
DROP TABLE IF EXISTS dispatch_sessions CASCADE;
DROP TABLE IF EXISTS shift_requests CASCADE;

DROP TYPE IF EXISTS assignmentstatus CASCADE;
CREATE TYPE assignmentstatus AS ENUM (
    'applied', 'confirmed', 'checked_in', 'completed', 'no_show', 'cancelled'
);

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
);
CREATE INDEX idx_shift_city_status ON shift_requests (city_id, status);
CREATE INDEX idx_shift_hospital_user ON shift_requests (hospital_user_id, created_at DESC);
CREATE INDEX idx_shift_idempotency ON shift_requests (idempotency_key);
CREATE INDEX idx_shift_status_created ON shift_requests (status, created_at DESC);

CREATE TABLE dispatch_sessions (
    id                SERIAL PRIMARY KEY,
    shift_request_id  INTEGER NOT NULL UNIQUE REFERENCES shift_requests(id),
    status            dispatchsessionstatus NOT NULL DEFAULT 'active',
    current_wave      INTEGER NOT NULL DEFAULT 1,
    waves_exhausted   BOOLEAN NOT NULL DEFAULT FALSE,
    started_at        TIMESTAMPTZ DEFAULT NOW(),
    completed_at      TIMESTAMPTZ
);
CREATE INDEX idx_dsession_shift ON dispatch_sessions (shift_request_id);
CREATE INDEX idx_dsession_status ON dispatch_sessions (status);

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
);
CREATE INDEX idx_offer_nurse_pending ON dispatch_offers (nurse_user_id, status);
CREATE INDEX idx_offer_session ON dispatch_offers (session_id);
CREATE INDEX idx_offer_shift ON dispatch_offers (shift_request_id);
CREATE INDEX idx_offer_expires_status ON dispatch_offers (expires_at, status);

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
);
CREATE INDEX idx_assignment_nurse_status ON live_assignments (nurse_user_id, status);
CREATE INDEX idx_assignment_shift ON live_assignments (shift_request_id);

CREATE TABLE shift_timeline_events (
    id                SERIAL PRIMARY KEY,
    shift_request_id  INTEGER NOT NULL REFERENCES shift_requests(id),
    event_type        VARCHAR(64) NOT NULL,
    actor_user_id     INTEGER,
    city_id           VARCHAR(10) NOT NULL DEFAULT 'HYD',
    payload           JSONB,
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_timeline_shift_time ON shift_timeline_events (shift_request_id, occurred_at);
CREATE INDEX idx_timeline_city_type ON shift_timeline_events (city_id, event_type);

UPDATE reliability_scores SET
    score = 100.0, total_offers = 0, accepted = 0, declined = 0,
    timed_out = 0, no_shows = 0, completed_shifts = 0, last_calculated_at = NULL;

COMMIT;
