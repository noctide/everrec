"""桌面端共用的纯逻辑（与 Qt 解耦，便于单元测试）。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 主题色：冷白底 + 语义状态色（与 web/src/styles.css 的 :root 同一套值）
# canvas 是页面底，bg 是内容面（卡片、输入框）。两者分开，卡片才有层次。
COLORS = {
    "canvas": "#F6F8FA", "bg": "#FFFFFF", "bar": "#EEF2F6",
    "line": "#E1E7EF", "line2": "#C9D3DF",
    "text": "#3D4B5C", "muted": "#64748B", "strong": "#0D1520", "disabled": "#9AA7B6",
    "accent": "#0D1520", "accent_hover": "#26313C", "accent_pressed": "#05080D",
    "focus": "#1D4ED8", "thumb": "#26313C", "note": "#9AA7B6",
}


# 状态色：四个级别，每级给"文字 / 浅底 / 描边"三个值，对比度都过 WCAG AA。
# 绿 = 正常，蓝 = 进行中，琥珀 = 要留意，红 = 出问题。网页端用同一组。
LEVEL_COLORS = {
    "ok": {"fg": "#0B7A50", "bg": "#ECFDF3", "line": "#BFE6D2"},
    "info": {"fg": "#1E5FBF", "bg": "#E8F0FE", "line": "#A9C4F5"},
    "warn": {"fg": "#B45309", "bg": "#FFFBEB", "line": "#E7D3AC"},
    "error": {"fg": "#C62828", "bg": "#FEF2F2", "line": "#EDC2C2"},
    "idle": {"fg": "#64748B", "bg": "#EEF2F6", "line": "#E1E7EF"},
}

CHECK_LABEL = {"service": "录制服务", "device": "摄像头", "process": "录制进程",
               "dataflow": "录像文件在增长", "storage": "存储"}


def overall_state(st: dict) -> dict:
    """把 status.json 归纳成一个整体状态：level / title / detail（给人看的大白话）。"""
    if not st or st.get("stale"):
        return {"level": "error", "title": "录制服务无响应",
                "detail": "没有收到录制服务的状态，请联系设备管理员"}
    if not st.get("recording"):
        if st.get("starting"):
            return {"level": "info", "title": "正在启动", "detail": "摄像头正在准备，请稍候"}
        return {"level": "error", "title": "没在录像",
                "detail": "请检查摄像头是否插好，或联系设备管理员"}
    bad = [CHECK_LABEL[k] for k, v in (st.get("checks") or {}).items()
           if v != "ok" and k in CHECK_LABEL]
    if bad:
        return {"level": "warn", "title": "正在录像 · 有异常",
                "detail": "需要留意：" + "、".join(bad)}
    online = st.get("online")
    if online is False:
        return {"level": "warn", "title": "正在录像 · 本机独立录制",
                "detail": "暂时连不上服务器，录像先存在本机，连上后自动补传"}
    if st.get("problems"):
        return {"level": "warn", "title": "正在录像 · 配置有提醒",
                "detail": str(st["problems"][0])}
    if online is None:
        return {"level": "ok", "title": "正在录像", "detail": "本机独立录像（没有连接服务器）"}
    pending = int(st.get("pending_sync", 0))
    detail = f"已连接服务器，还有 {pending} 段录像等待上传" if pending else "已连接服务器，录像自动上传"
    return {"level": "ok", "title": "正在录像", "detail": detail}


def recording_duration(state_dir: Path, now: datetime | None = None) -> str:
    """今天已连续录了多久（取今天第一段录像的开始时间）。"""
    bars = build_timeline(state_dir, now)
    if not bars:
        return "—"
    now = now or datetime.now()
    return format_duration((now.hour + now.minute / 60 - bars[0].start) * 3600)


def read_status(state_dir: Path, now: datetime | None = None, max_age: float = 15) -> dict:
    """读本机状态；过期时取消在录标记，没有或坏了就返回空 dict。"""
    path = state_dir / "status.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        sent = datetime.fromisoformat(data["sent_at"]).astimezone()
        age = ((now or datetime.now().astimezone()).astimezone() - sent).total_seconds()
        data["stale"] = age < -max_age or age > max_age
        if data["stale"]:
            data.update(recording=False, online=False, starting=False)
            data["checks"] = {**data.get("checks", {}), "service": "fail"}
        return data
    except (ValueError, KeyError, TypeError, OSError):
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
        # 按索引里记的实际时长画（分段默认 1 小时，也能被服务器改）。
        # 旧索引没有 duration，那时的分段固定 5 分钟，回退到 5 分钟是对的。
        seconds = float(e.get("duration") or 0) or 300.0
        bars.append(TimelineBar(start=hour, end=min(24.0, hour + seconds / 3600),
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


# ---------- 保存当前画面（截图） ----------

SNAPSHOT_DIR = "screenshots"

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def snapshot_path(dest_dir: Path, code: str, now: datetime | None = None) -> Path:
    """截图存哪、叫什么：`编号_年月日_时分秒.jpg`，同一秒重复保存加 -2、-3。

    编号可能来自容错后的配置，未必都是英文/数字/连字符，进文件名前先过滤非法字符。
    """
    now = now or datetime.now()
    safe = _SAFE_CHARS.sub("_", code.strip()) or "unknown"
    base = dest_dir / f"{safe}_{now:%Y%m%d_%H%M%S}.jpg"
    if not base.exists():
        return base
    i = 2
    while True:
        cand = dest_dir / f"{safe}_{now:%Y%m%d_%H%M%S}-{i}.jpg"
        if not cand.exists():
            return cand
        i += 1


# ---------- 设置：直接改配置文件，不需要管理员登录 ----------

SETTING_KEYS = ("dept", "section", "area", "site", "spot", "number")

# 位置层级：每一级都只能从配置文件里选，不能随手填。来源（优先级从高到低）：
#   1. state/location_options.json —— 连着服务器时，Agent 把服务器下发的选项缓存在这里；
#   2. config/location_options.json —— 本机的配置文件，没有服务器时用；不存在就按下面的默认生成，
#      要改选项就直接改这个文件。
# 文件格式有两种都认：
#   服务器格式  {"dept": ["FIN"], "section": ["AP"], "area": [...], "site": [...], "spot": [...]}
#   完整格式    {"levels": [{"key": "dept", "label": "部门",
#                          "options": ["HD", {"value": "HN", "label": "华南"}]}]}
# 选项的 value 进位置编号（只能英文、数字、连字符）；label 只是给人看的名字。
LEVEL_LABELS = {
    "dept": "部门", "section": "科室", "area": "区域", "site": "属地", "spot": "位置", "number": "编号",
}

# 没有任何配置文件时的默认选项（示例值，现场按实际情况改 config/location_options.json）
DEFAULT_LEVELS = [
    {"key": "dept", "label": "部门", "options": ["HD", "HN"]},
    {"key": "section", "label": "科室", "options": ["PD", "XH"]},
    {"key": "area", "label": "区域", "options": ["A", "B"]},
    {"key": "site", "label": "属地", "options": ["HQ", "BR"]},
    {"key": "spot", "label": "位置", "options": ["1F", "2F", "3F"]},
    {"key": "number", "label": "编号", "options": ["001", "002", "003", "004", "005"]},
]
LOCATION_FILE = "location_options.json"


def _norm_options(options) -> list[dict]:
    out = []
    for o in options or []:
        o = o if isinstance(o, dict) else {"value": str(o), "label": str(o)}
        if o.get("value"):
            out.append({"value": str(o["value"]), "label": str(o.get("label") or o["value"])})
    return out


def _read_levels(path: Path) -> list[dict]:
    """读一个位置选项文件，两种格式都认；读不了或没有任何选项返回空列表。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if "levels" in raw:
            levels = [{"key": str(lv["key"]), "label": str(lv.get("label") or lv["key"]),
                       "options": _norm_options(lv.get("options"))} for lv in raw["levels"]]
        else:
            levels = [{"key": k, "label": LEVEL_LABELS.get(k, k), "options": _norm_options(v)}
                      for k, v in raw.items() if isinstance(v, list)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    return levels if any(lv["options"] for lv in levels) else []


def ensure_location_file(config_dir: Path) -> Path:
    """本机还没有位置选项文件就按默认生成一份，方便现场直接改；写不了也不影响使用。"""
    path = Path(config_dir) / LOCATION_FILE
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"levels": DEFAULT_LEVELS}, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError:
            pass
    return path


