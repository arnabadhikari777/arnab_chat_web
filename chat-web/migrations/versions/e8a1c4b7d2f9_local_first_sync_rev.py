"""local-first sync: messages.sync_rev + sync_counter

Revision ID: e8a1c4b7d2f9
Revises: d4f6a8c0e2b3
Create Date: 2026-11-27 09:00:00.000000

Additive and non-destructive (Task 2, Android local-first sync):
  * adds ONE nullable-safe column messages.sync_rev (NOT NULL, server default 0) and an index on it;
  * creates the one-row table sync_counter;
  * back-fills sync_rev = id for every existing message and starts the counter at the highest id, so old rows keep
    their order and every new revision is larger. No message, attachment, deletion or user row is changed otherwise.
Safe on an existing database and on a fresh one (each step is skipped when it already exists).
Reversible: downgrade drops only the index, the column and the table added here.
"""
from alembic import op
import sqlalchemy as sa

revision = "e8a1c4b7d2f9"
down_revision = "d4f6a8c0e2b3"
branch_labels = None
depends_on = None


def _insp():
    return sa.inspect(op.get_bind())


def upgrade():
    insp = _insp()
    if "sync_counter" not in insp.get_table_names():
        op.create_table(
            "sync_counter",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("value", sa.BigInteger(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )
    cols = {c["name"] for c in insp.get_columns("messages")}
    if "sync_rev" not in cols:
        with op.batch_alter_table("messages") as batch:
            batch.add_column(sa.Column("sync_rev", sa.BigInteger(), nullable=False, server_default="0"))
    indexes = {i["name"] for i in _insp().get_indexes("messages")}
    if "ix_messages_sync_rev" not in indexes:
        op.create_index("ix_messages_sync_rev", "messages", ["sync_rev"])
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE messages SET sync_rev = id WHERE sync_rev = 0"))
    top = bind.execute(sa.text("SELECT COALESCE(MAX(sync_rev), 0) FROM messages")).scalar() or 0
    have = bind.execute(sa.text("SELECT COUNT(*) FROM sync_counter WHERE id = 1")).scalar()
    if have:
        bind.execute(sa.text("UPDATE sync_counter SET value = :v WHERE id = 1 AND value < :v"), {"v": top})
    else:
        bind.execute(sa.text("INSERT INTO sync_counter (id, value) VALUES (1, :v)"), {"v": top})


def downgrade():
    insp = _insp()
    if "ix_messages_sync_rev" in {i["name"] for i in insp.get_indexes("messages")}:
        op.drop_index("ix_messages_sync_rev", table_name="messages")
    if "sync_rev" in {c["name"] for c in insp.get_columns("messages")}:
        with op.batch_alter_table("messages") as batch:
            batch.drop_column("sync_rev")
    if "sync_counter" in insp.get_table_names():
        op.drop_table("sync_counter")
