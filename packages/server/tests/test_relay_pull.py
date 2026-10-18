"""主服务器消费中转节点（tasks.md WP10）：拉取入库、只出一条失联告警、回放代理缓存、配置下发。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from everrec_core.protocol import Checks, Heartbeat, UploadRequest
from everrec_relay.main import create_relay_app
from everrec_relay.settings import RelaySettings
from everrec_server.db import Agent, Event, Recording
from everrec_server.main import create_app
from everrec_server.settings import Settings as ServerSettings

RELAY_URL = "http://relay.test"


def _relay_app(tmp_path):
    server = ServerSettings(
        database_url=f"sqlite:///{tmp_path}/relay.db",
        recordings_dir=tmp_path / "relay-rec", sweep_seconds=3600, retention_seconds=3600,
    )
    return create_relay_app(RelaySettings(server=server, pull_token="relay-secret"))


def _main_app(tmp_path, relays=None):
    settings = ServerSettings(
        database_url=f"sqlite:///{tmp_path}/main.db",
        recordings_dir=tmp_path / "main-rec", sweep_seconds=3600, retention_seconds=3600,
        admin_password="test", secret_key="test-secret",
        relays=relays if relays is not None else {"sub1": RELAY_URL},
        relay_pull_token="relay-secret",
    )
    return create_app(settings), settings


def _hb(code="SUB-A-01", **kw) -> dict:
    return Heartbeat(
        code=code, hostname="PC-SUB", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=True, profile="720P / 10 帧", checks=Checks(**kw.pop("checks", {})), **kw,
    ).model_dump(mode="json")


def _upload(c: TestClient, token: str, code="SUB-A-01", content=b"x" * 64) -> None:
    req = UploadRequest(
        code=code, filename=f"{code}_091000.ts",
        started_at=datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 15, 9, 5, tzinfo=UTC),
        size=len(content), sha256=hashlib.sha256(content).hexdigest(),
        integrity="ok", channel="realtime",
    )
    uid = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                 headers={"X-Agent-Token": token}).json()["upload_id"]
    c.put(f"/agent/v1/uploads/{uid}?offset=0", content=content)
    assert c.post(f"/agent/v1/uploads/{uid}/complete").status_code == 200


def _route_httpx_to_relay(monkeypatch, rc: TestClient):
    """把主服务器发出的 httpx.post/get 路由到 relay 的 TestClient，做真端到端而不占端口。"""

    def fake_post(url, json=None, headers=None, timeout=None):
        return rc.post(url.replace(RELAY_URL, ""), json=json, headers=headers)

    def fake_get(url, headers=None, timeout=None):
        return rc.get(url.replace(RELAY_URL, ""), headers=headers)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)


def test_puller_ingests_devices_and_recordings(tmp_path, monkeypatch):
    relay_app = _relay_app(tmp_path)
    main_app, settings = _main_app(tmp_path)
    with TestClient(relay_app) as rc, TestClient(main_app) as mc:
        mc.post("/api/v1/login", json={"password": "test"})
        token = rc.post("/agent/v1/heartbeat", json=_hb()).json()["token"]
        _upload(rc, token)
        _route_httpx_to_relay(monkeypatch, rc)
        mc.app.state.relay_pull_once()

        # 设备墙出现这台设备，标记为经中转节点
        items = mc.get("/api/v1/devices").json()["items"]
        assert [d["code"] for d in items] == ["SUB-A-01"]
        with Session(settings.database_url) as s:
            agent = s.scalar(select(Agent).where(Agent.code == "SUB-A-01"))
            assert agent.via == "relay" and agent.relay == "sub1"
        # 录像索引进来了，文件留在 relay
        recs = mc.get("/api/v1/recordings", params={
            "code": "SUB-A-01", "from": "2026-10-15T00:00:00Z", "to": "2026-10-16T00:00:00Z",
        }).json()
        assert len(recs) == 1
        with Session(settings.database_url) as s:
            r = s.scalar(select(Recording).where(Recording.code == "SUB-A-01"))
            assert r.relay_id == "sub1" and r.relay_rec_id is not None


def test_relay_down_raises_single_alert_not_per_device(tmp_path, monkeypatch):
    relay_app = _relay_app(tmp_path)
    main_app, settings = _main_app(tmp_path)
    with TestClient(relay_app) as rc, TestClient(main_app) as mc:
        rc.post("/agent/v1/heartbeat", json=_hb())
        rc.post("/agent/v1/heartbeat", json=_hb(code="SUB-A-02"))
        _route_httpx_to_relay(monkeypatch, rc)

        def boom(*a, **k):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "post", boom)
        for _ in range(4):
            mc.app.state.relay_pull_once()
        with Session(settings.database_url) as s:
            lost = s.scalars(select(Event).where(Event.type == "relay.lost")).all()
            assert len(lost) == 1, "中转节点失联只出一条告警，不逐台报"
            assert "sub1" in lost[0].summary


def test_playback_proxies_relay_segment_with_cache(tmp_path, monkeypatch):
    relay_app = _relay_app(tmp_path)
    main_app, settings = _main_app(tmp_path)
    content = b"y" * 200
    with TestClient(relay_app) as rc, TestClient(main_app) as mc:
        mc.post("/api/v1/login", json={"password": "test"})
        token = rc.post("/agent/v1/heartbeat", json=_hb()).json()["token"]
        _upload(rc, token, content=content)
        _route_httpx_to_relay(monkeypatch, rc)
        mc.app.state.relay_pull_once()

        rid = mc.get("/api/v1/recordings", params={
            "code": "SUB-A-01", "from": "2026-10-15T00:00:00Z", "to": "2026-10-16T00:00:00Z",
        }).json()[0]["id"]
        f = mc.get(f"/api/v1/recordings/{rid}/file")
        assert f.status_code == 200 and f.content == content
        # 第二次直接命中缓存（把 relay 的 httpx.get 捭掉，若仍 200 说明走的是缓存）
        monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
        again = mc.get(f"/api/v1/recordings/{rid}/file")
        assert again.status_code == 200 and again.content == content


def test_config_pushed_main_to_relay_agents(tmp_path, monkeypatch):
    relay_app = _relay_app(tmp_path)
    main_app, settings = _main_app(tmp_path)
    with TestClient(relay_app) as rc, TestClient(main_app) as mc:
        mc.post("/api/v1/login", json={"password": "test"})
        mc.put("/api/v1/settings/global", json={"quality": "lean", "watermark": False})
        token = rc.post("/agent/v1/heartbeat", json=_hb()).json()["token"]
        _route_httpx_to_relay(monkeypatch, rc)
        mc.app.state.relay_pull_once()
        reply = rc.post("/agent/v1/heartbeat", json=_hb(), headers={"X-Agent-Token": token}).json()
        assert reply["settings"]["quality"] == "lean"


class Session:
    """轻量会话上下文，直接用 URL 建引擎查库。"""

    def __init__(self, url):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        self._s = sessionmaker(create_engine(url), expire_on_commit=False)()

    def __enter__(self):
        return self._s

    def __exit__(self, *a):
        self._s.close()
