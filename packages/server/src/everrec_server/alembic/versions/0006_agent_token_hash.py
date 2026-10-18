"""agent 表加设备令牌哈希（tasks.md WP5）。"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent", sa.Column("token_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("token_hash")
