"""Add performance indexes for job status, posted_by_user_id, and user role/verified

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-09

Bottlenecks fixed:
- get_jobs() does WHERE status='open' on every call — was full scan
- get_recruiter_jobs() does WHERE posted_by_user_id=X ORDER BY created_at — was full scan
- admin pending recruiters does WHERE role='recruiter' AND is_verified=false — was full scan
"""
from alembic import op


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # jobs.status — primary filter in every public job listing query
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_status
        ON jobs (status)
    """)

    # jobs.posted_by_user_id + created_at — recruiter dashboard query
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_posted_by
        ON jobs (posted_by_user_id, created_at DESC)
    """)

    # users.role + is_verified — admin pending recruiters query
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_role_verified
        ON users (role, is_verified)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_job_status")
    op.execute("DROP INDEX IF EXISTS idx_job_posted_by")
    op.execute("DROP INDEX IF EXISTS idx_users_role_verified")
