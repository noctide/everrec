"""数据表。骨架阶段只建 agent 和 event 两张，其余见设计文档第 11 节，按需逐步加。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, create_engine
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
    status: Mapped[str] = mapped_column(String(16), default="synced")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


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


class LocationOption(Base):
    """位置选项（州/区/楼栋/楼层），心跳回复给桌面端做下拉（tasks.md WP4）。"""

    __tablename__ = "location_option"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), index=True)  # state / district / building / floor
    value: Mapped[str] = mapped_column(String(64))


class Meta(Base):
    """少量键值对（配置版本号等）。"""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


def make_session_factory(url: str) -> sessionmaker:
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    from .migrations import run_migrations  # 延迟导入，避免 Alembic 的初始化开销

    run_migrations(url)
    return sessionmaker(engine, expire_on_commit=False)
