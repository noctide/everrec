"""Agent 的 HTTP 客户端（net.py）：不读系统代理环境变量。

演示时踩到的坑：这台机器的 NO_PROXY 里裸写了 ::1 和 [::1]，httpx 解析代理配置直接抛
InvalidURL，Agent 连启动都起不来、录像都不开始。Agent 连的是内网服务器，本来也不需要代理。
"""

from __future__ import annotations

import httpx
import pytest

from everrec_agent import net

BROKEN_NO_PROXY = "127.0.0.1,localhost,::1,[::1]"  # 实测会让 httpx 抛 InvalidURL


def test_client_ignores_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("NO_PROXY", BROKEN_NO_PROXY)
    with net.client() as c:
        assert c._trust_env is False, "Agent 的客户端不能读系统代理设置"


def test_request_goes_direct_with_broken_proxy_env(monkeypatch):
    """畸形 NO_PROXY 下请求也要真的发出去：127.0.0.1:9 没人监听，预期是连接错误，
    而不是建客户端时抛 InvalidURL（回归时会在这里炸）。"""
    monkeypatch.setenv("NO_PROXY", BROKEN_NO_PROXY)
    with pytest.raises(httpx.HTTPError):
        net.post("http://127.0.0.1:9/agent/v1/heartbeat", content=b"{}", timeout=1)
