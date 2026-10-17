"""滚动覆盖与磁盘空间（设计文档 5.4）。

- 后台任务每小时一次：删除超过保留天数的分段（文件 + recording 行一起删），加锁的跳过。
- 滚动覆盖开关关闭时不删。
- 磁盘达到告警阈值（默认 85%）产生 storage.low 事件；达到危险阈值（默认 95%）上传接口返回 507。
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Recording

log = logging.getLogger("everrec.server.retention")


def disk_usage_ratio(path: Path) -> float:
    """磁盘已用比例（0..1）。目录不存在时按 0 处理，不影响首次启动。"""
    try:
        total, used, _free = shutil.disk_usage(path)
    except OSError:
        return 0.0
    return used / total if total else 0.0


def disk_info(path: Path) -> dict:
    """磁盘总量/剩余/已用比例，设置页用它算"还能存几天"。"""
    try:
        total, used, free = shutil.disk_usage(path)
    except OSError:
        return {"total_gb": 0.0, "free_gb": 0.0, "used_ratio": 0.0}
    return {
        "total_gb": round(total / 1e9, 1),
        "free_gb": round(free / 1e9, 1),
        "used_ratio": round(used / total, 3) if total else 0.0,
    }


def _delete_file(recordings_dir: Path, path_str: str) -> bool:
    """删除录像文件；只删录像目录内的（和上传接口同一套防越权检查）。"""
    p = Path(path_str)
    try:
        if not p.resolve().is_relative_to(recordings_dir.resolve()):
            log.warning("录像文件不在录像目录内，跳过删除：%s", path_str)
            return False
    except (OSError, ValueError):
        return False
    try:
        p.unlink(missing_ok=True)
        return True
    except OSError:
        log.warning("删除录像文件失败：%s", path_str)
        return False


def sweep_retention(
    s: Session,
    recordings_dir: Path,
    retention_days: int,
    rollover_enabled: bool,
    now: datetime,
) -> int:
    """删除超过保留天数的分段，返回删除条数。加锁的分段不参与覆盖（事故留证）。"""
    if not rollover_enabled:
        return 0
    cutoff = now - timedelta(days=retention_days)
    expired = s.scalars(
        select(Recording).where(Recording.start_at < cutoff, Recording.locked.is_(False))
    ).all()
    deleted = 0
    for rec in expired:
        if _delete_file(recordings_dir, rec.path):
            log.info("滚动覆盖：删除过期分段 %s（%s）", rec.path, rec.start_at)
        else:
            log.info("滚动覆盖：删除过期分段记录 %s（文件已不存在）", rec.path)
        s.delete(rec)
        deleted += 1
    return deleted
