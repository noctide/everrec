"""画质：分辨率 + 帧率，码率自动算（取代设计文档 4.4 的三个固定档位）。

界面上（管理网站设置页，见 docs/tasks.md WP4）：
- 默认只显示一条"画质"滑条，5 个挡，推荐挡是"标准"（720P / 10 帧）。
- "高级"里分别调分辨率和帧率，只能停在下面列出的挡位上，不能连续取值。
- 码率不对任何人开放：按"每个像素每帧分多少数据"自动算，分辨率或帧率变了码率跟着变，
  避免"分辨率调高、码率没跟上，画面反而更糊"。

这里是挡位和码率的唯一来源，服务端、客户端、管理网站都从这里取，不要在别处另写一份。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Resolution(StrEnum):
    P360 = "360p"
    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"
    # 不做 4K：操作区电脑 CPU、存储、网络都扛不住，普通 USB 摄像头也出不了（见 AGENTS.md 第 2 节）


RESOLUTION_SIZE: dict[Resolution, tuple[int, int]] = {
    Resolution.P360: (640, 360),
    Resolution.P480: (854, 480),
    Resolution.P720: (1280, 720),
    Resolution.P1080: (1920, 1080),
}

FPS_STOPS: tuple[int, ...] = (5, 8, 10, 12, 15)

RECOMMENDED_RESOLUTION = Resolution.P720
RECOMMENDED_FPS = 10

# 每个像素每帧分到的比特数。监控画面大多静止，0.07 能让 720P/10 帧的人和动作清楚，
# 这个值是码率上限的依据；实际码率由 CRF 决定，画面不动时远低于上限。
BITS_PER_PIXEL = 0.07
CRF = 28

# 屏幕源（Windows 桌面，tasks.md WP12）：默认 1080P / 8 帧；文字和表格比人脸更需要细节，
# 码率系数比摄像头高（0.10）。
SCREEN_DEFAULT_RESOLUTION = Resolution.P1080
SCREEN_DEFAULT_FPS = 8
SCREEN_BITS_PER_PIXEL = 0.10


class Quality(StrEnum):
    SAVER = "saver"        # 省空间
    LEAN = "lean"          # 偏省
    STANDARD = "standard"  # 标准（推荐）
    SHARP = "sharp"        # 偏清晰
    MAX = "max"            # 清晰


QUALITY_LABEL: dict[Quality, str] = {
    Quality.SAVER: "省空间",
    Quality.LEAN: "偏省",
    Quality.STANDARD: "标准",
    Quality.SHARP: "偏清晰",
    Quality.MAX: "清晰",
}

QUALITY_STOPS: dict[Quality, tuple[Resolution, int]] = {
    Quality.SAVER: (Resolution.P360, 5),
    Quality.LEAN: (Resolution.P480, 8),
    Quality.STANDARD: (Resolution.P720, 10),
    Quality.SHARP: (Resolution.P720, 15),
    Quality.MAX: (Resolution.P1080, 10),
}

DEFAULT_QUALITY = Quality.STANDARD

# 旧配置文件里的 profile 名，自动换成新挡位，免得已经装好的电脑升级后读不懂配置
LEGACY_PROFILE = {"xlow": Quality.SAVER, "low": Quality.LEAN, "mid": Quality.STANDARD}


@dataclass(frozen=True)
class Encoding:
    resolution: Resolution
    fps: int
    bits_per_pixel: float = BITS_PER_PIXEL  # 摄像头 0.07，屏幕源 0.10

    @property
    def width(self) -> int:
        return RESOLUTION_SIZE[self.resolution][0]

    @property
    def height(self) -> int:
        return RESOLUTION_SIZE[self.resolution][1]

    @property
    def maxrate_kbps(self) -> int:
        return max(32, round(self.width * self.height * self.fps * self.bits_per_pixel / 1000))

    @property
    def bufsize_kbps(self) -> int:
        return self.maxrate_kbps * 2

    @property
    def crf(self) -> int:
        return CRF

    @property
    def mb_per_hour_max(self) -> float:
        """每路每小时的上限（MB）。设置页用它算"能存几天"。"""
        return self.maxrate_kbps * 3600 / 8 / 1000

    @property
    def label(self) -> str:
        """给人看的写法，例如 720P / 10 帧。"""
        return f"{self.resolution.upper()} / {self.fps} 帧"


def resolve(
    quality: Quality | str | None = None,
    resolution: Resolution | str | None = None,
    fps: int | None = None,
) -> Encoding:
    """画质挡 + 可选的高级覆盖 → 最终编码参数。高级里填了哪个就覆盖哪个。"""
    q = Quality(quality) if quality else DEFAULT_QUALITY
    base_res, base_fps = QUALITY_STOPS[q]
    res = Resolution(resolution) if resolution else base_res
    f = fps if fps else base_fps
    if f not in FPS_STOPS:
        raise ValueError(f"帧率只能是 {FPS_STOPS} 之一")
    return Encoding(res, f)


def quality_table() -> list[dict]:
    """给设置页和文档用的挡位表。"""
    rows = []
    for q, (res, f) in QUALITY_STOPS.items():
        e = Encoding(res, f)
        rows.append({
            "quality": q.value, "label": QUALITY_LABEL[q], "recommended": q is DEFAULT_QUALITY,
            "resolution": res.value, "fps": f, "maxrate_kbps": e.maxrate_kbps,
            "mb_per_hour_max": round(e.mb_per_hour_max),
        })
    return rows
