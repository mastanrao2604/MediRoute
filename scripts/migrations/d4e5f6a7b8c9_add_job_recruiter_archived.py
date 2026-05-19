"""Hide recruiter jobs via archive table (avoids ALTER jobs on Render).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-19
"""
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CREATE TABLE is fast; ALTER TABLE jobs timed out on Render Postgres.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS job_recruiter_archives (
            job_id INTEGER NOT NULL PRIMARY KEY
                REFERENCES jobs(id) ON DELETE CASCADE,
            archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_by_user_id INTEGER REFERENCES users(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_recruiter_archives_archived_at "
        "ON job_recruiter_archives (archived_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS job_recruiter_archives")
