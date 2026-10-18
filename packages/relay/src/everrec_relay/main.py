"""EverRec 子公司中转节点（tasks.md WP10）。

南向：复用主服务器 create_app，对子公司 Agent 提供与主服务器完全相同的
 /agent/v1/* 接口，录像存本地并按保留天数滚动覆盖，自己判定子公司设备失联。
北向：对主服务器提供 /relay/v1/pull（见 pull.py），用独立密钥 + HTTPS。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from everrec_server.main import create_app as create_server_app

from . import __version__
from .settings import RelaySettings

log = logging.getLogger("everrec.relay")


def create_relay_app(settings: RelaySettings | None = None) -> FastAPI:
    settings = settings or RelaySettings()
    # 南向：整套服务端逻辑（心跳、上传、保留、失联判定）原样复用
    app = create_server_app(settings.server)
    app.title = "EverRec 中转节点"
    app.version = __version__
    app.state.relay_settings = settings
    # 北向 /relay/v1/pull（Phase 2 接入，见 pull.mount_pull）
    from .pull import mount_pull

    mount_pull(app, settings)
    return app


def run() -> None:
    import argparse

    import uvicorn

    p = argparse.ArgumentParser(prog="everrec-relay")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8081)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_relay_app(), host=args.host, port=args.port)
