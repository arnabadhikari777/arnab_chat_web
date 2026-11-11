"""push subscriptions and delivery tracking

Revision ID: a4f8c2e1b9d0
Revises: 9c1d2e3f4a5b
Create Date: 2026-11-11 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "a4f8c2e1b9d0"
down_revision = "9c1d2e3f4a5b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=200), nullable=False),
        sa.Column("auth", sa.String(length=100), nullable=False),
        sa.Column("user_agent", sa.String(length=300)),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("last_attempt_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("endpoint", name="uq_push_endpoint"),
        sa.CheckConstraint("status in ('active','expired','failed')", name="ck_push_status"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])
    op.create_index("ix_push_user_status", "push_subscriptions", ["user_id", "status"])

    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notification_id", sa.Integer(), sa.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("push_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=300)),
        sa.Column("accepted_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("notification_id", "subscription_id", name="uq_push_delivery_pair"),
        sa.CheckConstraint(
            "status in ('pending','accepted','failed','expired')",
            name="ck_push_delivery_status",
        ),
    )
    op.create_index("ix_push_deliveries_notification_id", "push_deliveries", ["notification_id"])
    op.create_index("ix_push_deliveries_user_id", "push_deliveries", ["user_id"])
    op.create_index("ix_push_delivery_pending", "push_deliveries", ["user_id", "status", "created_at"])
    op.create_index("ix_push_deliveries_group_key", "push_deliveries", ["group_key"])


def downgrade():
    op.drop_table("push_deliveries")
    op.drop_table("push_subscriptions")
