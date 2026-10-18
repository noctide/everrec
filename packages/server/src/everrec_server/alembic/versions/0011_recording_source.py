"""录像来源：camera（摄像头）/ screen（桌面录屏），tasks.md WP12。

第一版界面不按来源筛选，只落库；旧数据的 source 一律按 camera 算。
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recording",
        sa.Column("source", sa.String(16), nullable=False, server_default="camera"),
    )


def downgrade() -> None:
    op.drop_column("recording", "source")
