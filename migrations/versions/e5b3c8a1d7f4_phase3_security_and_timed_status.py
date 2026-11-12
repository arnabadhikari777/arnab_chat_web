"""security events table + users.status_until (additive)

Revision ID: e5b3c8a1d7f4
Revises: d2a9b4e6f1c7
Create Date: 2026-11-12 09:00:00.000000

Rollback:  flask db downgrade d2a9b4e6f1c7
"""
from alembic import op
import sqlalchemy as sa


revision = "e5b3c8a1d7f4"
down_revision = "d2a9b4e6f1c7"
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
    op.add_column("users", sa.Column("status_until", sa.DateTime(), nullable=True))   # plain ALTER TABLE ADD COLUMN


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("status_until")
    op.drop_index("ix_security_kind_id", table_name="security_events")
    op.drop_table("security_events")
