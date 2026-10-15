"""本地录像索引：每个归档分段一行 JSON，记录完整性和同步状态。

status 取值与服务端一致：local / syncing / synced / failed（设计文档第 11 节）。
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

    def add(self, entry: IndexEntry) -> None:
        with self._lock, self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def entries(self) -> list[IndexEntry]:
        if not self.file.exists():
            return []
        latest: dict[str, IndexEntry] = {}
        for line in self.file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = IndexEntry(**json.loads(line))
                latest[e.path] = e  # 同一路径后写的覆盖先写的
        return list(latest.values())

    def synced_paths(self) -> set[str]:
        return {e.path for e in self.entries() if e.status == "synced"}

    def pending_count(self) -> int:
        return sum(1 for e in self.entries() if e.status != "synced" and Path(e.path).exists())
