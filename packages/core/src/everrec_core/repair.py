"""视频修复（设计文档第 9 节）。Agent 启动自检、服务器巡检、桌面修复工具共用。

原则：原文件永远不改动，修复结果另存。
第一版只做两级：
  1 级 重新封装：不重编码，重建时间戳，丢弃坏包。
  2 级 截断恢复：MPEG-TS 按 188 字节包重新对齐，去掉头尾残包后再重新封装。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .ffmpeg import ffmpeg_binary

TS_PACKET = 188
TS_SYNC = 0x47


class Integrity(StrEnum):
    OK = "ok"
    REPAIRED = "repaired"
    PARTIAL = "partial"
    BROKEN = "broken"


# 修复工具上给普通人看的文字（设计文档 9.6）
FRIENDLY_TEXT = {
    Integrity.OK: "正常",
    Integrity.REPAIRED: "已修复，可以正常播放",
    Integrity.PARTIAL: "部分恢复，缺少约 {lost} 秒",
    Integrity.BROKEN: "无法修复（文件内容已严重损坏）",
}


@dataclass
class Inspection:
    opens: bool
    duration: float | None
    decode_errors: int
    detail: str = ""

    @property
    def healthy(self) -> bool:
        return self.opens and self.decode_errors == 0 and (self.duration or 0) > 0


@dataclass
class RepairResult:
    integrity: Integrity
    output: Path | None
    method: str
    before_sec: float | None
    after_sec: float | None
    lost_sec: float = 0
    message: str = ""

    @property
    def friendly(self) -> str:
        return FRIENDLY_TEXT[self.integrity].format(lost=round(self.lost_sec))


def _ffprobe() -> str:
    ff = Path(ffmpeg_binary())
    probe = ff.with_name(ff.name.replace("ffmpeg", "ffprobe"))
    return str(probe)


def probe_duration(path: Path) -> float | None:
    r = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return None


def inspect(path: Path) -> Inspection:
    """能否打开 + 全片解码统计错误（设计文档 9.2）。"""
    duration = probe_duration(path)
    if duration is None:
        return Inspection(False, None, 0, "无法读取文件头")
    r = subprocess.run(
        [ffmpeg_binary(), "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    errors = [ln for ln in r.stderr.splitlines() if ln.strip()]
    if r.returncode != 0 and not errors:
        errors = ["解码失败"]
    return Inspection(True, duration, len(errors), "\n".join(errors[:20]))


def _remux(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [
            ffmpeg_binary(), "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-err_detect", "ignore_err", "-fflags", "+genpts+discardcorrupt",
            "-i", str(src), "-map", "0:v:0", "-c", "copy", "-f", "mpegts", str(dst),
        ],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def _keyframe_times(path: Path) -> list[float]:
    r = subprocess.run(
        [_ffprobe(), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    times = []
    for line in r.stdout.splitlines():
        try:
            times.append(float(line.strip().strip(",")))
        except ValueError:
            continue
    return sorted(times)


def _cut_before_last_gop(src: Path, dst: Path) -> bool:
    """尾部那组画面写到一半：截到最后一个关键帧之前，只保留完整的画面组。"""
    kfs = _keyframe_times(src)
    if len(kfs) < 2:
        return False
    start = kfs[0]
    r = subprocess.run(
        [ffmpeg_binary(), "-hide_banner", "-nostdin", "-v", "error", "-y",
         "-i", str(src), "-map", "0:v:0", "-c", "copy",
         "-t", f"{kfs[-1] - start:.3f}", "-f", "mpegts", str(dst)],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def _realign_ts(src: Path, dst: Path) -> bool:
    """找到连续的 0x47 同步字节，截掉头部垃圾和尾部残包。"""
    data = src.read_bytes()
    start = next(
        (i for i in range(min(len(data), TS_PACKET * 4))
         if all(i + k * TS_PACKET < len(data) and data[i + k * TS_PACKET] == TS_SYNC for k in range(3))),
        None,
    )
    if start is None:
        return False
    body = data[start:]
    body = body[: len(body) - len(body) % TS_PACKET]
    if not body:
        return False
    dst.write_bytes(body)
    return True


def repair_file(src: Path, out_dir: Path, expected_sec: float | None = None) -> RepairResult:
    """从轻到重尝试修复，结果写到 out_dir/<原文件名>，原文件不动。"""
    src = Path(src)
    before = inspect(src)
    if before.healthy:
        return RepairResult(Integrity.OK, src, "none", before.duration, before.duration)

    out = Path(out_dir) / src.name

    def _judge(method: str) -> RepairResult | None:
        after = inspect(out)
        if not after.healthy:
            return None
        lost = max(0.0, (expected_sec or 0) - (after.duration or 0))
        integ = Integrity.PARTIAL if lost > 2 else Integrity.REPAIRED
        return RepairResult(integ, out, method, before.duration, after.duration, lost)

    # 1 级：重新封装
    if _remux(src, out) and (res := _judge("remux")):
        return res

    # 2 级：截断恢复（MPEG-TS）：对齐包边界，重新封装，再截掉写到一半的最后一组画面
    with tempfile.TemporaryDirectory() as tmp:
        aligned, remuxed = Path(tmp) / "aligned.ts", Path(tmp) / "remuxed.ts"
        source = aligned if _realign_ts(src, aligned) else src
        if _remux(source, remuxed) and _cut_before_last_gop(remuxed, out) and (
            res := _judge("truncate")
        ):
            return res

    out.unlink(missing_ok=True)
    return RepairResult(
        Integrity.BROKEN, None, "none", before.duration, None, expected_sec or 0, before.detail
    )
