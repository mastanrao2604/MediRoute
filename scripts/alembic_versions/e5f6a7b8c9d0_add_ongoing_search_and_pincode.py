"""Pincode fields + multi-applicant assignment schema.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS hospital_pincode VARCHAR(10)")
    op.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS service_pincode VARCHAR(10)")
    op.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS service_locality VARCHAR(255)")
    op.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS location_source VARCHAR(32)")
    op.execute("ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS hospital_locality VARCHAR(255)")
    op.execute("ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS nurses_required INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE shift_requests ADD COLUMN IF NOT EXISTS search_closed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE live_assignments DROP CONSTRAINT IF EXISTS live_assignments_shift_request_id_key")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_assignment_shift_nurse "
        "ON live_assignments (shift_request_id, nurse_user_id)"
    )
    op.execute(
        "ALTER TABLE live_assignments "
        "ADD COLUMN IF NOT EXISTS recruiter_confirmed_at TIMESTAMPTZ"
    )
    with op.get_context().autocommit_block():
        op.execute("""
            DO $$ BEGIN
                ALTER TYPE assignmentstatus ADD VALUE 'applied';
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_assignment_shift_nurse")
    op.execute("ALTER TABLE live_assignments DROP COLUMN IF EXISTS recruiter_confirmed_at")
    op.execute("ALTER TABLE shift_requests DROP COLUMN IF EXISTS search_closed_at")
    op.execute("ALTER TABLE shift_requests DROP COLUMN IF EXISTS nurses_required")
    op.execute("ALTER TABLE shift_requests DROP COLUMN IF EXISTS hospital_locality")
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS location_source")
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS service_locality")
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS service_pincode")
    op.execute("ALTER TABLE shift_requests DROP COLUMN IF EXISTS hospital_pincode")
