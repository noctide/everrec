"""录屏模式（tasks.md WP12）：状态文件、锁屏暂停、Agent 不自己起 FFmpeg。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

from everrec_agent.agent import Agent
from everrec_agent.screen import ScreenMonitor, ScreenSession, is_locked, read_state, write_state
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording
from everrec_core.health import Check


def _cfg(tmp_path: Path) -> AgentConfig:
    c = AgentConfig(
        location=Location(state="HD", building="A", number="012"),
        camera=Camera(type=CameraType.SCREEN),
        recording=Recording(segment_seconds=2),
    )
    c.install_dir = tmp_path
    return c


def test_state_roundtrip_and_broken_file(tmp_path: Path):
    assert read_state(tmp_path) == {}
    write_state(tmp_path, running=True, locked=False)
    st = read_state(tmp_path)
    assert st["running"] is True and st["locked"] is False and st["pid"] > 0
    (tmp_path / "screen.json").write_text("{", encoding="utf-8")  # 半截文件不炸
    assert read_state(tmp_path) == {}


def test_monitor_only_trusts_fresh_state(tmp_path: Path):
    cfg = _cfg(tmp_path)
    monitor = ScreenMonitor(cfg, stale_seconds=60)
    monitor.supervise()
    assert not monitor.running and not monitor.locked

    write_state(cfg.state_dir, running=True, locked=True)
    monitor.supervise()
    assert monitor.running and monitor.locked

    # 状态文件太旧 = 进程已经死了（被杀掉时文件还留着）
    old = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat(timespec="seconds")
    (cfg.state_dir / "screen.json").write_text(
        f'{{"running": true, "locked": false, "at": "{old}", "pid": 1}}', encoding="utf-8"
    )
    monitor.supervise()
    assert not monitor.running


def test_agent_in_screen_mode_supervises_session_process(tmp_path: Path):
    agent = Agent(_cfg(tmp_path))
    assert agent._screen is True
    assert isinstance(agent.recorder, ScreenMonitor)


def test_locked_screen_is_paused_not_abnormal(tmp_path: Path):
    cfg = _cfg(tmp_path)
    agent = Agent(cfg)
    write_state(cfg.state_dir, running=True, locked=True)
    agent.run_checks()
    assert agent.paused == "锁屏暂停"
    assert agent.checks.process is Check.OK
    assert agent.checks.dataflow is Check.OK  # 锁屏没有画面，不算数据流异常
    assert agent.recording is False
    assert agent.build_heartbeat().paused == "锁屏暂停"


def test_is_locked_returns_bool():
    """非 Windows 永远 False；Windows 上按当前会话判断，这里只要求不抛异常。"""
    got = is_locked()
    assert isinstance(got, bool)
    if sys.platform != "win32":
        assert got is False


def test_session_apply_starts_and_stops(tmp_path: Path, monkeypatch):
    """锁屏停 FFmpeg、解锁恢复；只验证调用序列，不真起进程。"""
    session = ScreenSession(_cfg(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(session.recorder, "start", lambda: calls.append("start"))
    monkeypatch.setattr(session.recorder, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(type(session.recorder), "running", property(lambda _self: False))

    session._apply(locked=True)
    assert calls == ["stop"]
    assert read_state(session.cfg.state_dir)["locked"] is True

    session._apply(locked=False)
    assert calls == ["stop", "start"]
    assert read_state(session.cfg.state_dir)["locked"] is False
