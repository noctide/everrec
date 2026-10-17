"""客户端本地配置（设计文档 4.6）。

配置文件放在安装文件夹的 config/everrec.yaml。相对路径一律相对安装文件夹解析，
所以录像默认落在 <安装文件夹>/recordings。
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .profiles import DEFAULT_QUALITY, FPS_STOPS, LEGACY_PROFILE, Encoding, Quality, Resolution, resolve

CODE_PART = re.compile(r"^[A-Za-z0-9]+$")


class Location(BaseModel):
    state: str = Field(default="", description="州")
    district: str = Field(default="", description="区")
    building: str = Field(default="", description="楼栋")
    floor: str = Field(default="", description="楼层")
    number: str = Field(default="", description="编号")

    @field_validator("*")
    @classmethod
    def _only_ascii_code(cls, v: str) -> str:
        v = str(v).strip()
        if v and not CODE_PART.match(v):
            raise ValueError("位置字段只能用英文和数字（会进文件路径）")
        return v

    @property
    def code(self) -> str:
        """位置编号，例如 HD-PD-A-3F-012。空字段跳过。"""
        parts = [self.state, self.district, self.building, self.floor, self.number]
        return "-".join(p for p in parts if p) or "UNASSIGNED"


class CameraType(StrEnum):
    USB = "usb"
    TEST = "test"  # FFmpeg 内置测试画面，开发和自测用
    # 第二期：RTSP = "rtsp"; ONVIF = "onvif"; NVR = "nvr"


class Camera(BaseModel):
    type: CameraType = CameraType.USB
    device: str = Field(default="", description="dshow 设备名或 /dev/videoN；留空则自动选第一个")


class Recording(BaseModel):
    quality: Quality = Field(default=DEFAULT_QUALITY, description="画质挡：saver/lean/standard/sharp/max")
    resolution: Resolution | None = Field(
        default=None, description="高级：360p/480p/720p/1080p，填了就覆盖画质挡"
    )
    fps: int | None = Field(default=None, description=f"高级：帧率 {FPS_STOPS}，填了就覆盖画质挡")
    folder: str = "recordings"
    local_quota_gb: float = 20
    disk_usage_limit: float = 0.85
    watermark: bool = True
    segment_seconds: int = Field(default=300, ge=2, le=3600)

    @model_validator(mode="before")
    @classmethod
    def _legacy_profile(cls, data):
        """旧配置的 profile: xlow/low/mid 自动换成画质挡。"""
        if isinstance(data, dict) and "profile" in data:
            data = dict(data)
            old = str(data.pop("profile"))
            data.setdefault("quality", LEGACY_PROFILE.get(old, DEFAULT_QUALITY).value)
        return data

    @field_validator("fps")
    @classmethod
    def _fps_stop(cls, v: int | None) -> int | None:
        if v is not None and v not in FPS_STOPS:
            raise ValueError(f"帧率只能是 {FPS_STOPS} 之一")
        return v

    @property
    def encoding(self) -> Encoding:
        return resolve(self.quality, self.resolution, self.fps)


class AgentConfig(BaseModel):
    server: str = Field(default="", description="服务器或子公司中转节点地址；留空则纯本地运行")
    location: Location = Location()
    camera: Camera = Camera()
    recording: Recording = Recording()
    autostart: bool = True
    heartbeat_seconds: int = 30

    # 不写进文件，加载时填入
    install_dir: Path = Field(default=Path("."), exclude=True)
    config_warnings: list[str] = Field(default_factory=list, exclude=True)
    config_path: Path | None = Field(default=None, exclude=True)  # 配置文件路径，用于检测改动

    @property
    def code(self) -> str:
        return self.location.code

    @property
    def recordings_dir(self) -> Path:
        p = Path(self.recording.folder)
        return p if p.is_absolute() else self.install_dir / p

    @property
    def logs_dir(self) -> Path:
        return self.install_dir / "logs"

    @property
    def state_dir(self) -> Path:
        """缓存的服务器配置、待同步队列等运行状态。"""
        return self.install_dir / "state"

    @property
    def stagger_seconds(self) -> int:
        """错峰偏移（设计文档 5.5 措施 1）：按位置编号算一个固定值，落在 0..分段时长 之间。"""
        h = int(hashlib.sha256(self.code.encode()).hexdigest(), 16)
        return h % self.recording.segment_seconds


def _drop(data: dict, loc: tuple) -> bool:
    """按 pydantic 报错的位置删掉那一项，让它回到默认值。"""
    node = data
    for key in loc[:-1]:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    if isinstance(node, dict) and loc and loc[-1] in node:
        del node[loc[-1]]
        return True
    return False


def validate_tolerant(data: dict) -> tuple[AgentConfig, list[str]]:
    """配置写错不能导致停录：写错的项回到默认值，并给出给人看的提示。"""
    warnings: list[str] = []
    data = data if isinstance(data, dict) else {}
    for _ in range(50):
        try:
            return AgentConfig.model_validate(data), warnings
        except ValidationError as e:
            progressed = False
            for err in e.errors():
                loc = tuple(err["loc"])
                where = ".".join(str(x) for x in loc) or "（整个文件）"
                value = err.get("input")
                if _drop(data, loc) or (len(loc) > 1 and _drop(data, loc[:1])):
                    warnings.append(f"配置项 {where} 的值 {value!r} 无效，已改用默认值")
                    progressed = True
            if not progressed:
                break
    warnings.append("配置文件无法识别，已全部改用默认值")
    return AgentConfig(), warnings


def load_config(path: str | Path) -> AgentConfig:
    path = Path(path).resolve()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg, warnings = validate_tolerant(data)
    except yaml.YAMLError as e:
        cfg, warnings = AgentConfig(), [f"配置文件格式错误（{e.__class__.__name__}），已全部改用默认值"]
    cfg.config_warnings = warnings
    # config/everrec.yaml → 安装文件夹是它的上两级
    cfg.install_dir = path.parent.parent if path.parent.name == "config" else path.parent
    cfg.config_path = path
    return cfg


def save_config(cfg: AgentConfig, path: str | Path) -> None:
    data = cfg.model_dump(mode="json", exclude={"install_dir"})
    Path(path).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
