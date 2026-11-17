"""schema reconcile: make any production database match the models (additive only)

Revision ID: a9f0c3d7b1e2
Revises: f1a2b3c4d5e6
Create Date: 2026-11-16 12:00:00.000000

WHY THIS EXISTS: the history used to have two heads. A production database could have been upgraded along
either branch, or stamped at a revision whose columns were never added. The user loader then failed with
"no such column" while loading an existing user.

This final step checks the schema against what the models need and ADDS whatever is missing. It never drops,
renames, rewrites or deletes anything, and it never touches rows. Columns that exist only in an old branch
(users.status_until, push_deliveries.ack_token_hash and friends) are left alone.
"""
from alembic import op
import sqlalchemy as sa


revision = "a9f0c3d7b1e2"
down_revision = "f1a2b3c4d5e6"
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


# (table, column, type, nullable, server_default) - exactly what app/models needs on top of the first migrations
_COLUMNS = [
    ("users", "notify_messages", lambda: sa.Boolean(), False, sa.true()),
    ("users", "notify_announcements", lambda: sa.Boolean(), False, sa.true()),
    ("users", "suspended_until", lambda: sa.DateTime(), True, None),
    ("users", "session_epoch", lambda: sa.Integer(), False, "0"),
    ("users", "admin_role", lambda: sa.String(20), True, None),
    ("push_deliveries", "acked_at", lambda: sa.DateTime(), True, None),
]


def upgrade():
    for table, column, make_type, nullable, default in _COLUMNS:
        if _has_table(table) and not _has_column(table, column):
            op.add_column(table, sa.Column(column, make_type(), nullable=nullable, server_default=default))

    if not _has_table("site_settings"):
        op.create_table(
            "site_settings",
            sa.Column("key", sa.String(60), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )
    if not _has_table("moderation_notes"):
        op.create_table(
            "moderation_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("note", sa.String(500), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )
    if not _has_index("moderation_notes", "ix_moderation_notes_user_id"):
        op.create_index("ix_moderation_notes_user_id", "moderation_notes", ["user_id"])
    if not _has_table("security_events"):
        op.create_table(
            "security_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("kind", sa.String(length=30), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("ip", sa.String(length=45)),
            sa.Column("detail", sa.String(length=120)),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if not _has_index("security_events", "ix_security_kind_id"):
        op.create_index("ix_security_kind_id", "security_events", ["kind", "id"])


def downgrade():
    # Deliberately empty: this step only added missing pieces to bring a database up to the models.
    # Removing them would break the running application and could delete admin data.
    pass
