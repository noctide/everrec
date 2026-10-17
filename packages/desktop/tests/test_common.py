"""桌面端纯逻辑（与 Qt 解耦的部分）：status.json、时间轴、日志解析、文件展开。"""

from __future__ import annotations

import json
from datetime import datetime

from everrec_desktop.common import (
    build_timeline,
    collect_files,
    format_duration,
    parse_log_line,
    read_status,
)


def test_read_status(tmp_path):
    (tmp_path / "status.json").write_text(
        json.dumps({"code": "HD-A-001", "recording": True,
                    "sent_at": datetime.now().astimezone().isoformat()}), encoding="utf-8"
    )
    assert read_status(tmp_path)["code"] == "HD-A-001"
    assert read_status(tmp_path / "nope") == {}


def test_status_expires_and_recovers(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"recording": True, "online": True,
                                "sent_at": "2026-10-17T10:00:00+00:00"}), encoding="utf-8")
    fresh = read_status(tmp_path, datetime.fromisoformat("2026-10-17T10:00:05+00:00"))
    assert fresh["recording"] and not fresh["stale"]
    stale = read_status(tmp_path, datetime.fromisoformat("2026-10-17T10:01:00+00:00"))
    assert stale["stale"] and not stale["recording"] and not stale["online"]
    assert read_status(tmp_path, datetime.fromisoformat("2026-10-17T10:00:05+00:00"))["recording"]


def test_status_with_invalid_timestamp_is_unavailable(tmp_path):
    for data in ({"recording": True}, {"sent_at": "bad"}, []):
        (tmp_path / "status.json").write_text(json.dumps(data), encoding="utf-8")
        assert read_status(tmp_path) == {}


def test_build_timeline_only_today(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    now = datetime.now()
    today = now.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        {"path": "a.ts", "started_at": today, "size": 1, "integrity": "ok", "status": "synced"},
        {"path": "b.ts", "started_at": today, "size": 1, "integrity": "ok", "status": "local"},
        {"path": "c.ts", "started_at": "2020-01-01T00:00:00", "size": 1, "integrity": "ok",
         "status": "synced"},
    ]
    (state / "segments.jsonl").write_text(
        "\n".join(json.dumps(ln) for ln in lines), encoding="utf-8"
    )
    bars = build_timeline(state, now)
    assert len(bars) == 2, "昨天的段不画进今天的轴"
    assert [b.synced for b in bars] == [True, False]


def test_parse_log_line():
    parsed = parse_log_line("2026-10-17 10:40:05,123 INFO    agent: 分段已上传")
    assert parsed == {"time": "2026-10-17 10:40:05", "level": "INFO", "msg": "分段已上传"}


def test_format_duration():
    assert format_duration(0) == "0 分"
    assert format_duration(3600 * 2 + 600) == "2 小时 10 分"
    assert format_duration(86400 * 3 + 3600 * 5) == "3 天 5 小时"


def test_collect_files(tmp_path):
    (tmp_path / "a.ts").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.mkv").write_bytes(b"x")
    names = sorted(f.name for f in collect_files([tmp_path]))
    assert names == ["a.ts", "b.mp4", "d.mkv"], "只收视频文件，文件夹递归"
