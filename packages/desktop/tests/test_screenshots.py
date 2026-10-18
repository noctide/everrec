"""定时截屏的纯逻辑：到点判断、最新一张、超量清理。不抓真实屏幕、不建 QApplication。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from everrec_desktop.screenshots import is_due, newest_screenshot, prune_screenshots


def _touch(path, when: datetime) -> None:
    path.write_bytes(b"x")
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def test_is_due():
    now = datetime(2026, 10, 18, 12, 0, 0)
    assert is_due(None, now, 300), "从没抓过就该抓"
    assert not is_due(now.timestamp() - 10, now, 300), "刚抓过不抓"
    assert is_due(now.timestamp() - 300, now, 300), "到一个间隔就抓"
    # 间隔有下限：配置写成 10 秒也按 1 分钟算，不会一秒一张
    assert not is_due(now.timestamp() - 30, now, 10)


def test_newest_screenshot(tmp_path):
    assert newest_screenshot(tmp_path / "nope") is None, "目录不存在时返回 None"
    assert newest_screenshot(tmp_path) is None, "空目录返回 None"
    now = datetime(2026, 10, 18, 12, 0, 0)
    _touch(tmp_path / "old.jpg", now - timedelta(hours=2))
    _touch(tmp_path / "new.jpg", now)
    (tmp_path / "notes.txt").write_text("不是图片", encoding="utf-8")
    assert newest_screenshot(tmp_path) == now.timestamp()


def test_prune_keeps_newest_and_ignores_other_files(tmp_path):
    now = datetime(2026, 10, 18, 12, 0, 0)
    files = []
    for i in range(5):
        p = tmp_path / f"code_20261018_12000{i}.jpg"
        _touch(p, now + timedelta(minutes=i))
        files.append(p)
    keep = tmp_path / "keep.txt"
    keep.write_text("别的文件不归它管", encoding="utf-8")

    removed = prune_screenshots(tmp_path, 3)

    assert {p.name for p in removed} == {files[0].name, files[1].name}, "删最旧的"
    assert len(list(tmp_path.glob("*.jpg"))) == 3
    assert keep.exists(), "只清理截图，不动目录里别的文件"
