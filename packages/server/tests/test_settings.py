"""服务端设置：全局 + 设备覆盖、下发到心跳、非法值拒绝、位置选项、画质表。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from everrec_core.protocol import Checks, Heartbeat
from everrec_server.main import create_app
from everrec_server.settings import Settings


def _hb(code="HD-A-012", **kw) -> dict:
    return Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=True, profile="720P / 10 帧", checks=Checks(), **kw,
    ).model_dump(mode="json")


def _app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t.db",
        recordings_dir=tmp_path / "recordings",
        sweep_seconds=3600,
        retention_seconds=3600,
        admin_password="test",
        secret_key="test-secret",
    )
    return create_app(settings)


def _login(c: TestClient) -> None:
    assert c.post("/api/v1/login", json={"password": "test"}).status_code == 200, "管理员登录失败"


def test_settings_roundtrip_and_version_bumps(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        assert c.get("/api/v1/settings").json()["config_version"] == 0
        r = c.put("/api/v1/settings/global", json={"quality": "lean", "watermark": False})
        assert r.status_code == 200
        state = c.get("/api/v1/settings").json()
        assert state["config_version"] == 1
        assert state["global"]["quality"] == "lean"
        assert state["global"]["watermark"] is False


def test_settings_reject_invalid_values(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        assert c.put("/api/v1/settings/global", json={"quality": "ultra"}).status_code == 422
        assert c.put("/api/v1/settings/global", json={"resolution": "4k"}).status_code == 422
        assert c.put("/api/v1/settings/global", json={"fps": 30}).status_code == 422
        assert c.put("/api/v1/settings/global", json={"bitrate": 999}).status_code == 422


def test_global_settings_pushed_in_heartbeat(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        c.put("/api/v1/settings/global", json={"quality": "lean"})
        reply = c.post("/agent/v1/heartbeat", json=_hb()).json()
        assert reply["config_version"] == 1
        assert reply["settings"]["quality"] == "lean"


def test_device_override_pushed_only_to_that_device(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        c.put("/api/v1/settings/global", json={"quality": "standard"})
        c.put("/api/v1/settings/device/HD-A-012", json={"quality": "lean"})
        a = c.post("/agent/v1/heartbeat", json=_hb(code="HD-A-012")).json()
        b = c.post("/agent/v1/heartbeat", json=_hb(code="HD-A-999")).json()
        assert a["settings"]["quality"] == "lean", "单设备覆盖应生效"
        assert b["settings"]["quality"] == "standard", "其他设备仍走全局"


def test_retention_not_pushed_to_agent(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        c.put("/api/v1/settings/global", json={"retention_days": 30, "quality": "saver"})
        reply = c.post("/agent/v1/heartbeat", json=_hb()).json()
        assert "retention_days" not in reply["settings"], "保留天数是服务端设置，不下发"
        assert reply["settings"]["quality"] == "saver"


def test_location_options_roundtrip(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        loc = {"state": ["HD"], "district": ["PD"], "building": ["A"], "floor": ["3F"]}
        c.put("/api/v1/location-options", json=loc)
        reply = c.post("/agent/v1/heartbeat", json=_hb()).json()
        assert reply["location_options"]["state"] == ["HD"]
        assert reply["location_options"]["building"] == ["A"]


def test_quality_endpoint(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        q = c.get("/api/v1/quality").json()
        assert len(q["quality"]) == 5
        assert q["resolutions"] == ["360p", "480p", "720p", "1080p"]
        assert q["fps"] == [5, 8, 10, 12, 15]
        assert any(row["recommended"] for row in q["quality"])


def test_delete_device_override(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        c.put("/api/v1/settings/device/HD-A-012", json={"quality": "lean"})
        assert c.get("/api/v1/settings").json()["devices"]["HD-A-012"]["quality"] == "lean"
        c.delete("/api/v1/settings/device/HD-A-012")
        assert "HD-A-012" not in c.get("/api/v1/settings").json()["devices"]
