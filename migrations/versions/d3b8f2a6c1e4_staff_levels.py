"""staff levels: users.admin_role

Revision ID: d3b8f2a6c1e4
Revises: c9a1e5f7b2d3
Create Date: 2026-11-12 18:00:00.000000

Nullable column. Existing admins keep full access (a missing level counts as super admin and is
filled in at their next login from ADMIN_EMAILS). Reversible.
"""
from alembic import op
import sqlalchemy as sa

revision = "d3b8f2a6c1e4"
down_revision = "c9a1e5f7b2d3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("admin_role", sa.String(20), nullable=True))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("admin_role")
