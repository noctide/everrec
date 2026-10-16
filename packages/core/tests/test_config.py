from pathlib import Path

import pytest
from pydantic import ValidationError

from everrec_core.config import AgentConfig, Location, load_config
from everrec_core.ffmpeg import build_record_command, parse_dshow_devices


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
    assert cmd[cmd.index("-maxrate") + 1] == f"{test_cfg.recording.encoding.maxrate_kbps}k"
    assert "-an" in cmd


def test_parse_dshow_devices_new_format():
    out = (
        '[dshow @ 0x1] "Integrated Camera" (video)\n'
        '[dshow @ 0x1]   Alternative name "@device_pnp_\\\\?\\usb#vid_04f2"\n'
        '[dshow @ 0x1] "Microphone (Realtek Audio)" (audio)\n'
        '[dshow @ 0x1] "OBS Virtual Camera" (video)\n'
    )
    assert parse_dshow_devices(out) == ["Integrated Camera", "OBS Virtual Camera"]


def test_parse_dshow_devices_old_format():
    out = (
        "[dshow @ 0x1] DirectShow video devices (some may be both video and audio devices)\n"
        '[dshow @ 0x1]  "USB2.0 HD UVC WebCam"\n'
        '[dshow @ 0x1]     Alternative name "@device_pnp_xyz"\n'
        "[dshow @ 0x1] DirectShow audio devices\n"
        '[dshow @ 0x1]  "Microphone"\n'
    )
    assert parse_dshow_devices(out) == ["USB2.0 HD UVC WebCam"]
