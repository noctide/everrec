"""设置：直接写配置文件、首次未配置判断。"""

from __future__ import annotations

from everrec_core.config import load_config
from everrec_desktop.common import apply_settings, needs_setup


def test_needs_setup_when_no_file_or_no_location(tmp_path):
    path = tmp_path / "config" / "everrec.yaml"
    assert needs_setup(path)  # 没有配置文件
    apply_settings(path, {"server": "http://x"})
    assert needs_setup(path)  # 有文件但位置没填
    apply_settings(path, {"number": "001"})
    assert not needs_setup(path)


def test_apply_settings_writes_and_keeps_other_values(tmp_path):
    path = tmp_path / "config" / "everrec.yaml"
    apply_settings(path, {"dept": "HD", "number": "012", "quality": "saver", "autostart": False,
                          "server": " http://10.0.0.1:8080 ", "folder": "D:/rec"})
    cfg = load_config(path)
    assert cfg.code == "HD-012"
    assert cfg.server == "http://10.0.0.1:8080"
    assert cfg.recording.quality.value == "saver"
    assert cfg.recording.folder == "D:/rec"
    assert cfg.autostart is False
    apply_settings(path, {"server": ""})  # 只改一项，其他不动
    cfg = load_config(path)
    assert cfg.server == "" and cfg.code == "HD-012" and cfg.recording.quality.value == "saver"


def test_bad_value_falls_back_with_warning(tmp_path):
    path = tmp_path / "everrec.yaml"
    warnings = apply_settings(path, {"number": "中文编号"})
    assert warnings  # 位置只能英文数字，写错的项回默认值并提示
    assert load_config(path).code == "UNASSIGNED"


def test_apply_settings_saves_screen_source(tmp_path):
    """录制画面能切成录屏，切回来也不丢其他设置（tasks.md WP12）。"""
    path = tmp_path / "config" / "everrec.yaml"
    apply_settings(path, {"number": "012", "camera_type": "screen"})
    cfg = load_config(path)
    assert cfg.camera.type.value == "screen"
    apply_settings(path, {"camera_type": "usb"})  # 只切来源，位置编号还在
    cfg = load_config(path)
    assert cfg.camera.type.value == "usb" and cfg.code == "012"


def test_apply_settings_saves_screenshot_switch(tmp_path):
    """定时截屏的开关和间隔写进配置文件的 screenshot 段，其他设置不受影响。"""
    path = tmp_path / "config" / "everrec.yaml"
    apply_settings(path, {"number": "012", "screenshot_enabled": True,
                          "screenshot_interval_seconds": 600})
    cfg = load_config(path)
    assert cfg.screenshot.enabled is True
    assert cfg.screenshot.interval_seconds == 600
    apply_settings(path, {"screenshot_enabled": False})  # 只关开关，间隔保留
    cfg = load_config(path)
    assert cfg.screenshot.enabled is False and cfg.screenshot.interval_seconds == 600
    assert cfg.code == "012"
