"""中转节点（tasks.md WP10）：agent 加 via/relay，recording 加 relay_id/relay_rec_id，新建 relay 游标表。"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent", sa.Column("via", sa.String(8), nullable=False, server_default="local"))
    op.add_column("agent", sa.Column("relay", sa.String(64), nullable=True))
    op.create_index("ix_agent_relay", "agent", ["relay"])
    op.add_column("recording", sa.Column("relay_id", sa.String(64), nullable=True))
    op.add_column("recording", sa.Column("relay_rec_id", sa.BigInteger, nullable=True))
    op.create_index("ix_recording_relay_id", "recording", ["relay_id"])
    op.create_table(
        "relay",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("url", sa.String(256), nullable=False),
        sa.Column("since_event", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("since_recording", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_relay_name", "relay", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_relay_name", table_name="relay")
    op.drop_table("relay")
    op.drop_index("ix_recording_relay_id", table_name="recording")
    op.drop_column("recording", "relay_rec_id")
    op.drop_column("recording", "relay_id")
    op.drop_index("ix_agent_relay", table_name="agent")
    op.drop_column("agent", "relay")
    op.drop_column("agent", "via")
