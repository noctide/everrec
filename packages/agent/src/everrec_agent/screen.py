"""录屏会话进程（tasks.md WP12）：在登录用户的会话里跑 FFmpeg 抓桌面，写同一个 `_spool`。

为什么要有这个进程：Windows 服务跑在 Session 0，抓不到登录用户的桌面，服务里起 gdigrab
只会得到黑屏。所以录屏由"所有用户登录启动"项拉起本进程，归档、上传、健康检查仍由服务负责。

本进程只做三件事：
1. 锁屏就停 FFmpeg、解锁就恢复（锁屏期间抓到的是锁屏界面，不该录）。
2. 把状态写到 `state/screen.json`，服务读它判断"录屏进程在不在、是不是锁屏暂停"。
3. 单实例锁，多用户同时登录时只让第一个会话录。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from everrec_core.config import AgentConfig, CameraType, load_config

from .recorder import Recorder
from .single_instance import AlreadyRunning, SingleInstance

log = logging.getLogger("screen")

SCREEN_STATE = "screen.json"
POLL_SECONDS = 5.0


def is_locked() -> bool:
    """当前会话是不是锁屏。

    判断办法：打不开输入桌面（OpenInputDesktop 失败）或者输入桌面不是 "Default"（是 Winlogon），
    都算锁屏。非 Windows 上永远返回 False（这个进程本来就只在 Windows 上跑）。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        handle = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_READOBJECTS
        if not handle:
            return True
        try:
            # 桌面名是 Winlogon 说明正显示在登录/锁屏界面
            buf = ctypes.create_unicode_buffer(256)
            need = wintypes.DWORD()
            ok = user32.GetUserObjectInformationW(
                handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)  # UOI_NAME
            )
            return bool(ok) and buf.value.lower() == "winlogon"
        finally:
            user32.CloseDesktop(handle)
    except Exception:  # 探测失败按"没锁屏"处理：宁可继续录，也不要莫名停掉
        log.debug("判断锁屏失败，按没锁屏继续", exc_info=True)
        return False


def write_state(state_dir: Path, running: bool, locked: bool, now: datetime | None = None) -> Path:
    """原子写 `state/screen.json`：先写临时文件再改名，服务端不会读到半截。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    dest = state_dir / SCREEN_STATE
    data = {
        "running": running,
        "locked": locked,
        "at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "pid": os.getpid(),
    }
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(dest)
    return dest


def read_state(state_dir: Path) -> dict:
    """读 `state/screen.json`；没有或读坏了返回空字典。"""
    try:
        data = json.loads((state_dir / SCREEN_STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class ScreenMonitor:
    """Agent 侧看会话进程的窗口：接口跟 Recorder 一样，屏幕模式下顶替它（Agent 不起自己的 FFmpeg）。

    `running` 要求状态文件足够新：进程被杀掉后文件还留着，靠 `stale_seconds` 判断它已经死了。
    """

    def __init__(self, cfg: AgentConfig, stale_seconds: float = 90):
        self.cfg = cfg
        self.stale_seconds = stale_seconds
        self.restarts = 0
        self.device_error: str | None = None
        self._state: dict = {}

    def supervise(self) -> None:
        self._state = read_state(self.cfg.state_dir)

    def start(self) -> None:
        """自己不起 FFmpeg：录屏进程由登录启动项负责。"""

    def stop(self) -> None:
        """不主动停：服务停了录制也该继续，反正归档后还要切下一段。"""

    def restart(self, reason: str) -> None:
        log.warning("录屏没有画面（%s）：检查登录启动项里的 everrec-screen 是否在跑", reason)

    @property
    def running(self) -> bool:
        if not self._state.get("running"):
            return False
        try:
            at = datetime.fromisoformat(str(self._state.get("at", "")))
        except ValueError:
            return False
        return (datetime.now().astimezone() - at).total_seconds() <= self.stale_seconds

    @property
    def locked(self) -> bool:
        return self.running and bool(self._state.get("locked"))


class ScreenSession:
    """登录会话里跑的录屏进程本体。"""

    def __init__(self, cfg: AgentConfig, poll_seconds: float = POLL_SECONDS):
        self.cfg = cfg
        self.poll_seconds = poll_seconds
        self.recorder = Recorder(cfg)

    def run(self) -> None:
        log.info("录屏进程启动，位置编号 %s，录像目录 %s", self.cfg.code, self.cfg.recordings_dir)
        locked = is_locked()
        self._apply(locked)
        try:
            while True:
                time.sleep(self.poll_seconds)
                now_locked = is_locked()
                if now_locked != locked:
                    locked = now_locked
                    log.info("锁屏状态变化：%s", "已锁屏，暂停录屏" if locked else "已解锁，恢复录屏")
                    self._apply(locked)
                elif not locked:
                    self.recorder.supervise()  # 崩了就按退避重启
                write_state(self.cfg.state_dir, running=self.recorder.running, locked=locked)
        except KeyboardInterrupt:
            pass
        finally:
            self.recorder.stop()
            write_state(self.cfg.state_dir, running=False, locked=is_locked())
            log.info("录屏进程已退出")

    def _apply(self, locked: bool) -> None:
        if locked:
            self.recorder.stop()
        elif not self.recorder.running:
            self.recorder.start()
        write_state(self.cfg.state_dir, running=self.recorder.running, locked=locked)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import logs

    ap = argparse.ArgumentParser(prog="everrec-screen")
    ap.add_argument("--install-dir", help="安装目录，默认是程序所在的文件夹")
    args = ap.parse_args(argv)
    install_dir = Path(args.install_dir) if args.install_dir else Path(sys.executable).parent
    logs.setup_logging(install_dir / "logs")
    cfg = load_config(install_dir / "config" / "everrec.yaml")
    if cfg.camera.type is not CameraType.SCREEN:
        log.info("配置里不是录屏（camera.type=%s），本进程不做事", cfg.camera.type)
        return 0
    try:
        with SingleInstance(cfg.state_dir, name="EverRecScreen", file_name="screen.lock"):
            return ScreenSession(cfg).run() or 0
    except AlreadyRunning:
        log.warning("已有别的会话在录屏，本会话退出（多用户同时登录时只让第一个会话录）")
        return 0
