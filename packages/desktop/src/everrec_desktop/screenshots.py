"""定时截屏：按配置的间隔把当前屏幕存成图片，存本地 screenshots/，开关在客户端设置里。

为什么在客户端进程里做：Windows 服务跑在 Session 0，抓不到登录用户的桌面，
只有用户会话里的进程才能抓屏。多用户同时登录时会有多个进程，
所以靠"最新一张截图的时间"去重——谁看到刚存过就跳过，不会一秒存两张。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from everrec_core.config import load_config

from .common import SNAPSHOT_DIR
from .snapshot import save_screen

log = logging.getLogger("screenshots")

TICK_SECONDS = 30  # 每 30 秒看一次到没到点；实际间隔按配置（最短 1 分钟）
MIN_SLACK = 0.9  # 最新一张的年龄达到间隔的 90% 就算到点，避免时钟抖动漏抓


def is_due(newest: float | None, now: datetime, interval_seconds: int) -> bool:
    """现在该不该抓：从没抓过就抓，否则最新一张的年龄要够一个间隔。"""
    if newest is None:
        return True
    age = now.timestamp() - newest
    return age >= max(60, interval_seconds) * MIN_SLACK


def newest_screenshot(dest_dir: Path) -> float | None:
    """截图目录里最新一张的修改时间；一张都没有返回 None。"""
    if not dest_dir.exists():
        return None
    times = [p.stat().st_mtime for p in dest_dir.glob("*.jpg") if p.is_file()]
    return max(times) if times else None


def prune_screenshots(dest_dir: Path, keep: int) -> list[Path]:
    """只留最新的 keep 张，多出来的按时间从旧到新删掉；返回删掉的文件。"""
    if not dest_dir.exists():
        return []
    files = sorted((p for p in dest_dir.glob("*.jpg") if p.is_file()),
                   key=lambda p: p.stat().st_mtime)
    extra = files[:-keep] if keep > 0 else []
    removed = []
    for path in extra:
        try:
            path.unlink()
            removed.append(path)
        except OSError:  # 被别的程序占着就下次再删
            log.debug("删不掉旧截图：%s", path)
    return removed


class TimedScreenshots(QObject):
    """挂在客户端进程里的定时器；配置每轮重读，改开关不用重启客户端。"""

    def __init__(self, install_dir: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.install_dir = install_dir
        self.config_path = install_dir / "config" / "everrec.yaml"
        self.dest_dir = install_dir / SNAPSHOT_DIR
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.maybe_capture)
        self.timer.start(TICK_SECONDS * 1000)

    def maybe_capture(self, now: datetime | None = None) -> Path | None:
        """到点就抓一张，返回落盘路径；没开、没到点、抓不到都返回 None。"""
        cfg = load_config(self.config_path) if self.config_path.exists() else None
        if cfg is None or not cfg.screenshot.enabled:
            return None
        now = now or datetime.now()
        if not is_due(newest_screenshot(self.dest_dir), now, cfg.screenshot.interval_seconds):
            return None
        try:
            path = save_screen(self.dest_dir, cfg.code, now)
        except OSError as e:
            log.warning("定时截屏失败：%s", e)
            return None
        for old in prune_screenshots(self.dest_dir, cfg.screenshot.keep_files):
            log.info("截图超出上限，删掉最旧的一张：%s", old.name)
        return path
