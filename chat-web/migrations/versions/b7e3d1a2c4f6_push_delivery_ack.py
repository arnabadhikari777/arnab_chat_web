"""push delivery acknowledgement (device receipt): push_deliveries.acked_at

Revision ID: b7e3d1a2c4f6
Revises: a4f8c2e1b9d0
Create Date: 2026-11-11 12:00:00.000000

Adds push_deliveries.acked_at (nullable, so every existing row stays valid).
Repaired: skips the column if a database already has it (see deployment/03-database-backup-and-migration.md).
"""
from alembic import op
import sqlalchemy as sa


revision = "b7e3d1a2c4f6"
down_revision = "a4f8c2e1b9d0"
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
    if not _has_column("push_deliveries", "acked_at"):
        op.add_column("push_deliveries", sa.Column("acked_at", sa.DateTime(), nullable=True))


def downgrade():
    if _has_column("push_deliveries", "acked_at"):
        with op.batch_alter_table("push_deliveries") as batch:
            batch.drop_column("acked_at")
