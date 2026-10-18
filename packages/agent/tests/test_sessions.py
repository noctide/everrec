"""登录会话与锁屏判断：自动更新只在没人用桌面时装（tasks.md WP9）。

真查 WTS 的那部分只在 Windows 上跑（Linux 上返回 None），这里测"从会话列表得出结论"的纯逻辑。
"""

from __future__ import annotations

import sys

import psutil
import pytest

from everrec_agent import sessions


def _patch(monkeypatch, found):
    monkeypatch.setattr(sessions, "list_sessions", lambda: found)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 上真查 WTS，不测兜底分支")
def test_desktop_in_use_none_when_not_windows():
    """查不到 WTS：返回 None，调用方按"有人在用"处理，不会误更新。"""
    assert sessions.list_sessions() is None
    assert sessions.desktop_in_use() is None


def test_nobody_logged_in_is_idle(monkeypatch):
    _patch(monkeypatch, [])
    assert sessions.desktop_in_use() is False
    # 只有登录界面/服务自己的会话（没有用户名）也算没人
    _patch(monkeypatch, [sessions.Session(id=0, user="", locked=None)])
    assert sessions.desktop_in_use() is False


def test_all_sessions_locked_is_idle(monkeypatch):
    """只锁屏不注销：登录会话还在，但没人用桌面——现场大部分电脑是这样。"""
    _patch(monkeypatch, [
        sessions.Session(id=1, user="leo", locked=True),
        sessions.Session(id=2, user="wang", locked=True),
    ])
    assert sessions.desktop_in_use() is False


def test_any_unlocked_session_is_in_use(monkeypatch):
    _patch(monkeypatch, [
        sessions.Session(id=1, user="leo", locked=True),
        sessions.Session(id=2, user="wang", locked=False),
    ])
    assert sessions.desktop_in_use() is True


def test_unknown_lock_state_is_not_idle(monkeypatch):
    """有一个会话查不到锁屏状态：说不清，按"有人在用"处理。"""
    _patch(monkeypatch, [sessions.Session(id=1, user="leo", locked=None)])
    assert sessions.desktop_in_use() is None


def test_lock_flags_mapping():
    """WTSINFOEX 的 SessionFlags：Win8 起 0 = 锁屏、1 = 未锁屏，其余取值不认识。"""
    assert sessions._locked_from_flags(0) is True
    assert sessions._locked_from_flags(1) is False
    assert sessions._locked_from_flags(0xFFFFFFFF) is None
    assert sessions._locked_from_flags(4242) is None


def test_idle_must_last_ten_minutes():
    assert sessions.IDLE_LOCK_SECONDS == 600, "门槛改了记得同步文档和验收清单"
    assert not sessions.idle_long_enough(None, 10_000)
    assert not sessions.idle_long_enough(0.0, 599)
    assert sessions.idle_long_enough(0.0, 600)


def test_end_processes_kills_session_programs(monkeypatch):
    killed: list[str] = []

    class FakeProc:
        def __init__(self, name, pid):
            self.info = {"name": name}
            self.pid = pid

        def kill(self):
            killed.append(self.info["name"])

    agent_proc = FakeProc("everrec-agent.exe", 1)  # 服务自己不能被结束
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [
        FakeProc("everrec-screen.exe", 2),
        FakeProc("EverRec-Desktop.exe", 3),  # 大小写不敏感
        agent_proc,
    ])
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: (procs, []))
    assert sessions.end_processes() is True
    assert killed == ["everrec-screen.exe", "EverRec-Desktop.exe"]


def test_end_processes_reports_leftovers(monkeypatch):
    """没退干净要如实返回 False，调用方放弃本次更新。"""
    class FakeProc:
        info = {"name": "everrec-screen.exe"}
        pid = 4

        def kill(self):
            pass  # 假装杀不掉

    leftover = FakeProc()
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [leftover])
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: ([], procs))
    assert sessions.end_processes() is False
