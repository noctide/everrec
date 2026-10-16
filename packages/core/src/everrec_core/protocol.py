"""Agent ↔ 服务器内部接口 /agent/v1 的消息格式（设计文档 6.2）。两端共用。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

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
    # 第一版：服务器下发的统一设置/单独覆盖；为空表示沿用本地
    settings: dict = {}
