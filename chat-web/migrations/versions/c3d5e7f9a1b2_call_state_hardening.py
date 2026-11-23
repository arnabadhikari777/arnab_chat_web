"""call_history: ringing/accepted timestamps, who ended the call and why

Revision ID: c3d5e7f9a1b2
Revises: b2c4e6f8a1d3
Create Date: 2026-11-22 18:00:00.000000

Additive and non-destructive: four NULLable columns. Existing call records are untouched. Safe on an
existing database and on a fresh one (idempotent: columns that already exist are skipped).
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d5e7f9a1b2"
down_revision = "b2c4e6f8a1d3"
branch_labels = None
depends_on = None


def _cols():
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("call_history")}


def upgrade():
    have = _cols()
    with op.batch_alter_table("call_history") as batch:
        if "ringing_at" not in have:
            batch.add_column(sa.Column("ringing_at", sa.DateTime(), nullable=True))
        if "accepted_at" not in have:
            batch.add_column(sa.Column("accepted_at", sa.DateTime(), nullable=True))
        if "ended_by_id" not in have:
            batch.add_column(sa.Column("ended_by_id", sa.Integer(), nullable=True))
        if "end_reason" not in have:
            batch.add_column(sa.Column("end_reason", sa.String(length=30), nullable=True))


def downgrade():
    have = _cols()
    with op.batch_alter_table("call_history") as batch:
        for name in ("end_reason", "ended_by_id", "accepted_at", "ringing_at"):
            if name in have:
                batch.drop_column(name)
