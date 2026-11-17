"""site settings table - legacy duplicate of the table in c9a1e5f7b2d3, now a safe guard

Revision ID: d2a9b4e6f1c7
Revises: c9a1e5f7b2d3
Create Date: 2026-11-11 20:00:00.000000

HISTORY: this revision used to create site_settings in a second branch, in parallel with c9a1e5f7b2d3
(which creates the same table). Creating it twice is what made the history impossible to apply.
It now only makes sure the table exists. If it already exists, nothing happens and no setting is touched.
"""
from alembic import op
import sqlalchemy as sa


revision = "d2a9b4e6f1c7"
down_revision = "c9a1e5f7b2d3"
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
    if not _has_table("site_settings"):
        op.create_table(
            "site_settings",
            sa.Column("key", sa.String(length=60), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )


def downgrade():
    # site_settings is created by c9a1e5f7b2d3 and holds admin data: never dropped here.
    pass
