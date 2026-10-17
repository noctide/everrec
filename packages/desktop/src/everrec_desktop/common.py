"""桌面端共用的纯逻辑（与 Qt 解耦，便于单元测试）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 主题色：只用黑白灰（design/source/*.dc.html）
COLORS = {
    "bg": "#FFFFFF", "bar": "#F5F5F5", "line": "#D4D4D4", "text": "#383838",
    "muted": "#666666", "strong": "#1A1A1A", "accent": "#545454",
    "thumb": "#3F3F3F", "chip": "#F0F0F0", "chip_alert": "#E8E8E8",
    "note": "#8C8C8C", "warn": "#404040", "error": "#1A1A1A",
}


def read_status(state_dir: Path) -> dict:
    """读 Agent 写出的 status.json；没有或坏了就返回空 dict。"""
    path = state_dir / "status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


@dataclass(frozen=True)
class TimelineBar:
    start: float  # 当天 0-24 之间的小时数
    end: float
    synced: bool  # True=已上传，False=仅在本机


def build_timeline(state_dir: Path, today: datetime | None = None) -> list[TimelineBar]:
    """读 segments.jsonl，组出「今天」的录像时间轴（客户端主界面用）。"""
    today = today or datetime.now()
    path = state_dir / "segments.jsonl"
    if not path.exists():
        return []
    bars: list[TimelineBar] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            started = datetime.fromisoformat(e["started_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if started.date() != today.date():
            continue
        hour = started.hour + started.minute / 60 + started.second / 3600
        # 每段按 5 分钟画（索引里没有时长；首版分段固定 5 分钟）
        bars.append(TimelineBar(start=hour, end=min(24.0, hour + 5 / 60),
                                synced=e.get("status") == "synced"))
    return sorted(bars, key=lambda b: b.start)


def parse_log_line(line: str) -> dict | None:
    """解析一行日志：`2026-10-17 10:40:05,123 INFO    agent: message`。"""
    parts = line.split(" ", 3)
    if len(parts) < 4:
        return None
    level = parts[2].strip()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return None
    rest = parts[3].lstrip()
    if ": " in rest:
        rest = rest.split(": ", 1)[1]
    return {"time": f"{parts[0]} {parts[1].split(',')[0]}", "level": level, "msg": rest}


def read_logs(logs_dir: Path, limit: int = 500) -> list[dict]:
    """读当前 agent.log 的尾部（倒序返回）。"""
    path = logs_dir / "agent.log"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        parsed = parse_log_line(line)
        if parsed:
            out.append(parsed)
        if len(out) >= limit:
            break
    return out


def format_bytes(n: float) -> str:
    gb = n / (1024**3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    mb = n / (1024**2)
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{n / 1024:.1f} KB"


def format_duration(seconds: float) -> str:
    """把秒数说成人话，例如 `14 天 6 小时`、`2 小时 10 分`。"""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分"


def friendly_level(level: str) -> str:
    return {"INFO": "信息", "WARNING": "警告", "ERROR": "错误",
            "CRITICAL": "严重", "DEBUG": "调试"}.get(level, level)


VIDEO_EXTS = {".ts", ".mp4", ".mkv", ".mov", ".avi", ".flv", ".m2ts"}


def collect_files(paths: list[Path]) -> list[Path]:
    """文件/文件夹展开成视频文件列表（文件夹递归）。"""
    out: list[Path] = []
    for p in paths:
        if p.is_file():
            if p.suffix.lower() in VIDEO_EXTS:
                out.append(p)
        elif p.is_dir():
            out.extend(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXTS)
    return out
