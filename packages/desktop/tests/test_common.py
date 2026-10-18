"""桌面端纯逻辑（与 Qt 解耦的部分）：status.json、时间轴、日志解析、文件展开。"""

from __future__ import annotations

import json
from datetime import datetime

from everrec_desktop.common import (
    DEFAULT_LEVELS,
    build_timeline,
    collect_files,
    format_duration,
    load_location_levels,
    overall_state,
    parse_log_line,
    read_status,
    snapshot_path,
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


def test_overall_state_levels():
    assert overall_state({})["level"] == "error"
    assert overall_state({"recording": False, "starting": True})["level"] == "info"
    assert overall_state({"recording": False})["level"] == "error"
    assert overall_state({"recording": True, "checks": {"device": "fail"}})["level"] == "warn"
    assert overall_state({"recording": True, "online": False})["level"] == "warn"
    assert overall_state({"recording": True, "online": None})["level"] == "ok"
    assert overall_state({"recording": True, "online": True})["level"] == "ok"


def test_location_levels_priority(tmp_path):
    state, conf = tmp_path / "state", tmp_path / "config"
    state.mkdir()
    # 什么文件都没有：用默认，每级都有选项
    assert [lv["key"] for lv in load_location_levels(state, conf)] == [x["key"] for x in DEFAULT_LEVELS]
    # 本机配置文件（完整格式）
    (conf).mkdir()
    (conf / "location_options.json").write_text(json.dumps({"levels": [
        {"key": "l1", "label": "一级", "options": ["A", {"value": "B", "label": "乙"}]},
    ]}), encoding="utf-8")
    lv = load_location_levels(state, conf)
    assert lv[0]["options"] == [{"value": "A", "label": "A"}, {"value": "B", "label": "乙"}]
    # 服务器下发的优先；服务器没给的级沿用本机的
    (state / "location_options.json").write_text(
        json.dumps({"dept": ["S1"], "section": ["D1"]}), encoding="utf-8")
    lv = load_location_levels(state, conf)
    assert [x["key"] for x in lv] == ["dept", "section", "l1"]
    assert lv[0]["label"] == "部门" and lv[0]["options"][0]["value"] == "S1"
    # 坏文件回退
    (state / "location_options.json").write_text("坏的", encoding="utf-8")
    (conf / "location_options.json").write_text("坏的", encoding="utf-8")
    assert load_location_levels(state, conf)[0]["key"] == "dept"


def test_ensure_location_file(tmp_path):
    from everrec_desktop.common import ensure_location_file
    path = ensure_location_file(tmp_path / "config")
    assert path.exists() and load_location_levels(tmp_path, tmp_path / "config")[0]["options"]


def test_snapshot_path(tmp_path):
    now = datetime(2026, 10, 18, 3, 54, 38)
    p = snapshot_path(tmp_path, "HD-PD-A-HQ-2F-003", now)
    assert p.name == "HD-PD-A-HQ-2F-003_20261018_035438.jpg"
    # 同一秒再保存一张：加 -2 后缀
    p.write_bytes(b"x")
    assert snapshot_path(tmp_path, "HD-PD-A-HQ-2F-003", now).name == \
        "HD-PD-A-HQ-2F-003_20261018_035438-2.jpg"
    # 非法字符替换成下划线；空编号用 unknown
    assert snapshot_path(tmp_path, "A:B*C?", now).name == "A_B_C__20261018_035438.jpg"
    assert snapshot_path(tmp_path, "", now).name == "unknown_20261018_035438.jpg"


def test_timeline_uses_real_duration(tmp_path):
    """分段时长不再固定 5 分钟：索引里有 duration 就按它画，老索引回退 5 分钟。"""
    state = tmp_path / "state"
    state.mkdir()
    now = datetime(2026, 10, 18, 12, 0, 0)
    lines = [
        {"path": "a.ts", "started_at": "2026-10-18T10:00:00", "size": 1, "integrity": "ok",
         "status": "synced", "duration": 3600},
        {"path": "b.ts", "started_at": "2026-10-18T11:00:00", "size": 1, "integrity": "ok",
         "status": "synced"},
    ]
    (state / "segments.jsonl").write_text("\n".join(json.dumps(ln) for ln in lines), encoding="utf-8")
    bars = build_timeline(state, now)
    assert abs(bars[0].end - bars[0].start - 1.0) < 1e-6, "索引里的 1 小时要画成 1 小时"
    assert abs(bars[1].end - bars[1].start - 5 / 60) < 1e-6, "没有 duration 的老索引按 5 分钟画"