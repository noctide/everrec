"""服务端设置，全部从环境变量读取（systemd 单元里用 EnvironmentFile 配）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_relays() -> dict[str, str]:
    """解析 EVERREC_RELAYS，形如 "sub1=http://relay1:8081,sub2=http://relay2:8081"。"""
    raw = os.environ.get("EVERREC_RELAYS", "")
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, url = part.split("=", 1)
        name, url = name.strip(), url.strip()
        if name and url:
            out[name] = url
    return out


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
    # 认证（tasks.md WP5）：管理员密码、只读 API 令牌、会话签名密钥
    admin_password: str = field(
        default_factory=lambda: os.environ.get("EVERREC_ADMIN_PASSWORD", "")
    )
    api_token: str = field(
        default_factory=lambda: os.environ.get("EVERREC_API_TOKEN", "")
    )
    secret_key: str = field(
        default_factory=lambda: os.environ.get("EVERREC_SECRET_KEY", "")
    )
    # 中转节点（tasks.md WP10）：要拉取的中转节点、北向密钥、回放分段缓存上限
    relays: dict[str, str] = field(default_factory=_parse_relays)  # 名字 -> 基址
    relay_pull_token: str = field(
        default_factory=lambda: os.environ.get("EVERREC_RELAY_PULL_TOKEN", "")
    )
    relay_pull_seconds: int = field(
        default_factory=lambda: int(os.environ.get("EVERREC_RELAY_PULL_SECONDS", "30"))
    )
    relay_cache_max_mb: int = field(
        default_factory=lambda: int(os.environ.get("EVERREC_RELAY_CACHE_MB", "2048"))
    )
    # 自动更新（tasks.md WP9）：安装包存放目录、更新模式、更新时段
    releases_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("EVERREC_RELEASES_DIR", "./releases"))
    )
    web_dist: Path | None = field(
        default_factory=lambda: Path(p) if (p := os.environ.get("EVERREC_WEB_DIST")) else None
    )
