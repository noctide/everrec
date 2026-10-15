"""设备墙状态判定（设计文档 6.3）与状态变化产生的事件。

  ok             正常录制（绿）
  offline_local  离线本地录制（黄）：Agent 报告离线期间在本地录了、还没补传完
  abnormal       异常（红）：心跳正常，但没在录或检查不通过
  lost           失联（灰）：超过 2 分钟没有心跳——由服务器判定，Agent 关机断网时只能靠这个发现
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from .db import Agent


class Status(StrEnum):
    OK = "ok"
    OFFLINE_LOCAL = "offline_local"
    ABNORMAL = "abnormal"
    LOST = "lost"


def aware(dt: datetime) -> datetime:
    """SQLite 取回来的时间不带时区，统一当 UTC。"""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def device_status(agent: Agent, now: datetime, lost_after: int) -> Status:
    if (now - aware(agent.last_heartbeat)).total_seconds() > lost_after:
        return Status.LOST
    p = agent.last_payload or {}
    checks = p.get("checks", {})
    if p.get("starting") and "fail" not in checks.values():
        return Status.OK  # 刚启动的宽限期，不算异常也不推送
    if not p.get("recording") or "fail" in checks.values():
        return Status.ABNORMAL
    if p.get("offline_since"):  # Agent 自己报告"离线时在本地录了、还没补传完"
        return Status.OFFLINE_LOCAL
    return Status.OK


# 第一版推送的 4 类事件：没在录像、失联、空间不足、恢复正常
def transition_event(old: str, new: Status, lost_after: int = 120) -> tuple[str, str, str] | None:
    """状态变化 → (事件类型, 级别, 摘要)；没有值得推送的变化返回 None。"""
    if old == new:
        return None
    if new is Status.LOST:
        return "device.lost", "critical", f"设备失联，超过 {lost_after} 秒没有心跳"
    if new is Status.ABNORMAL:
        return "recording.stopped", "critical", "设备在线但没在正常录像"
    if old in (Status.LOST, Status.ABNORMAL) and new in (Status.OK, Status.OFFLINE_LOCAL):
        return "device.recovered", "info", "恢复正常录像"
    return None
