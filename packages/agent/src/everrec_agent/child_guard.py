"""保证 FFmpeg 子进程不会在 Agent 死掉后残留。

Agent 被强制结束（任务管理器、断电前的崩溃）时，操作系统默认不会连带结束子进程，
残留的 FFmpeg 会继续占着摄像头、继续写文件，新启动的 Agent 既打不开摄像头也搬不动文件。

三层防护：
1. Windows：把 FFmpeg 放进一个"关闭即结束全部成员"的作业对象（Job Object），
   Agent 进程一死，句柄被系统回收，FFmpeg 随之结束。
2. Linux：PR_SET_PDEATHSIG，父进程死时内核给 FFmpeg 发 SIGKILL。
3. 启动时兜底：找出命令行里写着本机录像目录的残留 FFmpeg，结束掉。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import psutil

log = logging.getLogger("child_guard")

_job = None  # Windows 作业对象句柄，进程存活期间一直持有


def _windows_job():
    global _job
    if _job is not None:
        return _job
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW 失败")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject 失败")
    _job = (kernel32, job)
    return _job


def attach(proc) -> None:
    """Popen 之后立即调用（Windows）。失败只记日志，不影响录制。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32, job = _windows_job()
        handle = ctypes.c_void_p(int(proc._handle))
        if not kernel32.AssignProcessToJobObject(job, handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject 失败")
    except Exception as e:  # noqa: BLE001
        log.warning("无法把 FFmpeg 绑定到 Agent 生命周期，Agent 被强制结束时它可能残留：%s", e)


def preexec_fn():
    """Popen 的 preexec_fn（Linux）：父进程死了内核给子进程发 SIGKILL。"""
    if not sys.platform.startswith("linux"):
        return None

    def _set_pdeathsig():
        import ctypes
        import signal

        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGKILL)

    return _set_pdeathsig


def kill_orphans(spool: Path) -> int:
    """结束命令行里写着本机 spool 目录的残留 FFmpeg。返回结束的个数。"""
    marker = str(spool).lower()
    killed = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            if not name.startswith("ffmpeg"):
                continue
            cmd = " ".join(p.info["cmdline"] or []).lower()
            if marker in cmd:
                p.kill()
                killed.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    psutil.wait_procs(killed, timeout=5)
    for p in killed:
        log.warning("结束了上次残留的 FFmpeg（PID %d），它还占着摄像头", p.pid)
    return len(killed)
