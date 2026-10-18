"""版本号比较（tasks.md WP9）：形如 0.1.0 的语义化版本，逐段按数字比。

用于服务端判断某台 Agent 是否落后于最新安装包，以及 Agent 侧自检。
非数字段（如 0.1.0-rc1）按字典序排在同数字版本之后。
"""

from __future__ import annotations


def parse(version: str) -> tuple:
    """把 "1.2.3" 解析成可比较的元组；无法识别的段按 0 处理。"""
    core, _, suffix = version.partition("-")
    parts: list = []
    for seg in core.split("."):
        seg = seg.strip()
        parts.append(int(seg) if seg.isdigit() else 0)
    # 补齐到 3 段，便于 "1.2" 与 "1.2.0" 相等
    while len(parts) < 3:
        parts.append(0)
    return (*parts, suffix)


def is_newer(candidate: str, current: str) -> bool:
    """candidate 是否比 current 更新。"""
    return parse(candidate) > parse(current)
