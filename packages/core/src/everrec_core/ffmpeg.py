"""拼 FFmpeg 录制命令（设计文档 4.3、4.4、5.5）。

要点：
- 录成 MPEG-TS 分段，断电只损失最后几秒；分段结束后再归档（见 segments.py）。
- 用分段器的 segment_atclocktime + segment_clocktime_offset 实现错峰切段，
  每台机器按位置编号得到固定偏移，35 台不会在同一秒切段、同一秒上传。
- 关键帧间隔不超过 10 秒，并与分段时长对齐，切段时不会产生残缺 GOP。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

from .config import AgentConfig, CameraType
from .profiles import get_profile

SPOOL_DIRNAME = "_spool"
SEGMENT_EXT = ".ts"


def ffmpeg_binary() -> str:
    """优先用安装包自带的 ffmpeg（与程序同目录的 bin/），否则用 PATH 里的。"""
    bundled = Path(sys.executable).parent / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if bundled.exists():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if not found:
        raise FileNotFoundError("找不到 ffmpeg，请随安装包分发或加入 PATH")
    return found


_DSHOW_VIDEO = re.compile(r'"(?P<name>[^"]+)"\s*\(video\)')


def parse_dshow_devices(stderr: str) -> list[str]:
    """从 `ffmpeg -list_devices true -f dshow -i dummy` 的输出里取视频设备名。

    兼容两种格式：新版每行带 (video)；旧版先出现 "DirectShow video devices" 标题，再逐行列出。
    """
    names = [m["name"] for m in _DSHOW_VIDEO.finditer(stderr)]
    if names:
        return names
    out, in_video = [], False
    for line in stderr.splitlines():
        low = line.lower()
        if "directshow video devices" in low:
            in_video = True
        elif "directshow audio devices" in low:
            in_video = False
        elif in_video and "alternative name" not in low and (m := re.search(r'"([^"]+)"', line)):
            out.append(m.group(1))
    return out


@cache
def list_cameras() -> list[str]:
    """本机的摄像头名称（Windows dshow）或设备路径（Linux v4l2）。"""
    if sys.platform == "win32":
        r = subprocess.run(
            [ffmpeg_binary(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return parse_dshow_devices(r.stderr)
    return sorted(str(p) for p in Path("/dev").glob("video*"))


def spool_dir(cfg: AgentConfig) -> Path:
    """正在写的分段放这里；写完后归档到 {位置编号}/{yyyy}/{MM}/{dd}/。"""
    return cfg.recordings_dir / SPOOL_DIRNAME


def _input_args(cfg: AgentConfig) -> list[str]:
    p = get_profile(cfg.recording.profile)
    cam = cfg.camera
    if cam.type == CameraType.TEST:
        return ["-re", "-f", "lavfi", "-i", f"testsrc2=size={p.width}x{p.height}:rate={p.fps}"]
    if cam.type == CameraType.USB:
        if sys.platform == "win32":
            name = cam.device or next(iter(list_cameras()), "")
            if not name:
                raise RuntimeError("没找到摄像头：设备管理器里有没有？隐私设置里是否允许桌面应用访问相机？")
            return ["-f", "dshow", "-rtbufsize", "64M", "-i", f"video={name}"]
        return ["-f", "v4l2", "-i", cam.device or "/dev/video0"]
    raise ValueError(f"第一版不支持的摄像头类型：{cam.type}")


def _video_filter(cfg: AgentConfig) -> str:
    p = get_profile(cfg.recording.profile)
    chain = [
        f"scale={p.width}:{p.height}:force_original_aspect_ratio=decrease",
        f"pad={p.width}:{p.height}:(ow-iw)/2:(oh-ih)/2",
        f"fps={p.fps}",
    ]
    if cfg.recording.watermark:
        # 盘符冒号既要加引号又要转义：Windows 实测只有这种写法稳定（Linux 上测不出差别）
        font = r":fontfile='C\:/Windows/Fonts/arial.ttf'" if sys.platform == "win32" else ""
        # %{localtime} 默认格式就是 YYYY-MM-DD HH:MM:SS，免去冒号转义
        chain.append(
            "drawtext=text='" + cfg.code + "  %{localtime}'"
            ":x=8:y=8:fontsize=14:fontcolor=white:box=1:boxcolor=black@0.5" + font
        )
    return ",".join(chain)


def build_record_command(cfg: AgentConfig) -> list[str]:
    p = get_profile(cfg.recording.profile)
    seg = cfg.recording.segment_seconds
    gop = min(10, seg)  # 关键帧间隔（秒）
    out = spool_dir(cfg) / f"{cfg.code}_%Y%m%d_%H%M%S{SEGMENT_EXT}"
    return [
        ffmpeg_binary(),
        "-hide_banner", "-loglevel", "warning", "-y",
        *_input_args(cfg),
        "-vf", _video_filter(cfg),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-crf", str(p.crf), "-maxrate", f"{p.maxrate_kbps}k", "-bufsize", f"{p.bufsize_kbps}k",
        "-force_key_frames", f"expr:gte(t,n_forced*{gop})",
        "-f", "segment",
        "-segment_format", "mpegts",
        # 必须传给内层 TS 封装器：外层的 -flush_packets 不生效，文件会一直是 0 字节到切段为止，
        # 健康检查 ④（文件是否增长）就失去意义
        "-segment_format_options", "flush_packets=1",
        "-segment_time", str(seg),
        "-segment_atclocktime", "1",
        "-segment_clocktime_offset", str(cfg.stagger_seconds),
        "-reset_timestamps", "1",
        "-strftime", "1",
        str(out),
    ]
