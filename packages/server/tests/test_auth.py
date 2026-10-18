"""认证（tasks.md WP5）：登录、会话、只读 API 令牌、设备令牌签发/校验/吊销。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from everrec_core.protocol import Checks, Heartbeat
from everrec_server.auth import SESSION_COOKIE, hash_agent_token
from everrec_server.db import Agent, Meta
from everrec_server.main import create_app
from everrec_server.settings import Settings


def _app(tmp_path, **kw):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t.db",
        recordings_dir=tmp_path / "recordings",
        sweep_seconds=3600,
        retention_seconds=3600,
        admin_password="test",
        secret_key="test-secret",
        **kw,
    )
    return create_app(settings), settings


def _hb(code="HD-A-012") -> dict:
    return Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=True, profile="720P / 10 帧", checks=Checks(),
    ).model_dump(mode="json")


def _login(c: TestClient, password: str = "test"):
    return c.post("/api/v1/login", json={"password": password})


def _first_hb(c: TestClient, code="HD-A-012") -> str:
    return c.post("/agent/v1/heartbeat", json=_hb(code)).json()["token"]


def _next_hb(c: TestClient, token: str, code="HD-A-012"):
    return c.post("/agent/v1/heartbeat", json=_hb(code), headers={"X-Agent-Token": token})


def test_reads_public_writes_require_login(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        # 读接口公开，便于现场维护人员直接看状态
        assert c.get("/api/v1/devices").status_code == 200
        assert c.get("/api/v1/settings").status_code == 200
        # 写操作未登录被拒
        assert c.put("/api/v1/settings/global", json={"quality": "lean"}).status_code == 401
        # 登录接口本身不需要登录
        assert c.post("/api/v1/login", json={"password": "wrong"}).status_code == 401


def test_readonly_api_token_reads_but_cannot_write(tmp_path):
    """EVERREC_API_TOKEN：其他系统查询用，能读，任何写操作一律 403。"""
    app, _ = _app(tmp_path, api_token="ro-token")
    with TestClient(app) as c:
        hdr = {"Authorization": "Bearer ro-token"}
        assert c.get("/api/v1/devices", headers=hdr).status_code == 200
        assert c.put("/api/v1/settings/global", json={"quality": "lean"},
                     headers=hdr).status_code == 403
        # 没登录也没带令牌，仍是 401（和"只读"区分开）
        assert c.put("/api/v1/settings/global", json={"quality": "lean"}).status_code == 401


def test_auth_status_reports_login_state(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        # 未登录：logged_in=false（接口本身公开可访问）
        assert c.get("/api/v1/auth/status").json() == {"logged_in": False}
        # 登录后：logged_in=true
        _login(c)
        assert c.get("/api/v1/auth/status").json() == {"logged_in": True}


def test_login_sets_session_cookie_and_grants_write(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        assert _login(c).status_code == 200
        assert SESSION_COOKIE in c.cookies
        assert c.put("/api/v1/settings/global", json={"quality": "lean"}).status_code == 200
        c.post("/api/v1/logout")
        assert c.put("/api/v1/settings/global", json={"quality": "lean"}).status_code == 401


def test_first_heartbeat_issues_token_and_later_requires_it(tmp_path):
    app, settings = _app(tmp_path)
    with TestClient(app) as c:
        token = _first_hb(c)
        assert token, "首次心跳应签发设备令牌"
        # 后续心跳无令牌被拒
        assert c.post("/agent/v1/heartbeat", json=_hb()).status_code == 401
        # 带令牌通过
        assert _next_hb(c, token).status_code == 200
    # 服务端只存哈希，不存明文
    with Session(create_engine(settings.database_url)) as s:
        agent = s.scalar(select(Agent).where(Agent.code == "HD-A-012"))
        assert agent.token_hash == hash_agent_token(token)
        assert agent.token_hash != token


def test_revoke_device_rejects_heartbeat_and_upload(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        _login(c)
        token = _first_hb(c)
        assert _next_hb(c, token).status_code == 200
        assert c.post("/api/v1/devices/HD-A-012/revoke").status_code == 200
        # 吊销后心跳与上传 init 都被拒；Agent 走离线模式照常录制
        assert _next_hb(c, token).status_code == 401
        r = c.post("/agent/v1/uploads", json={
            "code": "HD-A-012", "filename": "HD-A-012_091000.ts",
            "started_at": "2026-10-15T09:00:00Z", "ended_at": "2026-10-15T09:05:00Z",
            "size": 1, "sha256": "00", "integrity": "ok", "channel": "realtime",
        }, headers={"X-Agent-Token": token})
        assert r.status_code == 401


def test_password_hash_stored_not_plaintext(tmp_path):
    app, settings = _app(tmp_path)
    with TestClient(app) as c:
        assert _login(c).status_code == 200
    with Session(create_engine(settings.database_url)) as s:
        row = s.get(Meta, "admin_password_hash")
        assert row is not None and "test" not in row.value


def test_settings_writes_require_login(tmp_path):
    """改配置必须先登录管理员会话。"""
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        assert c.put("/api/v1/settings/global", json={"quality": "lean"}).status_code == 401
        _login(c)
        assert c.put("/api/v1/settings/global", json={"quality": "lean"}).status_code == 200
