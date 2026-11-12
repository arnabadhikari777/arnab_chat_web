"""site settings table (additive: one new table)

Revision ID: d2a9b4e6f1c7
Revises: c7e1a5d3f2b8
Create Date: 2026-11-11 20:00:00.000000

Rollback:  flask db downgrade c7e1a5d3f2b8   (drops only site_settings; every switch returns to its default)
"""
from alembic import op
import sqlalchemy as sa


revision = "d2a9b4e6f1c7"
down_revision = "c7e1a5d3f2b8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "site_settings",
        sa.Column("key", sa.String(length=60), primary_key=True),
        sa.Column("value", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )


def downgrade():
    op.drop_table("site_settings")
