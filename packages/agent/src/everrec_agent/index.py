"""本地录像索引：每个归档分段一行 JSON，记录完整性和同步状态。

status 取值与服务端一致：local / syncing / synced / failed（设计文档第 11 节）。
追加式写入，同一路径的状态变化追加一行、读时后者覆盖前者；行数攒多了就压缩，
只保留每个路径的最新一行，避免文件只增不减、越读越慢。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("index")


@dataclass
class IndexEntry:
    path: str
    started_at: str
    size: int
    integrity: str
    status: str = "local"
    # 这一段实际有多长（秒）。桌面端时间轴按它画；旧索引没有这一项，读出来是 0。
    duration: float = 0.0


class SegmentIndex:
    def __init__(self, state_dir: Path, compact_every: int = 2000):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.file = state_dir / "segments.jsonl"
        self._compact_every = compact_every
        self._appends = 0
        self._lock = threading.Lock()
        self._bad_lines: set[int] = set()

    def _read_locked(self) -> dict[str, IndexEntry]:
        if not self.file.exists():
            return {}
        latest: dict[str, IndexEntry] = {}
        lines = self.file.read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, 1):
            if line.strip():
                try:
                    e = IndexEntry(**json.loads(line))
                except (ValueError, TypeError):
                    if number not in self._bad_lines:
                        log.warning("录像索引第 %d 行不完整或已损坏，跳过；录像文件保留", number)
                        self._bad_lines.add(number)
                    continue
                latest[e.path] = e  # 同一路径后写的覆盖先写的
        return latest

    def add(self, entry: IndexEntry) -> None:
        with self._lock:
            self._append_locked(asdict(entry))

    def update(self, path: str, **fields) -> None:
        with self._lock:
            latest = self._read_locked().get(path)
            if latest is None:
                return
            self._append_locked({**asdict(latest), **fields})

    def _append_locked(self, data: dict) -> None:
        # 断电可能留下没有换行的半行；下一条记录必须从新行开始。
        if self.file.exists() and self.file.stat().st_size:
            with self.file.open("rb") as f:
                f.seek(-1, 2)
                needs_newline = f.read(1) != b"\n"
        else:
            needs_newline = False
        with self.file.open("a", encoding="utf-8") as f:
            if needs_newline:
                f.write("\n")
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        self._appends += 1
        if self._appends >= self._compact_every:
            self._compact_locked()
            self._appends = 0

    def _compact_locked(self) -> None:
        """只保留每个路径的最新一行。"""
        latest = self._read_locked()
        tmp = self.file.with_name(self.file.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in sorted(latest.values(), key=lambda x: x.path):
                f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
        tmp.replace(self.file)
        self._bad_lines.clear()

    def entries(self) -> list[IndexEntry]:
        with self._lock:
            return list(self._read_locked().values())

    def synced_paths(self) -> set[str]:
        with self._lock:
            return {e.path for e in self._read_locked().values() if e.status == "synced"}

    def pending_count(self) -> int:
        with self._lock:
            return sum(
                1 for e in self._read_locked().values()
                if e.status != "synced" and Path(e.path).exists()
            )
