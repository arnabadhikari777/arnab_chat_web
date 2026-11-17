"""admin control: site settings, moderation notes, timed suspension, sign-out-everywhere

Revision ID: c9a1e5f7b2d3
Revises: c7e1a5d3f2b8
Create Date: 2026-11-12 12:00:00.000000

Only adds things (two tables, two users columns). Existing rows stay valid.
Repaired: guarded so it works on a database that went down the other branch (it may already have site_settings),
and users columns are added with a plain ALTER TABLE ADD COLUMN (no table rebuild, no copy of user rows).
"""
from alembic import op
import sqlalchemy as sa


revision = "c9a1e5f7b2d3"
down_revision = "c7e1a5d3f2b8"
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
    if not _has_column("users", "suspended_until"):
        op.add_column("users", sa.Column("suspended_until", sa.DateTime(), nullable=True))
    if not _has_column("users", "session_epoch"):
        op.add_column("users", sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    if _has_column("users", "session_epoch") or _has_column("users", "suspended_until"):
        with op.batch_alter_table("users") as batch:
            if _has_column("users", "session_epoch"):
                batch.drop_column("session_epoch")
            if _has_column("users", "suspended_until"):
                batch.drop_column("suspended_until")
    if _has_index("moderation_notes", "ix_moderation_notes_user_id"):
        op.drop_index("ix_moderation_notes_user_id", table_name="moderation_notes")
    if _has_table("moderation_notes"):
        op.drop_table("moderation_notes")
    # site_settings is NOT dropped on downgrade: it holds the admin's saved settings.