def load_location_levels(state_dir: Path, config_dir: Path | None = None) -> list[dict]:
    """位置各级和每级可选项：服务器下发的优先，其次本机配置文件，再其次默认。选项统一成 {value, label}。"""
    local = _read_levels(Path(config_dir) / LOCATION_FILE) if config_dir else []
    local = local or [{**lv, "options": _norm_options(lv["options"])} for lv in DEFAULT_LEVELS]
    server = _read_levels(Path(state_dir) / LOCATION_FILE)
    if not server:
        return local
    # 服务器只管它给出的各级；它没给的级（比如"编号"）沿用本机配置里的
    server = [lv for lv in server if lv["options"]]
    have = {lv["key"] for lv in server}
    return server + [lv for lv in local if lv["key"] not in have]


def default_install_dir() -> Path:
    """没指定安装目录时用哪个：打包后的客户端就是 exe 所在的文件夹（视频、缓存、日志、配置都放在里面，
    整个文件夹拷给别人就能用）；开发时沿用 D:/EverRec（Windows）或 ~/everrec。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path("D:/EverRec") if sys.platform == "win32" else Path.home() / "everrec"


def needs_setup(config_path: Path) -> bool:
    """还没配置过（没有配置文件，或位置一项都没填）→ 打开客户端时先进设置界面。"""
    from everrec_core.config import load_config

    if not Path(config_path).exists():
        return True
    return load_config(config_path).code == "UNASSIGNED"


def apply_settings(config_path: Path, values: dict) -> list[str]:
    """把设置写进配置文件，返回给人看的提示（写错的项回到默认值）。

    values 的键：dept/section/area/site/spot/number、server、camera_type、quality、folder、autostart、
    screenshot_enabled、screenshot_interval_seconds。
    文件不存在就新建；没有写入权限时抛 OSError，由界面提示。
    """
    from everrec_core.config import load_config, save_config, validate_tolerant

    config_path = Path(config_path)
    cfg = load_config(config_path) if config_path.exists() else None
    data = (
        cfg.model_dump(mode="json", exclude={"install_dir", "config_warnings", "config_path"})
        if cfg else {}
    )
    loc = data.setdefault("location", {})
    for k in SETTING_KEYS:
        if k in values:
            loc[k] = str(values[k]).strip()
    if "server" in values:
        data["server"] = str(values["server"]).strip()
    cam = data.setdefault("camera", {})
    if values.get("camera_type"):
        cam["type"] = str(values["camera_type"])  # usb / screen（录屏，tasks.md WP12）
    rec = data.setdefault("recording", {})
    if "quality" in values:
        rec["quality"] = values["quality"]
        rec["resolution"] = None  # 选了画质挡就不再沿用高级里的单项覆盖
        rec["fps"] = None
    if "folder" in values and str(values["folder"]).strip():
        rec["folder"] = str(values["folder"]).strip()
    if "autostart" in values:
        data["autostart"] = bool(values["autostart"])
    shot = data.setdefault("screenshot", {})
    if "screenshot_enabled" in values:
        shot["enabled"] = bool(values["screenshot_enabled"])
    if "screenshot_interval_seconds" in values and values["screenshot_interval_seconds"]:
        shot["interval_seconds"] = int(values["screenshot_interval_seconds"])
    new_cfg, warnings = validate_tolerant(data)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    save_config(new_cfg, config_path)
    return warnings


def open_path(path: Path) -> None:
    """用系统默认方式打开文件或文件夹（Windows 资源管理器 / 记事本）。"""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


def open_url(url: str) -> None:
    """用默认浏览器打开网址（打开管理网站）。"""
    if sys.platform == "win32":
        os.startfile(url)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", url])  # noqa: S603, S607
