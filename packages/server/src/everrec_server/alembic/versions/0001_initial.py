"""首迁移：agent、event、recording 三张表（骨架阶段的 create_all 换成 Alembic）。

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("hostname", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_payload", sa.JSON(), nullable=False),
        sa.Column("recent_logs", sa.JSON(), nullable=False),
    )
    op.create_index("ix_agent_code", "agent", ["code"], unique=True)

    op.create_table(
        "event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agent.id"), nullable=True),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("summary", sa.String(256), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_type", "event", ["type"])

    op.create_table(
        "recording",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("integrity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_recording_code", "recording", ["code"])
    op.create_index("ix_recording_start_at", "recording", ["start_at"])
    op.create_index("ix_recording_sha256", "recording", ["sha256"])


def downgrade() -> None:
    op.drop_table("recording")
    op.drop_table("event")
    op.drop_table("agent")
