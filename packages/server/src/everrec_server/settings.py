"""服务端设置，全部从环境变量读取（systemd 单元里用 EnvironmentFile 配）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _webhook_headers() -> dict[str, str]:
    raw = os.environ.get("EVERREC_WEBHOOK_HEADERS", "")
    if not raw:
        return {}
    try:
        return dict(json.loads(raw))
    except json.JSONDecodeError:
        return {}


@dataclass(frozen=True)
class Settings:
    # 正式部署用 PostgreSQL：postgresql+psycopg://everrec@/everrec
    database_url: str = field(
        default_factory=lambda: os.environ.get("EVERREC_DATABASE_URL", "sqlite:///./everrec.db")
    )
    lost_after_seconds: int = field(
        default_factory=lambda: int(os.environ.get("EVERREC_LOST_AFTER", "120"))
    )
    sweep_seconds: int = field(
        default_factory=lambda: int(os.environ.get("EVERREC_SWEEP_SECONDS", "15"))
    )
    # 录像根目录；上传的分段归档到 {目录}/{位置编号}/{yyyy}/{MM}/{dd}/
    recordings_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("EVERREC_RECORDINGS_DIR", "./recordings"))
    )
    # 同一时刻最多放行多少台设备补传（设计文档 5.5 措施 3）
    backfill_limit: int = field(
        default_factory=lambda: int(os.environ.get("EVERREC_BACKFILL_LIMIT", "5"))
    )
    # 大批同时失联判定为服务器或网络故障（设计文档 6.3）
    mass_lost_ratio: float = 0.8
    # 事件推送（设计文档 8.3）：第一版一个地址，用环境变量；WP4 再做设置页
    webhook_url: str = field(default_factory=lambda: os.environ.get("EVERREC_WEBHOOK_URL", ""))
    webhook_secret: str = field(default_factory=lambda: os.environ.get("EVERREC_WEBHOOK_SECRET", ""))
    webhook_headers: dict[str, str] = field(default_factory=_webhook_headers)
    web_dist: Path | None = field(
        default_factory=lambda: Path(p) if (p := os.environ.get("EVERREC_WEB_DIST")) else None
    )
