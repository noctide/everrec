"""服务端安装包版本管理：上传、列表、删除、Agent 检查更新与下载（tasks.md WP9）。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from everrec_core.protocol import Checks, Heartbeat
from everrec_server.main import create_app
from everrec_server.settings import Settings


def _app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t.db",
        recordings_dir=tmp_path / "recordings",
        releases_dir=tmp_path / "releases",
        sweep_seconds=3600,
        admin_password="test",
        secret_key="test-secret",
    )
    return create_app(settings)


def _login(c: TestClient) -> None:
    assert c.post("/api/v1/login", json={"password": "test"}).status_code == 200


def _register(c: TestClient, code: str = "HD-A-012") -> str:
    r = c.post("/agent/v1/heartbeat", json=Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=True, profile="720P / 10 帧", checks=Checks(),
    ).model_dump(mode="json"))
    return r.json()["token"]


def _upload(c: TestClient, version: str, data: bytes = b"setup-exe-bytes"):
    return c.post(
        "/api/v1/releases",
        params={"version": version, "notes": "测试版本"},
        files={"file": ("setup.exe", data, "application/octet-stream")},
    )


def test_upload_list_delete_release(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        r = _upload(c, "1.0.0")
        assert r.status_code == 200
        assert r.json()["sha256"]
        rows = c.get("/api/v1/releases").json()
        assert len(rows) == 1 and rows[0]["version"] == "1.0.0"
        rid = rows[0]["id"]
        assert c.delete(f"/api/v1/releases/{rid}").status_code == 200
        assert c.get("/api/v1/releases").json() == []


def test_upload_requires_login(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        assert _upload(c, "1.0.0").status_code == 401


def test_upload_duplicate_version_rejected(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        assert _upload(c, "1.0.0").status_code == 200
        assert _upload(c, "1.0.0").status_code == 409


def test_agent_version_check_and_download(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        token = _register(c)
        data = b"installer-payload"
        _upload(c, "2.0.0", data)
        # 把更新时段设为覆盖当前小时，auto 模式下 should_update 才为真
        h = datetime.now(UTC).hour
        c.put("/api/v1/settings/global",
              json={"update_mode": "auto", "update_window_start": h,
                    "update_window_end": (h + 1) % 24})
        vc = c.get("/agent/v1/version-check", params={"code": "HD-A-012", "version": "0.1.0"},
                   headers={"X-Agent-Token": token}).json()
        assert vc["update_available"] and vc["should_update"]
        assert vc["latest_version"] == "2.0.0" and vc["sha256"]
        # 下载安装包，字节一致
        r = c.get(vc["download_url"], headers={"X-Agent-Token": token})
        assert r.status_code == 200 and r.content == data


def test_agent_version_check_no_token(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        _register(c)
        assert c.get("/agent/v1/version-check",
                     params={"code": "HD-A-012", "version": "0.1.0"}).status_code == 401


def test_agent_up_to_date_no_update(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        token = _register(c)
        _upload(c, "0.1.0")
        vc = c.get("/agent/v1/version-check", params={"code": "HD-A-012", "version": "0.1.0"},
                   headers={"X-Agent-Token": token}).json()
        assert vc["update_available"] is False and vc["should_update"] is False


def test_notify_mode_reports_available_but_not_update(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        token = _register(c)
        _upload(c, "9.9.9")
        h = datetime.now(UTC).hour
        c.put("/api/v1/settings/global",
              json={"update_mode": "notify", "update_window_start": h,
                    "update_window_end": (h + 1) % 24})
        vc = c.get("/agent/v1/version-check", params={"code": "HD-A-012", "version": "0.1.0"},
                   headers={"X-Agent-Token": token}).json()
        assert vc["update_available"] is True
        assert vc["should_update"] is False, "只提醒模式不该让 Agent 立即安装"


def test_update_settings_reject_invalid(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        assert c.put("/api/v1/settings/global", json={"update_mode": "sometimes"}).status_code == 422
        assert c.put("/api/v1/settings/global", json={"update_window_start": 99}).status_code == 422
