"""security events table (the second branch's phase-3 step, now a safe guard)

Revision ID: e5b3c8a1d7f4
Revises: d3b8f2a6c1e4
Create Date: 2026-11-12 09:00:00.000000

HISTORY: this revision used to branch from d2a9b4e6f1c7. It created security_events (also created by
f1a2b3c4d5e6) and added users.status_until. The application's timed suspension uses users.suspended_until
(added by c9a1e5f7b2d3), so status_until is not part of the schema any more and is NOT added here.
A database that already has users.status_until keeps it: it is nullable, unused and never dropped.
"""
from alembic import op
import sqlalchemy as sa


revision = "e5b3c8a1d7f4"
down_revision = "d3b8f2a6c1e4"
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
    # security_events is owned by f1a2b3c4d5e6 in the repaired history; nothing to undo here.
    pass
