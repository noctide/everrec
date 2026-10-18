"""服务端设置与下发（设计文档 7、tasks.md WP4）。

设置分全局一套 + 按位置编号/前缀覆盖，合并后随心跳下发；配置版本号单调递增。
可下发项：画质(quality/resolution/fps)、水印、本地配额、分段时长、上传限速。
不可下发：录像目录、服务器地址、开机自启（AGENTS.md 原则 5）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from everrec_core.profiles import FPS_STOPS, Quality, Resolution

from . import org
from .db import Meta, Setting

# 允许的服务端设置键（码率不开放，不出现在任何设置里）
_ALLOWED_KEYS = {
    "quality", "resolution", "fps", "watermark",
    "segment_seconds", "local_quota_gb",
    "upload_realtime_bps", "upload_backfill_bps",
    "retention_days", "rollover_enabled",
    "update_mode", "update_window_start", "update_window_end",
}

# 随心跳下发给 Agent 的键；其余（保留天数、滚动覆盖、补传名额、更新策略）是服务端自己用的
AGENT_KEYS = {
    "quality", "resolution", "fps", "watermark",
    "segment_seconds", "local_quota_gb",
    "upload_realtime_bps", "upload_backfill_bps",
}



def validate_settings(data: dict) -> dict:
    """校验设置，只接受 profiles 里定义的取值，其他一律拒绝（tasks.md WP4 验收）。"""
    out: dict = {}
    for key, value in data.items():
        if key not in _ALLOWED_KEYS:
            raise ValueError(f"不支持的设置项：{key}")
        out[key] = _validate_one(key, value)
    return out


def _validate_one(key: str, value):
    if key == "quality":
        return Quality(value).value
    if key == "resolution":
        return None if value is None else Resolution(value).value
    if key == "fps":
        if value is None:
            return None
        if int(value) not in FPS_STOPS:
            raise ValueError(f"帧率只能是 {FPS_STOPS} 之一")
        return int(value)
    if key in ("watermark", "rollover_enabled"):
        if not isinstance(value, bool):
            raise ValueError(f"{key} 只能是布尔值")
        return value
    if key in ("upload_realtime_bps", "upload_backfill_bps", "retention_days"):
        v = int(value)
        if v <= 0:
            raise ValueError(f"{key} 必须大于 0")
        return v
    if key == "segment_seconds":
        v = int(value)
        if not 2 <= v <= 3600:
            raise ValueError("segment_seconds 必须在 2 到 3600 秒之间")
        return v
    if key == "local_quota_gb":
        v = float(value)
        if v <= 0:
            raise ValueError("local_quota_gb 必须大于 0")
        return v
    if key == "update_mode":
        if value not in ("auto", "notify"):
            raise ValueError("update_mode 只能是 auto 或 notify")
        return value
    if key in ("update_window_start", "update_window_end"):
        v = int(value)
        if not 0 <= v <= 23:
            raise ValueError(f"{key} 必须是 0 到 23 的小时数")
        return v
    raise ValueError(f"不支持的设置项：{key}")


def effective_settings(s: Session, code: str) -> dict:
    """全局 + 编号前缀覆盖 + 单设备覆盖，合并后的最终配置。"""
    data: dict = {}
    global_row = s.scalar(select(Setting).where(Setting.scope == "global"))
    if global_row:
        data.update(global_row.data)
    # 前缀覆盖：越具体（越长）越后应用，覆盖全局
    prefix_rows = s.scalars(select(Setting).where(Setting.scope.like("prefix:%"))).all()
    for row in sorted(prefix_rows, key=lambda r: len(r.scope)):
        prefix = row.scope[len("prefix:"):]
        if code.startswith(prefix):
            data.update(row.data)
    dev = s.scalar(select(Setting).where(Setting.scope == f"device:{code}"))
    if dev:
        data.update(dev.data)
    return data


def agent_settings(full: dict) -> dict:
    """只挑下发给 Agent 的键。"""
    return {k: v for k, v in full.items() if k in AGENT_KEYS}


def get_config_version(s: Session) -> int:
    row = s.get(Meta, "config_version")
    return int(row.value) if row else 0


def bump_config_version(s: Session) -> int:
    row = s.get(Meta, "config_version")
    v = int(row.value) + 1 if row else 1
    if row:
        row.value = str(v)
    else:
        s.add(Meta(key="config_version", value=str(v)))
    return v


def location_options(s: Session) -> dict[str, list[str]]:
    """各层级（部门/科室/区域/属地/位置）已有的可选值，来自组织树。"""
    return org.flat_options(s)
