"""staff levels: users.admin_role

Revision ID: d3b8f2a6c1e4
Revises: d2a9b4e6f1c7
Create Date: 2026-11-12 18:00:00.000000

Nullable column. Existing admins keep full access (a missing level counts as super admin and is
filled in at their next login from ADMIN_EMAILS). Repaired: skips the column if it already exists.
"""
from alembic import op
import sqlalchemy as sa

revision = "d3b8f2a6c1e4"
down_revision = "d2a9b4e6f1c7"
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
    if not _has_column("users", "admin_role"):
        op.add_column("users", sa.Column("admin_role", sa.String(20), nullable=True))


def downgrade():
    if _has_column("users", "admin_role"):
        with op.batch_alter_table("users") as batch:
            batch.drop_column("admin_role")
