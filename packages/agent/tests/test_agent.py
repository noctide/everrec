"""Agent 端到端：用测试画面真录。"""

import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from everrec_agent.agent import Agent
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording
from everrec_core.ffmpeg import ffmpeg_binary, spool_dir
from everrec_core.segments import iter_archived, list_spool, parse_spool_name


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


def test_counts_logged_in_sessions(monkeypatch):
    """心跳带当前登录用户（设计文档 6.2）；录屏模式的机器也靠它判断能不能更新；查不到按有人登录。"""
    import psutil

    from everrec_agent.agent import count_logged_in_users

    monkeypatch.setattr(psutil, "users", lambda: [
        type("U", (), {"terminal": "console", "name": "leo"})(),
        type("U", (), {"terminal": "rdp-tcp#1", "name": "leo"})(),
    ])
    assert count_logged_in_users() == 2
    monkeypatch.setattr(psutil, "users", lambda: [])
    assert count_logged_in_users() == 0

    def boom():
        raise OSError("查不到")

    monkeypatch.setattr(psutil, "users", boom)
    assert count_logged_in_users() == 1, "查不到时宁可当成有人登录，别把正在用的程序换坏"


def test_desktop_idle_waits_ten_minutes_after_lock(cfg, monkeypatch):
    """只锁屏不注销的机器也要能自动更新，但要锁够 10 分钟才算"没人用"（tasks.md WP9）。"""
    from everrec_agent import sessions

    agent = Agent(cfg)
    monkeypatch.setattr(sessions, "desktop_in_use", lambda: True)
    agent._last_idle_check = 0.0
    agent._refresh_desktop_idle()
    assert agent._desktop_idle_since is None
    assert agent.desktop_idle is False, "有人在用，不能换程序"

    monkeypatch.setattr(sessions, "desktop_in_use", lambda: False)
    agent._last_idle_check = 0.0
    agent._refresh_desktop_idle()
    assert agent._desktop_idle_since is not None
    assert agent.desktop_idle is False, "刚锁上，还不到 10 分钟"

    monkeypatch.setattr(sessions, "idle_long_enough", lambda since, now: True)
    assert agent.desktop_idle is True


def test_desktop_unknown_state_is_not_idle(cfg, monkeypatch):
    """探测失败（Linux、查不到、旧系统）按有人在用处理，不更新。"""
    from everrec_agent import sessions

    agent = Agent(cfg)
    monkeypatch.setattr(sessions, "desktop_in_use", lambda: None)
    agent._last_idle_check = 0.0
    agent._refresh_desktop_idle()
    monkeypatch.setattr(sessions, "idle_long_enough", lambda since, now: True)
    assert agent._desktop_idle_since is None


def test_screen_mode_updates_only_without_logged_in_users(cfg, monkeypatch):
    """录屏机不参与锁屏口径：录屏进程装完不会自己回来（下次登录才起），所以还是没人登录才更新。"""
    import psutil

    from everrec_agent import sessions

    cfg.camera = Camera(type=CameraType.SCREEN)
    agent = Agent(cfg)
    agent._desktop_idle_since = 0.0
    monkeypatch.setattr(sessions, "idle_long_enough", lambda since, now: True)  # 锁再久也不放行
    monkeypatch.setattr(psutil, "users",
                        lambda: [type("U", (), {"terminal": "console", "name": "leo"})()])
    assert agent.desktop_idle is False
    monkeypatch.setattr(psutil, "users", lambda: [])
    assert agent.desktop_idle is True


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


@pytest.mark.parametrize("online,expected", [(False, "backfill"), (True, "realtime")])
def test_new_segments_use_realtime_after_connection_recovers(cfg, monkeypatch, online, expected):
    cfg.server = "http://test"
    agent = Agent(cfg)
    agent.online = online
    agent.offline_since = datetime.now().astimezone()
    channels = []
    monkeypatch.setattr(agent.uploader, "enqueue", lambda _entry, channel: channels.append(channel))
    spool = spool_dir(cfg)
    spool.mkdir(parents=True)
    path = spool / "HD-A-012_20261015_090000.ts"
    path.write_bytes(b"sample")
    try:
        agent._archive(parse_spool_name(path))
        assert channels == [expected]
    finally:
        agent.uploader.http.close()

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

    monkeypatch.setattr("everrec_agent.agent.net.put", fake_put)
    Agent(cfg).send_thumbnail()
    assert calls["url"] == "http://test/agent/v1/thumbnail?code=HD-A-012"
    assert calls["content"] == b"\xff\xd8 fake-jpeg"
