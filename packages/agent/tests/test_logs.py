"""日志：HTTP 请求行不该刷满现场日志和客户端"最近日志"（演示时 301 行里 244 行是它）。"""

from __future__ import annotations

import logging

from everrec_agent import logs


def test_httpx_info_is_silenced(tmp_path):
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    try:
        logs.setup_logging(tmp_path)
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

        logging.getLogger("agent").info("分段已归档：HD-A-012_102645.ts")
        logging.getLogger("httpx").info("HTTP Request: POST http://127.0.0.1/agent/v1/heartbeat")

        text = (tmp_path / "agent.log").read_text(encoding="utf-8")
        assert "分段已归档" in text, "业务日志照常记录"
        assert "HTTP Request" not in text, "HTTP 请求行不该进日志文件"
    finally:  # setup_logging 会给 root 挂 handler，用完收掉，别影响别的测试
        root.handlers[:] = old_handlers
        root.setLevel(old_level)
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.NOTSET)
