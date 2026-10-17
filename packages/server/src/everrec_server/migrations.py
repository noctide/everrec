"""用代码内嵌的 Alembic 配置跑迁移，取代骨架阶段的 create_all（设计文档第 11 节）。

Alembic 的脚本目录就放在包内（alembic/），随 wheel 一起分发，不依赖外部 alembic.ini。
`upgrade head` 幂等，服务端启动时跑一次即可；测试也走同一条路。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

_ALEMBIC_DIR = Path(__file__).parent / "alembic"


def run_migrations(url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
