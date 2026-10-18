"""设置表、位置选项、元数据（tasks.md WP4：全局一套 + 编号/前缀覆盖、配置版本号）。"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setting",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_setting_scope", "setting", ["scope"], unique=True)

    op.create_table(
        "location_option",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
    )
    op.create_index("ix_location_option_level", "location_option", ["level"])

    op.create_table(
        "meta",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(256), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("meta")
    op.drop_index("ix_location_option_level", table_name="location_option")
    op.drop_table("location_option")
    op.drop_index("ix_setting_scope", table_name="setting")
    op.drop_table("setting")
