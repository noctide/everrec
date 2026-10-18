"""Windows 登录会话查询：判断"机器上有没有人在用桌面"（tasks.md WP9）。

Agent 是 Windows 服务，跑在 Session 0，看不到登录用户的桌面。要判断"能不能趁现在换程序"，
只能从系统会话表里查，都走 WTS（wtsapi32）：

- `WTSEnumerateSessionsW` 列出会话，`WTSQuerySessionInformationW(WTSUserName)` 看会话有没有人；
- `WTSQuerySessionInformationW(WTSSessionInfoEx)` 取锁屏标志 `SessionFlags`：
  Windows 8 起 0 = 锁屏、1 = 未锁屏（Windows 7 上这两个值是反的，微软文档承认是缺陷，按"说不清"处理）。

查不到一律返回 None，调用方按"有人在用"处理——宁可这次不更新，也不要在人打字的时候把程序换掉。

这些函数只在 Windows 上真跑（Linux 上直接返回 None），"从会话列表得出能不能更新"这类判断
做成纯函数，Linux 上的测试覆盖它们。
"""

from __future__ import annotations

import ctypes
import logging
import platform
import sys
from dataclasses import dataclass

import psutil

log = logging.getLogger("sessions")

IDLE_LOCK_SECONDS = 600  # 锁屏超过这么久算"没人用桌面"，允许自动更新
SESSION_PROGRAMS = ("everrec-screen.exe", "everrec-desktop.exe")  # 占着 exe 的登录会话程序

# WTS_INFO_CLASS
_WTS_USER_NAME = 5
_WTS_SESSION_INFO_EX = 25

# SessionFlags：微软文档写的取值（Win8 起）
_WTS_SESSIONSTATE_UNKNOWN = 0xFFFFFFFF
_WTS_SESSIONSTATE_LOCK = 0
_WTS_SESSIONSTATE_UNLOCK = 1


@dataclass
class Session:
    id: int
    user: str  # 空串表示这个会话没人登录
    locked: bool | None  # None = 查不到锁屏状态


def list_sessions() -> list[Session] | None:
    """列出所有会话。查不到返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        return _wts_sessions()
    except Exception:  # noqa: BLE001  探测失败不能影响录制和上报
        log.debug("查登录会话失败，按有人在用处理", exc_info=True)
        return None


def desktop_in_use() -> bool | None:
    """现在有没有人在用桌面。None = 说不清（调用方按"有人在用"处理）。

    - 没有登录会话（登录界面 / 已注销）→ False
    - 有登录会话，全部锁屏 → False
    - 有一个会话没锁屏 → True
    - 有的会话查不到锁屏状态 → None
    """
    sessions = list_sessions()
    if sessions is None:
        return None
    logged_in = [s for s in sessions if s.user]
    if not logged_in:
        return False
    if any(s.locked is None for s in logged_in):
        return None
    return not all(s.locked for s in logged_in)


def idle_long_enough(since: float | None, now: float, seconds: float = IDLE_LOCK_SECONDS) -> bool:
    """桌面从 `since`（monotonic）开始没人用，到现在够不够久。"""
    return since is not None and now - since >= seconds


def end_processes(names: tuple[str, ...] = SESSION_PROGRAMS, timeout: float = 5.0) -> bool:
    """结束这些程序在**所有**会话里的进程。全部退出返回 True。

    服务以 SYSTEM 身份跑，能结束登录用户的进程。安装器要替换它们的 exe，文件被占用就换不掉。
    没退干净返回 False，调用方放弃本次更新（下一小时再试）。
    """
    wanted = {n.lower() for n in names}
    procs = []
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() in wanted:
                procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for p in procs:
        try:
            p.kill()
            log.info("已结束 %s（PID %d），准备替换程序文件", p.info["name"], p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log.warning("结束 %s 失败：%s", p.info["name"], e)
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        log.warning("%s（PID %d）没有退出，本次先不更新", p.info["name"], p.pid)
    return not alive


def _locked_from_flags(flags: int) -> bool | None:
    """SessionFlags → 是否锁屏。未知取值返回 None。"""
    if flags == _WTS_SESSIONSTATE_LOCK:
        return True
    if flags == _WTS_SESSIONSTATE_UNLOCK:
        return False
    return None


def _wts_sessions() -> list[Session]:
    """真查 WTS。非 Windows 走不到这里。"""
    from ctypes import wintypes

    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

    class WTS_SESSION_INFO(ctypes.Structure):
        _fields_ = [
            ("SessionId", wintypes.DWORD),
            ("pWinStationName", wintypes.LPWSTR),
            ("State", ctypes.c_int),
        ]

    wtsapi32.WTSEnumerateSessionsW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(WTS_SESSION_INFO)), ctypes.POINTER(wintypes.DWORD),
    ]
    wtsapi32.WTSEnumerateSessionsW.restype = wintypes.BOOL
    wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
    wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    wtsapi32.WTSFreeMemory.restype = None

    infos = ctypes.POINTER(WTS_SESSION_INFO)()
    count = wintypes.DWORD()
    if not wtsapi32.WTSEnumerateSessionsW(
        None, 0, 1, ctypes.byref(infos), ctypes.byref(count)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        session_ids = [infos[i].SessionId for i in range(count.value)]
    finally:
        wtsapi32.WTSFreeMemory(ctypes.cast(infos, ctypes.c_void_p))

    # Windows 7 / Server 2008 R2 的锁屏标志是反的（微软文档承认的缺陷）：这类系统不拿它判断
    trustworthy = not platform.version().startswith("6.1")
    return [
        Session(
            id=sid,
            user=_wts_string(wtsapi32, sid, _WTS_USER_NAME),
            locked=_wts_locked(wtsapi32, sid) if trustworthy else None,
        )
        for sid in session_ids
    ]


def _wts_string(wtsapi32, session_id: int, info_class: int) -> str:
    """查会话的一个字符串属性（这里只用 WTSUserName）。失败返回空串。"""
    from ctypes import wintypes

    buf = ctypes.POINTER(wintypes.WCHAR)()
    size = wintypes.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(
        None, session_id, info_class, ctypes.byref(buf), ctypes.byref(size)
    ):
        return ""
    try:
        return buf.value or ""
    finally:
        wtsapi32.WTSFreeMemory(ctypes.cast(buf, ctypes.c_void_p))


def _wts_locked(wtsapi32, session_id: int) -> bool | None:
    """查会话的锁屏标志。查不到或格式不认识返回 None。

    WTSINFOEX 的布局：Level(DWORD，1 才有锁屏状态) / 4 字节对齐填充 / SessionId / SessionState /
    SessionFlags。按 DWORD 下标取就是 [0]=Level、[4]=SessionFlags。
    """
    from ctypes import wintypes

    buf = ctypes.POINTER(ctypes.c_byte)()
    size = wintypes.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(
        None, session_id, _WTS_SESSION_INFO_EX, ctypes.byref(buf), ctypes.byref(size)
    ):
        return None
    try:
        if not buf or size.value < 20:
            return None
        words = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))
        if words[0] != 1:  # 只处理 Level 1
            return None
        return _locked_from_flags(words[4])
    finally:
        wtsapi32.WTSFreeMemory(ctypes.cast(buf, ctypes.c_void_p))
