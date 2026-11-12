"""security events table (additive)

Revision ID: f1a2b3c4d5e6
Revises: d3b8f2a6c1e4
Create Date: 2026-11-12 20:00:00.000000

Adds security_events for the admin security log. No columns are dropped.
"""
from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "d3b8f2a6c1e4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "security_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("ip", sa.String(length=45)),
        sa.Column("detail", sa.String(length=120)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_security_kind_id", "security_events", ["kind", "id"])


def downgrade():
    op.drop_index("ix_security_kind_id", table_name="security_events")
    op.drop_table("security_events")
