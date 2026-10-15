from pathlib import Path

import pytest
from pydantic import ValidationError

from everrec_core.config import AgentConfig, Location, load_config
from everrec_core.ffmpeg import build_record_command


def test_location_code():
    loc = Location(state="HD", district="PD", building="A", floor="3F", number="012")
    assert loc.code == "HD-PD-A-3F-012"
    assert Location().code == "UNASSIGNED"


def test_location_rejects_non_ascii():
    with pytest.raises(ValidationError):
        Location(building="A栋")


def test_relative_folder_resolves_to_install_dir(tmp_path: Path):
    (tmp_path / "config").mkdir()
    f = tmp_path / "config" / "everrec.yaml"
    f.write_text("location: {state: HD, number: '7'}\nrecording: {folder: recordings}\n", encoding="utf-8")
    cfg = load_config(f)
    assert cfg.install_dir == tmp_path
    assert cfg.recordings_dir == tmp_path / "recordings"
    assert cfg.code == "HD-7"


def test_stagger_is_stable_and_in_range():
    a = AgentConfig(location=Location(number="001"))
    b = AgentConfig(location=Location(number="002"))
    assert 0 <= a.stagger_seconds < 300
    assert a.stagger_seconds == AgentConfig(location=Location(number="001")).stagger_seconds
    assert a.stagger_seconds != b.stagger_seconds


def test_command_has_stagger_and_ratecap(test_cfg):
    cmd = build_record_command(test_cfg)
    assert cmd[cmd.index("-segment_clocktime_offset") + 1] == str(test_cfg.stagger_seconds)
    assert cmd[cmd.index("-maxrate") + 1] == "64k"
    assert "-an" in cmd
