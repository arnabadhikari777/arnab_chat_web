"""native_devices: Android app registrations (separate from browser Web Push)

Revision ID: d4f6a8c0e2b3
Revises: c3d5e7f9a1b2
Create Date: 2026-11-23 09:00:00.000000

Additive only: creates ONE new table. No existing table or row is touched. Safe on an existing database
and on a fresh one (skipped if the table already exists, e.g. after create_all in development).
Reversible: downgrade drops only this table.
"""
from alembic import op
import sqlalchemy as sa

revision = "d4f6a8c0e2b3"
down_revision = "c3d5e7f9a1b2"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    if "native_devices" in _tables():
        return
    op.create_table(
        "native_devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("push_token", sa.Text(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("os_version", sa.String(length=32), nullable=True),
        sa.Column("device_model", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "device_id", name="uq_native_device_install"),
        sa.UniqueConstraint("token_hash", name="uq_native_device_token"),
        sa.CheckConstraint("status in ('active','disabled','invalid')", name="ck_native_device_status"),
        sa.CheckConstraint("platform in ('android')", name="ck_native_device_platform"),
    )
    op.create_index("ix_native_devices_user_id", "native_devices", ["user_id"])
    op.create_index("ix_native_device_user_status", "native_devices", ["user_id", "status"])


def downgrade():
    if "native_devices" not in _tables():
        return
    op.drop_index("ix_native_device_user_status", table_name="native_devices")
    op.drop_index("ix_native_devices_user_id", table_name="native_devices")
    op.drop_table("native_devices")
