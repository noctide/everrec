"""set-config 子命令：改配置写回、非法值回退默认；命令行输出中文不能崩。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import pytest

from everrec_agent.agent import Agent
from everrec_agent.cli import cmd_set_config
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording, load_config, save_config


def test_init_works_on_non_utf8_console(tmp_path):
    """英文代码页控制台（cp1252）下 `init` 也要正常退出。

    实测：配置已经生成好了，最后 print 中文抛 UnicodeEncodeError、退出码 1，现场看着像失败。
    """
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run(
        [sys.executable, "-c", "from everrec_agent.cli import main; raise SystemExit(main())",
         "init", str(tmp_path / "inst")],
        capture_output=True, env=env,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert (tmp_path / "inst" / "config" / "everrec.yaml").exists()


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


@pytest.mark.parametrize("before,after", [
    ("", "http://new-server"),
    ("http://old-server", "http://new-server"),
    ("http://old-server", ""),
])
def test_config_change_updates_upload_target(tmp_path, monkeypatch, before, after):
    path = tmp_path / "config" / "everrec.yaml"
    path.parent.mkdir()
    save_config(AgentConfig(server=before), path)
    agent = Agent(load_config(path))
    monkeypatch.setattr("everrec_agent.uploader.Uploader.start", lambda _self: None)
    monkeypatch.setattr(agent, "_probe_camera", lambda: None)
    monkeypatch.setattr(agent.recorder, "restart", lambda _reason: None)
    save_config(AgentConfig(server=after), path)
    try:
        agent._config_dirty = True  # 模拟 _check_config_change 检测到文件改动
        agent._apply_pending_config()
        assert agent.cfg.server == after
        assert agent.uploader is not None and agent.uploader.base == after
        assert agent.online is None
    finally:
        if agent.uploader:
            agent.uploader.http.close()

