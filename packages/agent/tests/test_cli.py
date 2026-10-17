"""set-config 子命令：改配置写回、非法值回退默认。"""

from __future__ import annotations

import argparse
import json

from everrec_agent.agent import Agent
from everrec_agent.cli import cmd_set_config
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording, load_config, save_config


def test_set_config_writes_and_reloads(tmp_path):
    cfg_path = tmp_path / "config" / "everrec.yaml"
    cfg_path.parent.mkdir(parents=True)
    save_config(AgentConfig(), cfg_path)

    args = argparse.Namespace(
        config=str(cfg_path),
        keyvalues=["recording.quality=saver", "server=http://hq.local", "location.number=012"],
    )
    assert cmd_set_config(args) == 0

    cfg = load_config(cfg_path)
    assert cfg.recording.quality.value == "saver"
    assert cfg.server == "http://hq.local"
    assert cfg.code == "012"


def test_set_config_bad_value_falls_back(tmp_path):
    cfg_path = tmp_path / "config" / "everrec.yaml"
    cfg_path.parent.mkdir(parents=True)
    save_config(AgentConfig(), cfg_path)

    args = argparse.Namespace(config=str(cfg_path), keyvalues=["recording.quality=不存在的挡"])
    assert cmd_set_config(args) == 0  # 不报错，回退默认
    assert load_config(cfg_path).recording.quality.value == "standard"


def test_agent_writes_status_json(tmp_path):
    cfg = AgentConfig(
        location=Location(state="HD", building="A", number="012"),
        camera=Camera(type=CameraType.TEST),
        recording=Recording(segment_seconds=2),
    )
    cfg.install_dir = tmp_path
    Agent(cfg).write_status()
    data = json.loads((tmp_path / "state" / "status.json").read_text(encoding="utf-8"))
    assert data["code"] == "HD-A-012"
    assert "online" in data
    assert "checks" in data
