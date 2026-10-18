"""日志（设计文档 4.7）：按天一个文件，保留 30 天；最近的警告和错误随心跳上报。"""

from __future__ import annotations

import collections
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

from everrec_core.protocol import LogLine


class RecentProblems(logging.Handler):
    """内存里留最近 50 条警告及以上，心跳时取走。"""

    def __init__(self, capacity: int = 50):
        super().__init__(level=logging.WARNING)
        self.buffer: collections.deque[LogLine] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        self.buffer.append(
            LogLine(
                at=datetime.fromtimestamp(record.created).astimezone(),
                level=record.levelname,
                message=record.getMessage(),
            )
        )

    def drain(self) -> list[LogLine]:
        items = list(self.buffer)
        self.buffer.clear()
        return items


def setup_logging(logs_dir: Path, verbose: bool = False) -> RecentProblems:
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    # httpx 每发一次请求都打一行 INFO，能把日志和客户端的"最近日志"刷满（实测 301 行里 244 行是它），
    # 现场要的"分段已归档""上传完成"全被埋掉。只留它的警告。
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    file_h = logging.handlers.TimedRotatingFileHandler(
        logs_dir / "agent.log", when="midnight", backupCount=30, encoding="utf-8"
    )
    file_h.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    recent = RecentProblems()

    for h in (file_h, console, recent):
        root.addHandler(h)
    return recent
