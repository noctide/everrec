"""组织树（部门 - 科室 - 区域 - 属地 - 位置）与责任人。

旧的 location_option 表不再使用，由 0010 删除。
设备的六级位置随心跳存在 agent.last_payload 里，不新增列；树节点在设备上线后自动补齐。
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_node",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("org_node.id"), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("owner_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("owner_dept", sa.String(64), nullable=False, server_default=""),
        sa.Column("owner_note", sa.String(256), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_org_node_parent_id", "org_node", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_org_node_parent_id", table_name="org_node")
    op.drop_table("org_node")
