-- Idempotent Postgres DDL for pilot DBs (pincode matching / profiles).
ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS hospital_pincode VARCHAR(10);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS service_pincode VARCHAR(10);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS service_locality VARCHAR(255);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS location_source VARCHAR(32);
ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS hospital_locality VARCHAR(255);
