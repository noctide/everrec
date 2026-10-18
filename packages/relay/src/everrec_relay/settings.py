"""中转节点设置（tasks.md WP10）。

南向完全复用主服务器的 Settings（数据库、录像目录、保留天数、认证……），
另加北向 /relay/v1/pull 的独立密钥。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from everrec_server.settings import Settings as ServerSettings


@dataclass(frozen=True)
class RelaySettings:
    server: ServerSettings = field(default_factory=ServerSettings)
    # 北向 /relay/v1/pull 的独立密钥，主服务器拉取时用 Bearer 带上（tasks.md WP10：独立密钥 + HTTPS）
    pull_token: str = field(
        default_factory=lambda: os.environ.get("EVERREC_RELAY_PULL_TOKEN", "")
    )
