"""push delivery acknowledgement (device receipt)

Revision ID: b7e3d1a2c4f6
Revises: a4f8c2e1b9d0
Create Date: 2026-11-11 12:00:00.000000

Adds push_deliveries.acked_at. Nullable, so every existing row stays valid. Reversible.
"""
from alembic import op
import sqlalchemy as sa


revision = "b7e3d1a2c4f6"
down_revision = "a4f8c2e1b9d0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("push_deliveries") as batch:
        batch.add_column(sa.Column("acked_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("push_deliveries") as batch:
        batch.drop_column("acked_at")
