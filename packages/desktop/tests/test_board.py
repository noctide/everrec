"""看板（首页红绿灯）的纯逻辑：灯色规则、数据来源切换。"""

from __future__ import annotations

import json
from datetime import datetime

from everrec_desktop.common import (
    build_board,
    light_from_server_status,
    local_light,
)


def _write_status(tmp_path, **kw):
    data = {"code": "HD-A-001", "recording": True, "checks": {},
            "sent_at": datetime.now().astimezone().isoformat(), **kw}
    (tmp_path / "status.json").write_text(json.dumps(data), encoding="utf-8")


def test_server_status_to_light():
    assert light_from_server_status("ok") == "green"
    assert light_from_server_status("offline_local") == "yellow"
    assert light_from_server_status("abnormal") == "red"
    assert light_from_server_status("lost") == "red"
    assert light_from_server_status("没见过的状态") == "red"


def test_local_light_rules():
    assert local_light({}) == "red"
    assert local_light({"stale": True, "recording": True}) == "red"
    assert local_light({"recording": False}) == "red"
    assert local_light({"recording": True, "checks": {"device": "fail"}}) == "red"
    assert local_light({"recording": True, "offline_since": "x"}) == "yellow"
    assert local_light({"recording": True, "checks": {"device": "ok"}}) == "green"
    assert local_light({"recording": False, "starting": True}) == "green"  # 刚启动的宽限期


def test_no_server_shows_local_only(tmp_path):
    _write_status(tmp_path)
    board = build_board("", "", tmp_path, fetch=lambda *_: 1 / 0)  # 没配服务器时绝不去取
    assert board.source == "local"
    assert [(t.code, t.light) for t in board.tiles] == [("HD-A-001", "green")]


def test_server_board_counts(tmp_path):
    items = [{"code": "A", "status": "ok"}, {"code": "B", "status": "lost", "problems": ["失联"]},
             {"code": "C", "status": "offline_local"}]
    board = build_board("http://x", "t", tmp_path, fetch=lambda s, t: items)
    assert board.source == "server"
    assert board.counts() == {"green": 1, "yellow": 1, "red": 1}


def test_server_unreachable_falls_back_to_local(tmp_path):
    _write_status(tmp_path)

    def boom(*_):
        raise RuntimeError("连不上服务器")

    board = build_board("http://x", "", tmp_path, fetch=boom)
    assert board.source == "local"
    assert "连不上服务器" in board.hint
    assert board.tiles[0].light == "green"
