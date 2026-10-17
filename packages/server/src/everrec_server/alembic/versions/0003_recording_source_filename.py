"""保存上传时的原始文件名，归档重名后仍能正确去重。"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0001"  # 本分支无 WP6 的 0002(delivery)；合并时需改回 0002
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recording", sa.Column("source_filename", sa.String(128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recording") as batch:
        batch.drop_column("source_filename")
