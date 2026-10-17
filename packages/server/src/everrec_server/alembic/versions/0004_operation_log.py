"""操作日志表：查看、下载、加锁、改设置留痕（设计文档 15）。"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("recording_id", sa.Integer(), sa.ForeignKey("recording.id"), nullable=True),
        sa.Column("user", sa.String(64), nullable=True),
        sa.Column("detail", sa.String(256), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operation_log_action", "operation_log", ["action"])


def downgrade() -> None:
    op.drop_index("ix_operation_log_action", table_name="operation_log")
    op.drop_table("operation_log")
