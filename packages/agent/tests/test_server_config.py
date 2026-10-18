"""Agent 应用服务器下发配置：映射、缓存、分段边界生效。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from everrec_agent.agent import Agent
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording


@pytest.fixture
def cfg(tmp_path: Path) -> AgentConfig:
    c = AgentConfig(
        location=Location(state="HD", building="A", number="012"),
        camera=Camera(type=CameraType.TEST),
        recording=Recording(segment_seconds=2),
    )
    c.install_dir = tmp_path
    return c


def test_apply_to_cfg_maps_settings(cfg, monkeypatch):
    cfg.server = ""
    agent = Agent(cfg)
    monkeypatch.setattr(agent, "_probe_camera", lambda: None)
    agent._apply_to_cfg({
        "quality": "lean", "watermark": False,
        "segment_seconds": 120, "local_quota_gb": 30,
    })
    rec = agent.cfg.recording
    assert rec.quality == "lean"
    assert rec.watermark is False
    assert rec.segment_seconds == 120
    assert rec.local_quota_gb == 30
    assert rec.encoding.label == "480P / 8 帧"


def test_load_cached_server_config(cfg):
    cfg.server = ""
    state = cfg.state_dir
    state.mkdir(parents=True)
    (state / "server_config.json").write_text(
        json.dumps({"config_version": 5, "settings": {"quality": "sharp"}}), encoding="utf-8"
    )
    agent = Agent(cfg)
    assert agent._server_settings == {"quality": "sharp"}
    assert agent._config_version == 5


def test_tick_applies_pending_config_at_segment_boundary(cfg, monkeypatch):
    cfg.server = ""
    agent = Agent(cfg)
    agent._server_settings = {"quality": "lean"}
    applied: list[dict] = []
    monkeypatch.setattr(agent, "run_checks", lambda: None)
    monkeypatch.setattr(agent, "enforce_local_quota", lambda: None)
    monkeypatch.setattr(agent, "maybe_heartbeat", lambda: None)
    monkeypatch.setattr(agent, "write_status", lambda: None)
    monkeypatch.setattr(agent, "_reload_local_config", lambda: False)
    monkeypatch.setattr(agent, "_apply_to_cfg", lambda s: applied.append(s))
    monkeypatch.setattr(agent.recorder, "restart", lambda r: None)

    monkeypatch.setattr(agent, "archive_finished", lambda: 0)
    agent.tick()
    assert applied == [], "没到分段边界不应用"
    assert agent._server_settings is not None

    monkeypatch.setattr(agent, "archive_finished", lambda: 1)
    agent.tick()
    assert applied == [{"quality": "lean"}], "分段边界到达才应用"
    assert agent._server_settings is None, "应用后清空待处理配置"


def test_apply_upload_limits(cfg, monkeypatch):
    cfg.server = "http://test"
    agent = Agent(cfg)
    monkeypatch.setattr(agent, "_probe_camera", lambda: None)
    try:
        agent._apply_to_cfg({"upload_realtime_bps": 2_000_000, "upload_backfill_bps": 8_000_000})
        assert agent.uploader.buckets["realtime"].rate == 2_000_000 / 8
        assert agent.uploader.buckets["backfill"].rate == 8_000_000 / 8
    finally:
        agent.uploader.http.close()
