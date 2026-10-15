"""健康检查（设计文档 6.1）。第一版做 ①服务 ②设备 ③录制进程 ④数据流 ⑥存储。

④ 是重点："进程在运行"不等于"在录像"，所以盯着当前分段文件是否在增长。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .segments import list_spool


class Check(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class GrowthWatcher:
    """当前分段 stall_seconds 秒没有变大就判定为假录制。"""

    spool: Path
    stall_seconds: float = 30
    _path: Path | None = None
    _size: int = -1
    _changed_at: float = field(default_factory=time.monotonic)

    _grew: bool = False

    def reset(self) -> None:
        self._path, self._size, self._changed_at = None, -1, time.monotonic()
        self._grew = False

    @property
    def grew_since_reset(self) -> bool:
        """重启录制后，文件确实写出过数据。进程活着但一个字节没写不算在录。"""
        return self._grew

    def check(self, now: float | None = None) -> tuple[Check, str]:
        now = time.monotonic() if now is None else now
        segs = list_spool(self.spool)
        if segs:
            cur = segs[-1].path
            try:
                size = cur.stat().st_size
            except FileNotFoundError:  # 刚被归档走
                size = -1
            if cur != self._path or size != self._size:
                if self._path is not None and size > 0:
                    self._grew = True
                self._path, self._size, self._changed_at = cur, size, now
        idle = now - self._changed_at
        if idle >= self.stall_seconds:
            return Check.FAIL, f"录像文件 {int(idle)} 秒无增长"
        return Check.OK, ""

    @property
    def current_segment(self) -> str | None:
        return self._path.name if self._path else None


def storage_check(path: Path, usage_limit: float = 0.85) -> tuple[Check, str, float]:
    path.mkdir(parents=True, exist_ok=True)
    du = shutil.disk_usage(path)
    used = du.used / du.total
    if used >= 0.95:
        return Check.FAIL, f"磁盘已用 {used:.0%}", used
    if used >= usage_limit:
        return Check.WARN, f"磁盘已用 {used:.0%}", used
    return Check.OK, "", used
