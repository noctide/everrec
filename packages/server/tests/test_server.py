from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from everrec_core.protocol import Checks, Heartbeat
from everrec_server.db import Event
from everrec_server.main import create_app
from everrec_server.settings import Settings


def _hb(code="HD-PD-A-3F-012", recording=True, **kw) -> dict:
    return Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=recording, profile="xlow", checks=Checks(**kw.pop("checks", {})), **kw,
    ).model_dump(mode="json")


def _app(tmp_path, lost_after=120):
    url = f"sqlite:///{tmp_path}/t.db"
    settings = Settings(
        database_url=url, lost_after_seconds=lost_after, sweep_seconds=3600,
        admin_password="test", secret_key="test-secret",
    )
    return create_app(settings), url


def _events(url):
    with Session(create_engine(url)) as s:
        return [e.type for e in s.scalars(select(Event).order_by(Event.id))]


def _login(c: TestClient) -> None:
    assert c.post("/api/v1/login", json={"password": "test"}).status_code == 200, "管理员登录失败"


def _first_hb(c: TestClient, **kw) -> str:
    """首次心跳注册设备，返回签发的设备令牌（tasks.md WP5）。"""
    r = c.post("/agent/v1/heartbeat", json=_hb(**kw))
    return r.json()["token"]


def _next_hb(c: TestClient, token: str, **kw):
    """带设备令牌的后续心跳。"""
    return c.post("/agent/v1/heartbeat", json=_hb(**kw), headers={"X-Agent-Token": token})


def test_heartbeat_registers_and_lists(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        assert c.post("/agent/v1/heartbeat", json=_hb()).status_code == 200
        c.post("/agent/v1/heartbeat", json=_hb(code="SUB-B-2F-001", pending_sync=12,
                                               offline_since=datetime.now(UTC).isoformat()))
        body = c.get("/api/v1/devices").json()
        status = {d["code"]: d["status"] for d in body["items"]}
        assert status == {"HD-PD-A-3F-012": "ok", "SUB-B-2F-001": "offline_local"}
        assert c.get("/api/v1/devices", params={"prefix": "SUB"}).json()["items"][0]["code"] == "SUB-B-2F-001"


def test_stalled_recording_is_abnormal_and_raises_event(tmp_path):
    app, url = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        token = _first_hb(c)
        _next_hb(c, token, recording=False, checks={"dataflow": "fail"},
                 problems=["录像文件 31 秒无增长"])
        d = c.get("/api/v1/devices").json()["items"][0]
        assert d["status"] == "abnormal" and d["problems"]
        _next_hb(c, token)
    assert _events(url) == ["recording.stopped", "device.recovered"]


def test_starting_grace_is_not_abnormal(tmp_path):
    app, url = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        token = _first_hb(c, recording=False, starting=True)
        assert c.get("/api/v1/devices").json()["items"][0]["status"] == "ok"
        _next_hb(c, token)
    assert _events(url) == []


def test_lost_detected_by_server_then_recovers(tmp_path):
    app, url = _app(tmp_path, lost_after=0)
    with TestClient(app) as c:
        _login(c)
        token = _first_hb(c)
        app.state.sweep_once()
        assert c.get("/api/v1/devices").json()["items"][0]["status"] == "lost"
        _next_hb(c, token)
    assert _events(url) == ["device.lost", "device.recovered"]


def test_mass_lost_flag(tmp_path):
    app, _ = _app(tmp_path, lost_after=0)
    with TestClient(app) as c:
        _login(c)
        for i in range(5):
            c.post("/agent/v1/heartbeat", json=_hb(code=f"HD-{i}"))
        assert c.get("/api/v1/devices").json()["mass_lost"] is True
