"""分段文件：命名、归档路径、本地滚动覆盖（设计文档 5.3、5.4）。

分段先写进 _spool/，FFmpeg 写完一段开始下一段后，上一段就是完整的，归档到：
    {位置编号}/{yyyy}/{MM}/{dd}/{位置编号}_{HHmmss}.ts
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ffmpeg import SEGMENT_EXT, SPOOL_DIRNAME

_SPOOL_NAME = re.compile(r"^(?P<code>.+)_(?P<ts>\d{8}_\d{6})" + re.escape(SEGMENT_EXT) + "$")


@dataclass(frozen=True)
class SpoolSegment:
    path: Path
    code: str
    started_at: datetime


def parse_spool_name(path: Path) -> SpoolSegment | None:
    m = _SPOOL_NAME.match(path.name)
    if not m:
        return None
    return SpoolSegment(path, m["code"], datetime.strptime(m["ts"], "%Y%m%d_%H%M%S"))


def list_spool(spool: Path) -> list[SpoolSegment]:
    """按开始时间排序的 spool 分段。"""
    if not spool.exists():
        return []
    segs = [s for p in spool.iterdir() if p.is_file() and (s := parse_spool_name(p))]
    return sorted(segs, key=lambda s: (s.started_at, s.path.name))


def archive_path(recordings_dir: Path, seg: SpoolSegment) -> Path:
    t = seg.started_at
    return (
        recordings_dir / seg.code / f"{t:%Y}" / f"{t:%m}" / f"{t:%d}"
        / f"{seg.code}_{t:%H%M%S}{SEGMENT_EXT}"
    )


def archive_segment(recordings_dir: Path, seg: SpoolSegment) -> Path:
    dest = archive_path(recordings_dir, seg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():  # 同一秒重启过：加后缀，不覆盖
        dest = dest.with_name(f"{dest.stem}_{int(seg.path.stat().st_mtime)}{dest.suffix}")
    seg.path.replace(dest)
    return dest


def iter_archived(recordings_dir: Path) -> Iterable[Path]:
    """所有已归档分段，最旧的在前（文件名里含日期时间，路径排序即时间排序）。"""
    files = [
        p for p in recordings_dir.rglob(f"*{SEGMENT_EXT}")
        if SPOOL_DIRNAME not in p.parts and "_quarantine" not in p.parts
    ]
    return sorted(files, key=lambda p: (p.parent.as_posix(), p.name))


def enforce_quota(
    recordings_dir: Path,
    quota_bytes: int,
    can_delete: Callable[[Path], bool] = lambda _p: True,
) -> list[Path]:
    """超过本地配额时从最旧开始删，只删 can_delete 允许的（例如已同步的）。返回删除的文件。"""
    files = list(iter_archived(recordings_dir))
    total = sum(p.stat().st_size for p in files)
    deleted: list[Path] = []
    for p in files:
        if total <= quota_bytes:
            break
        if not can_delete(p):
            continue
        size = p.stat().st_size
        p.unlink()
        total -= size
        deleted.append(p)
    return deleted
