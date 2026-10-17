"""Agent 自动更新（tasks.md WP9、设计文档 14.5）。

每天在设定时段（服务端下发 update_mode / update_window_start / update_window_end，
默认 auto / 02:00–04:00）向服务器检查版本：有新版且服务端判定"该更新"时，
下载安装包到临时目录、校验 SHA-256、静默安装（Inno Setup /VERYSILENT），
安装器会停服务、替换文件、重启，Agent 随之退出。

只提醒模式（update_mode=notify）或不在时段内：只记一条日志，不下载不安装。
更新逻辑本身在 Linux 上可测；真正"运行安装器"是 Windows 专用步骤，做成可注入，
测试里用假安装器替代。

原则：录像不因更新中断到无法恢复——下载与校验失败只放弃本次更新，不影响录制；
安装由安装器负责停/启服务与失败回滚（Inno Setup 事务式：出错还原旧文件）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("everrec.updater")

# Inno Setup 静默安装参数：无界面、不弹框、不重启（安装器自己停启服务）
SILENT_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]


@dataclass
class UpdatePlan:
    """一次检查的结论。"""

    update_available: bool = False
    should_update: bool = False
    latest_version: str | None = None
    update_mode: str = "auto"
    sha256: str = ""
    size: int = 0
    download_url: str = ""
    notes: str | None = None


def check(server: str, code: str, token: str, current_version: str,
          *, http: httpx.Client | None = None) -> UpdatePlan | None:
    """向服务器检查更新。连不上返回 None（不影响录制）。"""
    client = http or httpx.Client(timeout=15)
    try:
        r = client.get(
            server.rstrip("/") + "/agent/v1/version-check",
            params={"code": code, "version": current_version},
            headers={"X-Agent-Token": token},
        )
        r.raise_for_status()
        return UpdatePlan(**r.json())
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("检查更新失败（继续录制）：%s", e)
        return None
    finally:
        if http is None:
            client.close()


def download(server: str, code: str, token: str, url: str, expected_sha256: str,
             *, http: httpx.Client | None = None) -> Path | None:
    """下载安装包到临时目录并校验 SHA-256。失败返回 None，不落任何残留。"""
    client = http or httpx.Client(timeout=httpx.Timeout(300.0, connect=15.0))
    dest: Path | None = None
    try:
        full = url if url.startswith("http") else server.rstrip("/") + url
        with client.stream("GET", full, params={"code": code},
                           headers={"X-Agent-Token": token}) as r:
            r.raise_for_status()
            fd, name = tempfile.mkstemp(prefix="everrec-update-", suffix=".exe")
            os.close(fd)
            dest = Path(name)
            h = hashlib.sha256()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(1024 * 1024):
                    f.write(chunk)
                    h.update(chunk)
        if h.hexdigest() != expected_sha256:
            log.error("安装包校验失败，放弃本次更新")
            dest.unlink(missing_ok=True)
            return None
        return dest
    except (httpx.HTTPError, OSError) as e:
        log.warning("下载安装包失败，放弃本次更新：%s", e)
        if dest:
            dest.unlink(missing_ok=True)
        return None
    finally:
        if http is None:
            client.close()


def install(package: Path, *, runner=subprocess.Popen) -> bool:
    """静默安装。默认真跑安装器（Windows）；测试注入假 runner。

    安装器接管后会停服务、替换文件、重启，Agent 进程随之结束。
    这里不等待安装完成（它会杀掉我们），只负责拉起。
    """
    if sys.platform != "win32" and runner is subprocess.Popen:
        log.warning("非 Windows 平台不执行安装器：%s", package)
        return False
    try:
        runner([str(package), *SILENT_ARGS])
        log.info("已拉起安装器：%s，等待其接管并重启服务", package.name)
        return True
    except OSError as e:
        log.error("拉起安装器失败：%s", e)
        return False


def run_update(server: str, code: str, token: str, current_version: str,
               *, http: httpx.Client | None = None, runner=subprocess.Popen) -> str:
    """完整跑一次更新检查。返回动作描述（no-update / notify / downloaded / installed / failed）。"""
    plan = check(server, code, token, current_version, http=http)
    if plan is None:
        return "unreachable"
    if not plan.update_available:
        return "no-update"
    if not plan.should_update:
        # 只提醒模式或不在时段内：记日志，不下载
        log.info("发现新版本 %s，但当前不自动安装（模式 %s）", plan.latest_version, plan.update_mode)
        return "notify"
    package = download(server, code, token, plan.download_url, plan.sha256, http=http)
    if package is None:
        return "failed"
    if install(package, runner=runner):
        return "installed"
    package.unlink(missing_ok=True)
    return "failed"
