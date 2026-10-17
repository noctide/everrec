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
    # 滚动覆盖：保留天数、开关、磁盘阈值（设计文档 5.4；第一版全局一套，界面在 WP4）
    retention_days: int = field(
        default_factory=lambda: int(os.environ.get("EVERREC_RETENTION_DAYS", "90"))
    )
    rollover_enabled: bool = field(
        default_factory=lambda: os.environ.get("EVERREC_ROLLOVER_ENABLED", "1").lower()
        not in ("0", "false", "no", "off")
    )
    disk_warn_threshold: float = 0.85
    disk_critical_threshold: float = 0.95
    retention_seconds: int = field(
        default_factory=lambda: int(os.environ.get("EVERREC_RETENTION_SECONDS", "3600"))
    )
    web_dist: Path | None = field(
        default_factory=lambda: Path(p) if (p := os.environ.get("EVERREC_WEB_DIST")) else None
    )
