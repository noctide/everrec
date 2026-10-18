"""数据表。骨架阶段只建 agent 和 event 两张，其余见设计文档第 11 节，按需逐步加。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hostname: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # 上次判定的状态，用于发现变化
    last_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    recent_logs: Mapped[list] = mapped_column(JSON, default=list)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 设备令牌哈希（WP5）
    # 中转节点（tasks.md WP10）：local = 直连主服务器；relay = 经子公司中转节点
    via: Mapped[str] = mapped_column(String(8), default="local")
    relay: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # 归属中转节点名


class Event(Base):
    """推送事件源（设计文档第 8 节）。先写表，再由投递任务推送。"""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agent.id"), nullable=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Recording(Base):
    """录像分段索引（设计文档第 11 节）。到服务器的分段一定已校验，status 恒为 synced。"""

    __tablename__ = "recording"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(String(512))
    source_filename: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    integrity: Mapped[str] = mapped_column(String(16))  # ok / repaired / partial
    source: Mapped[str] = mapped_column(String(16), default="camera")  # camera / screen（WP12）
    status: Mapped[str] = mapped_column(String(16), default="synced")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # 中转节点（tasks.md WP10）：主服务器只存索引，文件在中转节点上，回放时按需代理取
    relay_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # 归属中转节点名
    relay_rec_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 在中转节点上的 recording id


class Relay(Base):
    """中转节点游标状态（tasks.md WP10）：主服务器每 30 秒拉一次的增量游标与失联计数。"""

    __tablename__ = "relay"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(256))
    since_event: Mapped[int] = mapped_column(BigInteger, default=0)
    since_recording: Mapped[int] = mapped_column(BigInteger, default=0)
    failures: Mapped[int] = mapped_column(default=0)  # 连续拉不到的次数
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok / lost
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperationLog(Base):
    """操作日志（设计文档 15）：查看、下载、加锁、改设置都留痕。用户名在 WP5 登录后写入。"""

    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(32), index=True)  # view / download / lock / unlock / settings
    recording_id: Mapped[int | None] = mapped_column(ForeignKey("recording.id"), nullable=True)
    user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(256), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """服务端设置（设计文档 7、tasks.md WP4）：全局一套 + 按位置编号/前缀覆盖。"""

    __tablename__ = "setting"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # scope 取值："global" / "device:<code>" / "prefix:<prefix>"
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrgNode(Base):
    """组织树节点：部门 - 科室 - 区域 - 属地 - 位置（level 1-5）。编号不进树，设备本身是叶子。"""

    __tablename__ = "org_node"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("org_node.id"), nullable=True, index=True)
    level: Mapped[int] = mapped_column(Integer)  # 1=部门 … 5=位置
    code: Mapped[str] = mapped_column(String(32))  # 英文数字，进设备编号和文件路径，建好后不改
    name: Mapped[str] = mapped_column(String(64), default="")  # 中文显示名，可空
    owner_name: Mapped[str] = mapped_column(String(64), default="")  # 责任人
    owner_dept: Mapped[str] = mapped_column(String(64), default="")  # 责任部门
    owner_note: Mapped[str] = mapped_column(String(256), default="")  # 备注（联系方式等）
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Meta(Base):
    """少量键值对（配置版本号等）。"""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


class Release(Base):
    """安装包版本（tasks.md WP9）：管理员在网站上传，Agent 检查更新时下载。"""

    __tablename__ = "release"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(256))  # 安装包文件名
    path: Mapped[str] = mapped_column(String(512))  # 服务器上的存放路径
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)  # 更新说明
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Delivery(Base):
    """推送发件箱（设计文档 8.5）：先存后发，重试到送达或连续失败 24 小时暂停。"""

    __tablename__ = "delivery"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 去重
    event_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 完整消息体（设计 8.4）
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/sent/failed/paused
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    http_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def make_session_factory(url: str) -> sessionmaker:
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    from .migrations import run_migrations  # 延迟导入，避免 Alembic 的初始化开销

    run_migrations(url)
    return sessionmaker(engine, expire_on_commit=False)
