"""Agent 出的所有 HTTP 请求统一走这里：只连内网服务器，不读系统代理设置。

机器上的 HTTP_PROXY / NO_PROXY 写歪时（实测：NO_PROXY 里裸写 ::1、[::1]），httpx 解析代理配置
会抛 InvalidURL，Agent 连启动都起不来、录像直接停——现场不值得为这种环境变量冒险。
Agent 连的是内网服务器，本来也不需要走代理。
"""

from __future__ import annotations

import httpx


def client(**kwargs) -> httpx.Client:
    """新建客户端。`trust_env=False`：忽略 HTTP_PROXY / NO_PROXY / .netrc 等环境配置。"""
    return httpx.Client(trust_env=False, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    """一次性 POST（内部临时客户端，同样不读代理环境变量）。"""
    with client() as c:
        return c.post(url, **kwargs)


def put(url: str, **kwargs) -> httpx.Response:
    """一次性 PUT（内部临时客户端，同样不读代理环境变量）。"""
    with client() as c:
        return c.put(url, **kwargs)
