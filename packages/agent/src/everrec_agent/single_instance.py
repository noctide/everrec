"""整台机器只允许一个录制进程（设计文档 4.1，需求 F03）。

Windows 用 Global\\ 命名的互斥锁，与登录用户无关；其他系统用文件锁。
"""

from __future__ import annotations

import sys
from pathlib import Path

MUTEX_NAME = "Global\\EverRecAgent"


class AlreadyRunning(RuntimeError):
    pass


class SingleInstance:
    def __init__(self, lock_dir: Path):
        self._lock_dir = lock_dir
        self._handle = None

    def __enter__(self) -> SingleInstance:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            self._handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                raise AlreadyRunning("本机已有 EverRec 录制进程在运行")
        else:
            import fcntl

            self._lock_dir.mkdir(parents=True, exist_ok=True)
            self._handle = open(self._lock_dir / "agent.lock", "w")
            try:
                fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise AlreadyRunning("本机已有 EverRec 录制进程在运行") from e
        return self

    def __exit__(self, *exc) -> None:
        if self._handle is None:
            return
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
        else:
            self._handle.close()
