"""admin control: site settings, moderation notes, timed suspension, sign-out-everywhere

Revision ID: c9a1e5f7b2d3
Revises: b7e3d1a2c4f6
Create Date: 2026-11-12 12:00:00.000000

Only adds things (two tables, two users columns). Existing rows stay valid. Reversible.
"""
from alembic import op
import sqlalchemy as sa


revision = "c9a1e5f7b2d3"
down_revision = "b7e3d1a2c4f6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "site_settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_table(
        "moderation_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("ix_moderation_notes_user_id", "moderation_notes", ["user_id"])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("suspended_until", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("session_epoch")
        batch.drop_column("suspended_until")
    op.drop_index("ix_moderation_notes_user_id", table_name="moderation_notes")
    op.drop_table("moderation_notes")
    op.drop_table("site_settings")
