"""security events table (additive)

Revision ID: f1a2b3c4d5e6
Revises: e5b3c8a1d7f4
Create Date: 2026-11-12 20:00:00.000000

Adds security_events for the admin security log. No columns are dropped.
Repaired: skips what already exists (e5b3c8a1d7f4 may have created it on a database from the other branch).
"""
from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e5b3c8a1d7f4"
branch_labels = None
depends_on = None


# ---- schema guards: every step here is safe to run on a database that already has the change ----
def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name):
    return name in _insp().get_table_names()


def _has_column(table, column):
    return _has_table(table) and column in {c["name"] for c in _insp().get_columns(table)}


def _has_index(table, index):
    return _has_table(table) and index in {i["name"] for i in _insp().get_indexes(table)}


def upgrade():
    if not _has_table("security_events"):
        op.create_table(
            "security_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("kind", sa.String(length=30), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("ip", sa.String(length=45)),
            sa.Column("detail", sa.String(length=120)),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if not _has_index("security_events", "ix_security_kind_id"):
        op.create_index("ix_security_kind_id", "security_events", ["kind", "id"])


def downgrade():
    if _has_index("security_events", "ix_security_kind_id"):
        op.drop_index("ix_security_kind_id", table_name="security_events")
    if _has_table("security_events"):
        op.drop_table("security_events")
