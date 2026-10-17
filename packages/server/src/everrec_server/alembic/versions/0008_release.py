"""安装包版本表（tasks.md WP9）：管理员上传新版本，Agent 检查更新时下载。"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "release",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(256), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("notes", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_release_version", "release", ["version"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_release_version", table_name="release")
    op.drop_table("release")
