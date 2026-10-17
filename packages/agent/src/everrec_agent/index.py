"""本地录像索引：每个归档分段一行 JSON，记录完整性和同步状态。

status 取值与服务端一致：local / syncing / synced / failed（设计文档第 11 节）。
追加式写入：同一路径的状态变化追加一行，读时后者覆盖前者。
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class IndexEntry:
    path: str
    started_at: str
    size: int
    integrity: str
    status: str = "local"


class SegmentIndex:
    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.file = state_dir / "segments.jsonl"
        self._lock = threading.Lock()

    def _read_locked(self) -> dict[str, IndexEntry]:
        if not self.file.exists():
            return {}
        latest: dict[str, IndexEntry] = {}
        for line in self.file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = IndexEntry(**json.loads(line))
                latest[e.path] = e  # 同一路径后写的覆盖先写的
        return latest

    def add(self, entry: IndexEntry) -> None:
        with self._lock, self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def update(self, path: str, **fields) -> None:
        """追加一行覆盖同一路径的最新状态（上传完成后标 synced）。"""
        with self._lock:
            latest = self._read_locked().get(path)
            if latest is None:
                return
            with self.file.open("a", encoding="utf-8") as f:
                f.write(json.dumps({**asdict(latest), **fields}, ensure_ascii=False) + "\n")

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
