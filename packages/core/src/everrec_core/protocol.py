"""Agent ↔ 服务器内部接口 /agent/v1 的消息格式（设计文档 6.2）。两端共用。"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from .health import Check


class Checks(BaseModel):
    service: Check = Check.OK      # ① 服务
    device: Check = Check.OK       # ② 摄像头
    process: Check = Check.OK      # ③ 录制进程
    dataflow: Check = Check.OK     # ④ 文件是否增长
    storage: Check = Check.OK      # ⑥ 存储
    # ⑤ 画面检测为第二期


class LogLine(BaseModel):
    at: datetime
    level: str
    message: str


class Heartbeat(BaseModel):
    code: str = Field(description="位置编号，例如 HD-PD-A-3F-012")
    location: dict[str, str] = Field(
        default={},
        description="六级位置 dept/section/area/site/spot/number 和编号名称 label；"
        "服务端据此把设备归到组织树。旧版 Agent 不带，设备归入“未归位”",
    )
    hostname: str
    agent_version: str
    sent_at: datetime
    recording: bool
    starting: bool = Field(default=False, description="刚（重新）启动录制，还在宽限期内，文件尚未写出数据")
    profile: str = Field(description="实际录制的画质，给人看的写法，例如 720P / 10 帧")
    camera_resolutions: list[str] = Field(
        default=[], description="摄像头支持的分辨率，例如 1280x720；设置页据此把达不到的挡位置灰"
    )
    current_segment: str | None = None
    checks: Checks = Checks()
    problems: list[str] = []
    # 不算异常、但确实没画面的原因（录屏时锁屏 / 没人登录）。有值时服务端不判异常（WP12）
    paused: str = ""
    restarts: int = 0
    pending_sync: int = 0
    disk_used: float = 0
    offline_since: datetime | None = Field(
        default=None,
        description="离线开始时间；重新连上后保留到离线期间的录像补传完为止（设备墙显示黄色）",
    )
    recent_logs: list[LogLine] = []


class HeartbeatReply(BaseModel):
    ok: bool = True
    config_version: int = 0
    # 服务器下发的合并后最终配置（设计文档 7）；为空表示沿用本地/缓存
    settings: dict = {}
    # 位置选项：各层级可选值（平铺），兼容用；联动下拉用下面的 location_tree
    location_options: dict = {}
    # 组织树 [{level, code, name, children:[...]}]，桌面端设置页逐级联动选择
    location_tree: list = []
    # 首次心跳时签发设备令牌；之后所有 /agent/v1/* 需带它（tasks.md WP5）
    token: str | None = None


# ---------- 上传与补传（设计文档 5.1、5.5，接口前缀 /agent/v1） ----------

# 位置编号只用英文、数字、连字符（会进文件路径）；上传接口据此防 ../ 越权
_CODE_RE = re.compile(r"^[A-Za-z0-9-]+$")


class UploadRequest(BaseModel):
    code: str
    filename: str
    started_at: datetime
    ended_at: datetime
    size: int
    sha256: str
    integrity: str  # ok / repaired / partial
    channel: str    # realtime / backfill
    # 这段画面的来源：camera = 摄像头，screen = 桌面录屏（tasks.md WP12）。
    # 旧版 Agent 不带这个字段，按摄像头算；第一版界面不按来源筛，只落库。
    source: str = "camera"

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if v not in ("camera", "screen"):
            raise ValueError("来源只能是 camera 或 screen")
        return v

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = v.strip()
        if not _CODE_RE.match(v):
            raise ValueError("位置编号只能用英文、数字、连字符")
        return v

    @model_validator(mode="after")
    def _filename(self) -> UploadRequest:
        # 文件名必须是 <编号>_HHmmss.ts，可带归档重名时加的数字后缀（segments.archive_segment）
        if not re.fullmatch(rf"{re.escape(self.code)}_\d{{6}}(_\d+)?\.ts", self.filename):
            raise ValueError("文件名不符合 编号_时分秒.ts 的格式")
        return self


class UploadInitReply(BaseModel):
    upload_id: str
    received: int = 0   # 服务器已收到的字节数，断点续传从这里开始
    done: bool = False  # 同 code+filename+sha256 已上传完成，无需再传


class UploadProgressReply(BaseModel):
    received: int


class UploadCompleteReply(BaseModel):
    recording_id: int


class BackfillSlotReply(BaseModel):
    token: str
    expires_at: datetime
