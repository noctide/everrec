"""中转节点（tasks.md WP10）：南向复用服务端接口、北向增量 pull、配置回传、分段代理。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from everrec_core.protocol import Checks, Heartbeat, UploadRequest
from everrec_relay.main import create_relay_app
from everrec_relay.settings import RelaySettings
from everrec_server.settings import Settings as ServerSettings


def _relay(tmp_path, pull_token="relay-secret"):
    server = ServerSettings(
        database_url=f"sqlite:///{tmp_path}/relay.db",
        recordings_dir=tmp_path / "recordings",
        sweep_seconds=3600,
        retention_seconds=3600,
        admin_password="test",
        secret_key="test-secret",
    )
    return create_relay_app(RelaySettings(server=server, pull_token=pull_token))


def _hb(code="SUB-A-01", recording=True, **kw) -> dict:
    return Heartbeat(
        code=code, hostname="PC-SUB", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=recording, profile="720P / 10 帧", checks=Checks(**kw.pop("checks", {})), **kw,
    ).model_dump(mode="json")


def _upload(c: TestClient, token: str, code="SUB-A-01", content=b"x" * 64) -> int:
    req = UploadRequest(
        code=code, filename=f"{code}_091000.ts",
        started_at=datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 15, 9, 5, tzinfo=UTC),
        size=len(content), sha256=__import__("hashlib").sha256(content).hexdigest(),
        integrity="ok", channel="realtime",
    )
    uid = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                 headers={"X-Agent-Token": token}).json()["upload_id"]
    c.put(f"/agent/v1/uploads/{uid}?offset=0", content=content)
    comp = c.post(f"/agent/v1/uploads/{uid}/complete")
    assert comp.status_code == 200, comp.text
    return comp.json()["recording_id"]


def test_southbound_heartbeat_and_upload(tmp_path):
    app = _relay(tmp_path)
    with TestClient(app) as c:
        token = c.post("/agent/v1/heartbeat", json=_hb()).json()["token"]
        assert token, "子公司 Agent 首次心跳应拿到令牌"
        assert c.get("/api/v1/devices", headers={"Authorization": "Bearer x"}).status_code == 401
        _upload(c, token)


def test_pull_requires_token(tmp_path):
    app = _relay(tmp_path)
    with TestClient(app) as c:
        assert c.post("/relay/v1/pull", json={}).status_code == 401
        assert c.post("/relay/v1/pull", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_pull_returns_devices_and_recordings_incrementally(tmp_path):
    app = _relay(tmp_path)
    hdr = {"Authorization": "Bearer relay-secret"}
    with TestClient(app) as c:
        token = c.post("/agent/v1/heartbeat", json=_hb()).json()["token"]
        _upload(c, token)
        r = c.post("/relay/v1/pull", json={"since_event": 0, "since_recording": 0}, headers=hdr)
        assert r.status_code == 200
        body = r.json()
        assert [d["code"] for d in body["devices"]] == ["SUB-A-01"]
        assert len(body["recordings"]) == 1 and body["recordings"][0]["code"] == "SUB-A-01"
        assert body["last_recording_id"] >= 1
        # 游标推进后再拉，不再返回同一条录像
        again = c.post("/relay/v1/pull", json={
            "since_event": body["last_event_id"], "since_recording": body["last_recording_id"],
        }, headers=hdr).json()
        assert again["recordings"] == [] and again["events"] == []


def test_pull_applies_config_to_agents(tmp_path):
    app = _relay(tmp_path)
    hdr = {"Authorization": "Bearer relay-secret"}
    with TestClient(app) as c:
        token = c.post("/agent/v1/heartbeat", json=_hb()).json()["token"]  # 注册设备
        c.post("/relay/v1/pull", json={"settings": {"quality": "lean", "watermark": False}}, headers=hdr)
        reply = c.post("/agent/v1/heartbeat", json=_hb(),
                       headers={"X-Agent-Token": token}).json()
        assert reply["settings"]["quality"] == "lean"
        assert reply["settings"]["watermark"] is False


def test_relay_recording_file_served_to_main_server(tmp_path):
    app = _relay(tmp_path)
    hdr = {"Authorization": "Bearer relay-secret"}
    with TestClient(app) as c:
        token = c.post("/agent/v1/heartbeat", json=_hb()).json()["token"]
        content = b"y" * 128
        _upload(c, token, content=content)
        rid = c.post("/relay/v1/pull", json={"since_recording": 0}, headers=hdr).json()["recordings"][0]["id"]
        f = c.get(f"/relay/v1/recordings/{rid}/file", headers=hdr)
        assert f.status_code == 200 and f.content == content
        assert c.get(f"/relay/v1/recordings/{rid}/file").status_code == 401
