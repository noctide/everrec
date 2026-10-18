# ruff: noqa: E501  (dshow 原始输出样例行较长)
"""画质挡位、码率自动计算、摄像头取流格式选择、配置写错不停录。"""

from pathlib import Path

import pytest

from everrec_core import ffmpeg as ff
from everrec_core.config import AgentConfig, Camera, CameraType, load_config
from everrec_core.ffmpeg import CameraMode, build_record_command, choose_mode, parse_dshow_modes
from everrec_core.profiles import (
    DEFAULT_QUALITY,
    Encoding,
    Quality,
    Resolution,
    quality_table,
    resolve,
)

# ---------- 挡位与码率 ----------


def test_default_is_720p_10fps():
    assert DEFAULT_QUALITY is Quality.STANDARD
    e = AgentConfig().recording.encoding
    assert (e.width, e.height, e.fps) == (1280, 720, 10)


def test_quality_stops_and_rate_estimates():
    rows = {r["quality"]: r for r in quality_table()}
    assert [r["recommended"] for r in quality_table()].count(True) == 1
    assert (rows["saver"]["resolution"], rows["saver"]["fps"]) == ("360p", 5)
    assert (rows["max"]["resolution"], rows["max"]["fps"]) == ("1080p", 10)
    # 与给 Leo 的估算表一致（每路每小时上限，MB）
    assert rows["saver"]["mb_per_hour_max"] == pytest.approx(36, abs=3)
    assert rows["standard"]["mb_per_hour_max"] == pytest.approx(290, abs=5)
    assert rows["max"]["mb_per_hour_max"] == pytest.approx(653, abs=5)


def test_bitrate_follows_resolution_and_fps():
    base = Encoding(Resolution.P720, 10)
    assert Encoding(Resolution.P1080, 10).maxrate_kbps > 2 * base.maxrate_kbps
    assert Encoding(Resolution.P720, 15).maxrate_kbps == pytest.approx(base.maxrate_kbps * 1.5, rel=0.01)


def test_advanced_overrides_and_fps_stops():
    e = resolve("saver", resolution="720p")
    assert (e.resolution, e.fps) == (Resolution.P720, 5)
    with pytest.raises(ValueError):
        resolve("standard", fps=30)


def test_legacy_profile_names_still_load(tmp_path: Path):
    f = tmp_path / "everrec.yaml"
    f.write_text("recording: {profile: mid}\n", encoding="utf-8")
    cfg = load_config(f)
    assert cfg.recording.quality is Quality.STANDARD and not cfg.config_warnings


def test_command_uses_derived_bitrate(test_cfg):
    cmd = build_record_command(test_cfg)
    enc = test_cfg.recording.encoding
    assert cmd[cmd.index("-maxrate") + 1] == f"{enc.maxrate_kbps}k"
    assert f"testsrc2=size={enc.width}x{enc.height}:rate={enc.fps}" in cmd


# ---------- 配置写错不停录 ----------


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("recording: {quality: superhd}\n", "recording.quality"),
        ("recording: {fps: 30}\n", "recording.fps"),
        ("recording: {resolution: 4k}\n", "recording.resolution"),
        ("location: {area: A栋, number: '012'}\n", "location.area"),
        ("recording: {segment_seconds: abc}\n", "recording.segment_seconds"),
    ],
)
def test_bad_value_falls_back_to_default(tmp_path: Path, text: str, field: str):
    f = tmp_path / "everrec.yaml"
    f.write_text(text, encoding="utf-8")
    cfg = load_config(f)
    assert cfg.config_warnings and field in cfg.config_warnings[0]
    cfg.recording.encoding  # 能算出编码参数，录制可以继续  # noqa: B018


def test_bad_value_keeps_other_settings(tmp_path: Path):
    f = tmp_path / "everrec.yaml"
    f.write_text("location: {state: HD, number: '7'}\nrecording: {fps: 99, quality: saver}\n",
                 encoding="utf-8")
    cfg = load_config(f)
    assert cfg.code == "HD-7"
    assert cfg.recording.quality is Quality.SAVER and cfg.recording.fps is None


def test_broken_yaml_records_with_defaults(tmp_path: Path):
    f = tmp_path / "everrec.yaml"
    f.write_text("recording: {quality: [unclosed\n", encoding="utf-8")
    cfg = load_config(f)
    assert cfg.config_warnings and "格式错误" in cfg.config_warnings[0]
    assert cfg.recording.quality is DEFAULT_QUALITY


# ---------- 摄像头取流格式 ----------

DSHOW_OPTIONS = """\
[dshow @ 000001] DirectShow video device options (from video devices)
[dshow @ 000001]  Pin "Capture" (alternative pin name "0")
[dshow @ 000001]   vcodec=mjpeg  min s=1280x720 fps=30 max s=1280x720 fps=30
[dshow @ 000001]   vcodec=mjpeg  min s=1280x720 fps=30 max s=1280x720 fps=30 (tv, bt470bg/bt709/unknown, topleft)
[dshow @ 000001]   vcodec=mjpeg  min s=640x480 fps=30 max s=640x480 fps=30
[dshow @ 000001]   pixel_format=yuyv422  min s=1280x720 fps=10 max s=1280x720 fps=10
[dshow @ 000001]   pixel_format=yuyv422  min s=640x480 fps=30 max s=640x480 fps=30
[dshow @ 000001]   pixel_format=yuyv422  min s=640x360 fps=30 max s=640x360 fps=30
"""


def test_parse_dshow_modes():
    modes = parse_dshow_modes(DSHOW_OPTIONS)
    assert {m.size for m in modes} == {"1280x720", "640x480", "640x360"}
    assert CameraMode("mjpeg", None, 1280, 720, 30) in modes
    assert CameraMode(None, "yuyv422", 1280, 720, 10) in modes


def test_choose_exact_size_prefers_mjpeg():
    m = choose_mode(parse_dshow_modes(DSHOW_OPTIONS), Encoding(Resolution.P720, 10))
    assert (m.size, m.codec) == ("1280x720", "mjpeg")


def test_choose_downscales_from_next_bigger():
    m = choose_mode(parse_dshow_modes(DSHOW_OPTIONS), Encoding(Resolution.P480, 8))
    assert m.size == "1280x720"  # 没有 854x480，取比它大的里最小的，缩小不损失清晰度


def test_camera_too_small_caps_recording(monkeypatch):
    cfg = AgentConfig(camera=Camera(type=CameraType.USB, device="cam"))
    cfg.recording.quality = Quality.MAX  # 1080P
    monkeypatch.setattr(ff, "list_camera_modes", lambda _d: tuple(parse_dshow_modes(DSHOW_OPTIONS)))
    enc, mode, note = ff.effective_encoding(cfg)
    assert enc.resolution is Resolution.P720 and mode.size == "1280x720"
    assert note and "1280x720" in note


def test_no_mode_info_means_no_forced_size(monkeypatch):
    monkeypatch.setattr(ff, "list_camera_modes", lambda _d: ())
    assert choose_mode((), Encoding(Resolution.P720, 10)) is None
