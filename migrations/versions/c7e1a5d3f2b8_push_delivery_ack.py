"""push delivery acknowledgements - legacy duplicate of b7e3d1a2c4f6, now a safe no-op step

Revision ID: c7e1a5d3f2b8
Revises: b7e3d1a2c4f6
Create Date: 2026-11-11 18:00:00.000000

HISTORY: this revision used to branch from a4f8c2e1b9d0 in parallel with b7e3d1a2c4f6 and added a second,
unused design for push receipts (ack_token_hash, ack_expires_at, message_upto_id, acked_upto_id,
applied_upto_id and the index ix_push_delivery_acked). The application never used those columns: it
verifies receipts with a signed token and only needs push_deliveries.acked_at.

The revision id is kept so a production database that was already stamped with it still has a place in
the history. It now only guarantees acked_at. If a database already has the legacy columns they are left
exactly as they are (nullable, unused, harmless). Nothing is dropped.
"""
from alembic import op
import sqlalchemy as sa


revision = "c7e1a5d3f2b8"
down_revision = "b7e3d1a2c4f6"
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
    # Nothing to undo: acked_at belongs to b7e3d1a2c4f6, and legacy columns are never touched here.
    pass
