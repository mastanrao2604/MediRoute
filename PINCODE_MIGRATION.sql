-- Idempotent Postgres DDL for pilot DBs (pincode matching / profiles).
ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS hospital_pincode VARCHAR(10);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS service_pincode VARCHAR(10);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS service_locality VARCHAR(255);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS location_source VARCHAR(32);
ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS hospital_locality VARCHAR(255);
-- Ongoing staff search (multi-nurse + manual stop)
ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS nurses_required INTEGER NOT NULL DEFAULT 1;
ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS search_closed_at TIMESTAMPTZ;
ALTER TABLE live_assignments DROP CONSTRAINT IF EXISTS live_assignments_shift_request_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_assignment_shift_nurse ON live_assignments (shift_request_id, nurse_user_id);
