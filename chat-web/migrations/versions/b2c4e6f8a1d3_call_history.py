"""add call_history table for voice/video calling

Revision ID: b2c4e6f8a1d3
Revises: a9f0c3d7b1e2
Create Date: 2026-11-22 12:00:00.000000

Additive only. Safe on existing databases.
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c4e6f8a1d3"
down_revision = "a9f0c3d7b1e2"
branch_labels = None
depends_on = None


def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name):
    return name in _insp().get_table_names()


def upgrade():
    if _has_table("call_history"):
        return

    op.create_table(
        "call_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("call_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("caller_id", sa.Integer(), nullable=False),
        sa.Column("receiver_id", sa.Integer(), nullable=False),
        sa.Column("call_type", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="initiated"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["caller_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["receiver_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("call_type in ('voice','video')", name="ck_call_type"),
        sa.CheckConstraint(
            "status in ('initiated','ringing','connected','completed','missed','rejected','busy','failed','cancelled')",
            name="ck_call_status",
        ),
    )
    op.create_index("ix_call_history_call_id", "call_history", ["call_id"], unique=True)
    op.create_index("ix_call_history_conversation_id", "call_history", ["conversation_id"])
    op.create_index("ix_call_history_caller_id", "call_history", ["caller_id"])
    op.create_index("ix_call_history_receiver_id", "call_history", ["receiver_id"])
    op.create_index("ix_call_history_pair", "call_history", ["caller_id", "receiver_id"])
    op.create_index("ix_call_history_started", "call_history", ["started_at"])


def downgrade():
    if _has_table("call_history"):
        op.drop_table("call_history")
