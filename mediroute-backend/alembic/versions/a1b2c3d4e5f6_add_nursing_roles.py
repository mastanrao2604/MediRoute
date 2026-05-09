"""add nursing roles to userrole enum

Revision ID: a1b2c3d4e5f6
Revises: 0963c94ecc13
Create Date: 2026-05-09 18:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = 'a1b2c3d4e5f6'
down_revision = '0963c94ecc13'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL requires ALTER TYPE ... ADD VALUE for enum extensions.
    # IF NOT EXISTS prevents errors on re-runs / multiple deploys.
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'staff_nurse'")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'icu_nurse'")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'ot_nurse'")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'emergency_nurse'")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'home_care_nurse'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    # A downgrade would require a full type recreation — skip to avoid data risk.
    pass
