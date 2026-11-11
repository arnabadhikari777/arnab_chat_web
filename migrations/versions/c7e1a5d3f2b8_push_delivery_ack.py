"""push delivery acknowledgements (additive: new nullable columns + one index)

Revision ID: c7e1a5d3f2b8
Revises: a4f8c2e1b9d0
Create Date: 2026-11-11 18:00:00.000000

Nothing is dropped or rewritten. Existing rows keep NULL in every new column.
Rollback:  flask db downgrade a4f8c2e1b9d0   (removes only these columns/index)
"""
from alembic import op
import sqlalchemy as sa


revision = "c7e1a5d3f2b8"
down_revision = "a4f8c2e1b9d0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("push_deliveries") as batch:
        batch.add_column(sa.Column("ack_token_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("ack_expires_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("message_upto_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("acked_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("acked_upto_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("applied_upto_id", sa.Integer(), nullable=True))
        batch.create_index("ix_push_delivery_acked", ["user_id", "acked_at"])


def downgrade():
    with op.batch_alter_table("push_deliveries") as batch:
        batch.drop_index("ix_push_delivery_acked")
        batch.drop_column("applied_upto_id")
        batch.drop_column("acked_upto_id")
        batch.drop_column("acked_at")
        batch.drop_column("message_upto_id")
        batch.drop_column("ack_expires_at")
        batch.drop_column("ack_token_hash")
