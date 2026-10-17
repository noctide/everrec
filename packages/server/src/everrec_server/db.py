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
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    integrity: Mapped[str] = mapped_column(String(16))  # ok / repaired / partial
    status: Mapped[str] = mapped_column(String(16), default="synced")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


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
