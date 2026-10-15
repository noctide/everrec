"""管理 FFmpeg 录制子进程：启动、异常退出后重启、累计重启次数（健康检查 ③）。"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from everrec_core.config import AgentConfig
from everrec_core.ffmpeg import build_record_command, spool_dir

log = logging.getLogger("recorder")

# 摄像头打不开、被占用、权限被关时 FFmpeg 的典型报错（健康检查 ②）
DEVICE_ERROR_HINTS = (
    "could not find video device",
    "i/o error",
    "device or resource busy",
    "no such file or directory",
    "could not run graph",
    "permission denied",
    "cannot open video device",
)


class Recorder:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.proc: subprocess.Popen[str] | None = None
        self.restarts = 0
        self.device_error: str | None = None
        self._stderr_tail: list[str] = []
        self._next_start = 0.0
        self._backoff = 2.0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            spool_dir(self.cfg).mkdir(parents=True, exist_ok=True)
            cmd = build_record_command(self.cfg)
            log.info("启动录制：%s", " ".join(cmd[1:]))
            self._stderr_tail = []
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            threading.Thread(target=self._pump_stderr, args=(self.proc,), daemon=True).start()

    def _pump_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.strip()
            if not line:
                continue
            self._stderr_tail = (self._stderr_tail + [line])[-20:]
            low = line.lower()
            if any(h in low for h in DEVICE_ERROR_HINTS):
                self.device_error = line
            log.warning("ffmpeg: %s", line)

    def stop(self, timeout: float = 10) -> None:
        """正常停止：发 q 让 FFmpeg 收尾，当前分段完整落盘。"""
        with self._lock:
            p, self.proc = self.proc, None
        if p is None or p.poll() is not None:
            return
        p.terminate()  # FFmpeg 收到 SIGTERM/CTRL_BREAK 会正常写完当前分段
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()

    def restart(self, reason: str) -> None:
        log.warning("重启录制：%s", reason)
        self.stop()
        self.restarts += 1
        self.start()

    def supervise(self) -> None:
        """主循环里定期调用：进程挂了就按退避重启。"""
        if self.running:
            self._backoff = 2.0
            return
        if self.proc is not None:
            code = self.proc.returncode
            log.error("录制进程退出（代码 %s）：%s", code, " | ".join(self._stderr_tail[-3:]))
            self.proc = None
            self.restarts += 1
            self._next_start = time.monotonic() + self._backoff
            self._backoff = min(self._backoff * 2, 60)
        if time.monotonic() >= self._next_start:
            self.start()  # device_error 等到文件重新增长时由 Agent 清掉
