"""服务端设置，全部从环境变量读取（systemd 单元里用 EnvironmentFile 配）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


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
    # 大批同时失联判定为服务器或网络故障（设计文档 6.3）
    mass_lost_ratio: float = 0.8
    web_dist: Path | None = field(
        default_factory=lambda: Path(p) if (p := os.environ.get("EVERREC_WEB_DIST")) else None
    )
