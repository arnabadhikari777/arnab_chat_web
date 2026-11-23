"""performance indexes (no data is changed or removed)

Revision ID: 9c1d2e3f4a5b
Revises: 1b0f7999dbc9
Create Date: 2026-11-11 00:00:00

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '9c1d2e3f4a5b'
down_revision = '1b0f7999dbc9'
branch_labels = None
depends_on = None


def upgrade():
    # IF NOT EXISTS: safe to run on a database that already has them. Only indexes are added.
    op.execute("CREATE INDEX IF NOT EXISTS ix_message_deletions_user_id ON message_deletions (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_otp_email_purpose_id ON otp_verifications (email, purpose, id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_otp_email_purpose_id")
    op.execute("DROP INDEX IF EXISTS ix_message_deletions_user_id")
