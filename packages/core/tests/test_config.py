from pathlib import Path

import pytest
from pydantic import ValidationError

from everrec_core.config import AgentConfig, Location, load_config
from everrec_core.ffmpeg import build_record_command, parse_dshow_devices


def test_location_code():
    loc = Location(state="HD", district="PD", building="A", floor="3F", number="012")
    assert loc.code == "HD-PD-A-3F-012"
    assert Location().code == "UNASSIGNED"


def test_location_six_levels():
    loc = Location(dept="FIN", section="AP", area="E", site="HQ", spot="F3", number="012", label="三号收银台")
    assert loc.code == "FIN-AP-E-HQ-F3-012"
    assert Location(dept="FIN", spot="F3", number="1").code == "FIN-F3-1"  # 空层级跳过


def test_legacy_location_keys_keep_the_same_code(tmp_path: Path):
    """旧配置（州/区/楼栋/楼层）自动对应到新层级，编号字符串不变，录像目录不受影响。"""
    loc = Location(**{"state": "HD", "district": "PD", "building": "A", "floor": "3F", "number": "012"})
    assert (loc.dept, loc.section, loc.area, loc.site, loc.spot) == ("HD", "PD", "A", "", "3F")
    assert loc.code == "HD-PD-A-3F-012"
    (tmp_path / "config").mkdir()
    f = tmp_path / "config" / "everrec.yaml"
    f.write_text(
        "location: {state: HD, district: PD, building: A, floor: 3F, number: '012'}\n", encoding="utf-8")
    assert load_config(f).code == "HD-PD-A-3F-012"


def test_location_rejects_non_ascii():
    with pytest.raises(ValidationError):
        Location(area="A栋")


def test_location_label_may_be_chinese():
    assert Location(number="1", label=" 三号收银台 ").label == "三号收银台"


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
    assert 0 <= a.stagger_seconds < a.recording.segment_seconds
    assert a.stagger_seconds == AgentConfig(location=Location(number="001")).stagger_seconds
    assert a.stagger_seconds != b.stagger_seconds


def test_segment_defaults_to_one_hour():
    """默认一个录像文件 1 小时（Leo 定）；命令行按这个值切段。"""
    from everrec_core.config import Camera, CameraType

    cfg = AgentConfig(location=Location(number="001"), camera=Camera(type=CameraType.TEST))
    assert cfg.recording.segment_seconds == 3600
    cmd = build_record_command(cfg)
    assert cmd[cmd.index("-segment_time") + 1] == "3600"


def test_screenshot_defaults_off():
    """定时截屏默认关，间隔 5 分钟（本地设置，服务器不下发）。"""
    cfg = AgentConfig()
    assert cfg.screenshot.enabled is False
    assert cfg.screenshot.interval_seconds == 300


def test_bad_screenshot_interval_falls_back(tmp_path: Path):
    """间隔写小了不报错、不停录：回到默认值并给提示。"""
    f = tmp_path / "everrec.yaml"
    f.write_text("screenshot: {enabled: true, interval_seconds: 5}\n", encoding="utf-8")
    cfg = load_config(f)
    assert cfg.screenshot.enabled is True
    assert cfg.screenshot.interval_seconds == 300
    assert any("screenshot" in w for w in cfg.config_warnings)


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
