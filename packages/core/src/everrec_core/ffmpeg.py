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
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .config import AgentConfig, CameraType
from .profiles import RESOLUTION_SIZE, Encoding

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


@dataclass(frozen=True)
class CameraMode:
    """摄像头能输出的一种格式。codec 为 mjpeg 时用 -vcodec，否则 pixel_format 用 -pixel_format。"""

    codec: str | None
    pixel_format: str | None
    width: int
    height: int
    max_fps: float

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"


_DSHOW_MODE = re.compile(
    r"(?:vcodec=(?P<codec>\w+)|pixel_format=(?P<pf>\w+))\s+"
    r"min s=\d+x\d+ fps=[\d.]+\s+max s=(?P<w>\d+)x(?P<h>\d+) fps=(?P<fps>[\d.]+)"
)


def parse_dshow_modes(stderr: str) -> list[CameraMode]:
    """解析 `ffmpeg -f dshow -list_options true -i video=<名称>` 的输出。"""
    modes = {
        CameraMode(m["codec"], m["pf"], int(m["w"]), int(m["h"]), float(m["fps"]))
        for m in _DSHOW_MODE.finditer(stderr)
    }
    return sorted(modes, key=lambda x: (x.width * x.height, x.max_fps, x.codec or ""))


@cache
def list_camera_modes(device: str) -> tuple[CameraMode, ...]:
    """摄像头支持的输出格式（目前只在 Windows 上探测）。"""
    if sys.platform != "win32" or not device:
        return ()
    r = subprocess.run(
        [ffmpeg_binary(), "-hide_banner", "-f", "dshow", "-list_options", "true", "-i", f"video={device}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return tuple(parse_dshow_modes(r.stderr))


def choose_mode(modes: list[CameraMode] | tuple[CameraMode, ...], enc: Encoding) -> CameraMode | None:
    """挑一个摄像头格式去取流。

    - 有和目标一样大的就用它；多个时优先帧率够的，再优先 MJPEG（USB2 下 MJPEG 高分辨率帧率更高）。
    - 没有一样大的，就挑宽和高都够的里面最小的（缩小画面不损失清晰度；
      只看高度会选到 640x480 这种 4:3 的，录出来两侧有黑边）。
    - 摄像头最大也比目标小，就用最大的（见 effective_encoding，会把录制分辨率降下来，不做无意义的放大）。
    """
    if not modes:
        return None

    def rank(m: CameraMode):
        return (m.max_fps >= enc.fps, m.codec == "mjpeg", m.max_fps)

    same = [m for m in modes if (m.width, m.height) == (enc.width, enc.height)]
    if same:
        return max(same, key=rank)
    bigger = [m for m in modes if m.width >= enc.width and m.height >= enc.height]
    if bigger:
        smallest = min(m.width * m.height for m in bigger)
        return max((m for m in bigger if m.width * m.height == smallest), key=rank)
    largest = max(m.width * m.height for m in modes)
    return max((m for m in modes if m.width * m.height == largest), key=rank)


def camera_device(cfg: AgentConfig) -> str:
    if cfg.camera.type != CameraType.USB:
        return ""
    if sys.platform == "win32":
        return cfg.camera.device or next(iter(list_cameras()), "")
    return cfg.camera.device or "/dev/video0"


def effective_encoding(cfg: AgentConfig) -> tuple[Encoding, CameraMode | None, str | None]:
    """实际录制用的参数、取流格式，以及需要提醒的话（摄像头达不到设定分辨率时）。"""
    enc = cfg.recording.encoding
    modes = list_camera_modes(camera_device(cfg)) if cfg.camera.type == CameraType.USB else ()
    mode = choose_mode(modes, enc)
    if mode and mode.height < enc.height:
        fit = [r for r, (_w, h) in RESOLUTION_SIZE.items() if h <= mode.height]
        if fit:
            capped = Encoding(max(fit, key=lambda r: RESOLUTION_SIZE[r][1]), enc.fps)
            note = f"摄像头最高只有 {mode.size}，已按 {capped.label} 录制"
            return capped, mode, note
    return enc, mode, None


def spool_dir(cfg: AgentConfig) -> Path:
    """正在写的分段放这里；写完后归档到 {位置编号}/{yyyy}/{MM}/{dd}/。"""
    return cfg.recordings_dir / SPOOL_DIRNAME


def _input_args(cfg: AgentConfig, enc: Encoding, mode: CameraMode | None) -> list[str]:
    cam = cfg.camera
    if cam.type == CameraType.TEST:
        return ["-re", "-f", "lavfi", "-i", f"testsrc2=size={enc.width}x{enc.height}:rate={enc.fps}"]
    if cam.type == CameraType.USB:
        device = camera_device(cfg)
        if sys.platform == "win32":
            if not device:
                raise RuntimeError("没找到摄像头：设备管理器里有没有？隐私设置里是否允许桌面应用访问相机？")
            # 不指定格式时摄像头按自己的默认格式出图（常见 640x480），设成 720P 也只是放大
            fmt: list[str] = []
            if mode:
                fmt = ["-video_size", mode.size]
                fmt += ["-vcodec", "mjpeg"] if mode.codec == "mjpeg" else (
                    ["-pixel_format", mode.pixel_format] if mode.pixel_format else [])
            return ["-f", "dshow", "-rtbufsize", "128M", *fmt, "-i", f"video={device}"]
        return ["-f", "v4l2", "-i", device]
    raise ValueError(f"第一版不支持的摄像头类型：{cam.type}")


def _video_filter(cfg: AgentConfig, p: Encoding) -> str:
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
            f":x=8:y=8:fontsize={max(14, p.height // 28)}:fontcolor=white:box=1:boxcolor=black@0.5" + font
        )
    return ",".join(chain)


def build_record_command(cfg: AgentConfig, thumb_fps: str = "1/60") -> list[str]:
    p, mode, _note = effective_encoding(cfg)
    seg = cfg.recording.segment_seconds
    gop = min(10, seg)  # 关键帧间隔（秒）
    out = spool_dir(cfg) / f"{cfg.code}_%Y%m%d_%H%M%S{SEGMENT_EXT}"
    thumb = cfg.state_dir / "thumb.jpg"
    # 一路录像、一路缩略图（设计 6.2）：摄像头只能读一次，用 split 分流。
    # 录像那一路的参数保持不变；缩略图一路 fps 抽帧、缩到宽 320，持续覆盖同一文件。
    rec = _video_filter(cfg, p)
    fc = f"[0:v]split=2[rec][thumb];[rec]{rec}[recv];[thumb]fps={thumb_fps},scale=320:-2[thumbv]"
    return [
        ffmpeg_binary(),
        "-hide_banner", "-loglevel", "warning", "-y",
        *_input_args(cfg, p, mode),
        "-filter_complex", fc,
        "-map", "[recv]",
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
        "-map", "[thumbv]",
        "-c:v", "mjpeg",
        "-q:v", "5",
        "-f", "image2",
        "-update", "1",
        str(thumb),
    ]


def remux_to_mp4(src: Path, dst: Path) -> bool:
    """把 MPEG-TS 转封装成 MP4（不重新编码），加 faststart 让浏览器能边下边播。

    浏览器不能直接播 TS，上传完成后服务端用这个转封装（设计文档 12、tasks.md WP3）。
    失败返回 False，调用方保留原 TS 供修复。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [ffmpeg_binary(), "-hide_banner", "-nostdin", "-v", "error", "-y",
         "-i", str(src), "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", str(dst)],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
