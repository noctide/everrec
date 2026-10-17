"""Agent 端到端：用测试画面真录。"""

import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

from everrec_agent.agent import Agent
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording
from everrec_core.ffmpeg import ffmpeg_binary, spool_dir
from everrec_core.segments import iter_archived, list_spool


@pytest.fixture
def cfg(tmp_path: Path) -> AgentConfig:
    c = AgentConfig(
        location=Location(state="HD", building="A", number="012"),
        camera=Camera(type=CameraType.TEST),
        recording=Recording(segment_seconds=2),
        heartbeat_seconds=1,
    )
    c.install_dir = tmp_path
    return c


def _run_for(agent: Agent, seconds: float) -> list[bool]:
    """跑一段时间，每秒记一次 agent.recording。"""
    t = threading.Thread(target=agent.run, daemon=True)
    t.start()
    seen = []
    for _ in range(int(seconds)):
        time.sleep(1)
        seen.append(agent.recording)
    agent.stop_event.set()
    t.join(30)
    assert not t.is_alive()
    return seen


def test_records_offline_and_archives(cfg):
    cfg.server = "http://127.0.0.1:9"  # 没人监听：模拟服务器不可用
    agent = Agent(cfg)
    seen = _run_for(agent, 9)
    # 不写死第几秒开始（Windows 上启动更慢），只要求确实录上了，且一旦开始就持续、不中途掉线
    assert any(seen), f"一直没报告在录：{seen}"
    assert all(seen[seen.index(True):]), f"开始录制后中途掉线：{seen}"

    archived = list(iter_archived(cfg.recordings_dir))
    assert len(archived) >= 3, "连不上服务器也要照常录"
    assert all("HD-A-012" in p.parts for p in archived)
    assert list_spool(spool_dir(cfg)) == [], "停止时最后一段也要归档"
    assert agent.online is False and agent.offline_since is not None
    assert len(agent.index.entries()) == len(archived)
    # 正常停止（给 FFmpeg 发 q）时最后一段也是完好的，不需要修复
    from everrec_core.repair import inspect
    last = archived[-1]
    assert inspect(last).healthy, last
    assert not (cfg.recordings_dir / "_quarantine").exists()


def test_recovers_interrupted_segment_on_startup(cfg):
    spool = spool_dir(cfg)
    spool.mkdir(parents=True)
    good = spool / "HD-A-012_20261015_090000.ts"
    subprocess.run(
        [ffmpeg_binary(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=5",
         "-t", "6", "-c:v", "libx264", "-g", "10", "-f", "mpegts", str(good)],
        check=True,
    )
    data = good.read_bytes()
    broken = spool / "HD-A-012_20261015_090500.ts"
    broken.write_bytes(data[: len(data) // 2 + 33])  # 断电：截在半个包上

    Agent(cfg).recover_spool()

    archived = sorted(p.name for p in iter_archived(cfg.recordings_dir))
    assert archived == ["HD-A-012_090000.ts", "HD-A-012_090500.ts"]
    assert (cfg.recordings_dir / "_quarantine" / broken.name).exists(), "原文件进隔离目录保留"
    integrities = {Path(e.path).name: e.integrity for e in Agent(cfg).index.entries()}
    assert integrities["HD-A-012_090000.ts"] == "ok"
    assert integrities["HD-A-012_090500.ts"] in ("repaired", "partial")


def test_send_thumbnail_uploads(tmp_path, monkeypatch):
    cfg = AgentConfig(
        server="http://test",
        location=Location(state="HD", building="A", number="012"),
        camera=Camera(type=CameraType.TEST),
        recording=Recording(segment_seconds=2),
    )
    cfg.install_dir = tmp_path
    thumb = cfg.state_dir / "thumb.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"\xff\xd8 fake-jpeg")

    calls: dict = {}

    def fake_put(url, **kw):
        calls["url"] = url
        calls["content"] = kw.get("content")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("everrec_agent.agent.httpx.put", fake_put)
    Agent(cfg).send_thumbnail()
    assert calls["url"] == "http://test/agent/v1/thumbnail?code=HD-A-012"
    assert calls["content"] == b"\xff\xd8 fake-jpeg"
