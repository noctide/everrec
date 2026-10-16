"""Agent 被强制结束后，FFmpeg 不能残留（Windows 实测发现：摄像头灯一直亮）。"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
import yaml

from everrec_agent.agent import Agent
from everrec_agent.child_guard import kill_orphans
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording
from everrec_core.ffmpeg import build_record_command, spool_dir
from everrec_core.segments import iter_archived, list_spool


@pytest.fixture
def cfg(tmp_path: Path) -> AgentConfig:
    c = AgentConfig(
        location=Location(number="9"),
        camera=Camera(type=CameraType.TEST),
        recording=Recording(segment_seconds=3),
    )
    c.install_dir = tmp_path
    return c


def _ffmpeg_children_of(pid: int) -> list[psutil.Process]:
    try:
        return [c for c in psutil.Process(pid).children(recursive=True) if "ffmpeg" in c.name()]
    except psutil.NoSuchProcess:
        return []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Windows 走作业对象，需在 Windows 上实测")
def test_ffmpeg_dies_when_agent_is_killed(cfg, tmp_path):
    conf = tmp_path / "config" / "everrec.yaml"
    conf.parent.mkdir()
    conf.write_text(yaml.safe_dump(cfg.model_dump(mode="json", exclude={"install_dir"})), encoding="utf-8")

    agent = subprocess.Popen(
        [sys.executable, "-m", "everrec_agent.cli", "run", "-c", str(conf)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not (kids := _ffmpeg_children_of(agent.pid)):
            time.sleep(0.3)
        assert kids, "FFmpeg 没启动"
        os.kill(agent.pid, signal.SIGKILL)  # 相当于任务管理器"结束任务"
        agent.wait(5)
        _, alive = psutil.wait_procs(kids, timeout=5)
        assert not alive, "Agent 被强制结束后 FFmpeg 还在"
    finally:
        agent.kill()
        for k in _ffmpeg_children_of(agent.pid):
            k.kill()


def test_startup_kills_orphan_and_recovers(cfg):
    """复现：残留 FFmpeg 还在写 spool，新 Agent 启动时不能崩，要先清掉它。"""
    spool_dir(cfg).mkdir(parents=True)
    orphan = subprocess.Popen(build_record_command(cfg), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(4)
        assert list_spool(spool_dir(cfg)), "残留进程应该已经写出分段"

        assert kill_orphans(spool_dir(cfg)) == 1
        assert orphan.wait(5) is not None

        Agent(cfg).recover_spool()
        assert list_spool(spool_dir(cfg)) == []
        assert list(iter_archived(cfg.recordings_dir))
    finally:
        orphan.kill()


def test_recover_skips_locked_file_instead_of_crashing(cfg, monkeypatch):
    spool_dir(cfg).mkdir(parents=True)
    locked = spool_dir(cfg) / "9_20261016_114536.ts"
    locked.write_bytes(b"\x47" + b"\0" * 187)
    agent = Agent(cfg)

    def busy(*_a, **_k):
        raise PermissionError(32, "另一个程序正在使用此文件")

    monkeypatch.setattr(agent, "_recover_one", busy)
    agent.recover_spool()  # 不抛异常
    assert locked.exists(), "被占用的文件原样保留，下次再处理"
