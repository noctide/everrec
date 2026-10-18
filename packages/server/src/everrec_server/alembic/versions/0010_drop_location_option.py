"""删掉停用的 location_option 表：位置选项从 0009 起由组织树提供。"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_location_option_level", table_name="location_option")
    op.drop_table("location_option")


def downgrade() -> None:
    op.create_table(
        "location_option",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
    )
    op.create_index("ix_location_option_level", "location_option", ["level"])
