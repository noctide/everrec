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
    apply_settings(path, {"state": "HD", "number": "012", "quality": "saver", "autostart": False,
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
