"""服务端滚动覆盖：保留天数删除、加锁跳过、开关关闭、磁盘 507、空间不足事件。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from everrec_core.protocol import Checks, Heartbeat, UploadRequest
from everrec_server.db import Event, Recording
from everrec_server.main import create_app
from everrec_server.settings import Settings


def _register(c: TestClient, code: str = "HD-A-012") -> str:
    """首次心跳注册设备，返回设备令牌（tasks.md WP5）。"""
    r = c.post("/agent/v1/heartbeat", json=Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=True, profile="720P / 10 帧", checks=Checks(),
    ).model_dump(mode="json"))
    return r.json()["token"]


def _settings(tmp_path, **kw) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/t.db",
        recordings_dir=tmp_path / "recordings",
        sweep_seconds=3600,
        retention_seconds=3600,
        **kw,
    )


def _add_recording(
    s: Session, settings: Settings, code: str, started_at: datetime, locked: bool = False
) -> tuple[int, str]:
    """直接在库里插一条 recording 并落地一个文件，返回 (id, 文件绝对路径字符串)。"""
    d = settings.recordings_dir / code / f"{started_at:%Y}" / f"{started_at:%m}" / f"{started_at:%d}"
    d.mkdir(parents=True, exist_ok=True)
    fname = f"{code}_{started_at:%H%M%S}.ts"
    p = d / fname
    p.write_bytes(b"x" * 10)
    rec = Recording(
        code=code, path=str(p), start_at=started_at, end_at=started_at + timedelta(minutes=5),
        size=10, sha256=hashlib.sha256(b"x" * 10).hexdigest(), integrity="ok",
        status="synced", locked=locked,
    )
    s.add(rec)
    s.commit()
    s.refresh(rec)
    return rec.id, str(p)


def test_retention_deletes_expired_segments(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    now = datetime.now(UTC)
    with Session(create_engine(settings.database_url)) as s:
        old_id, old_path = _add_recording(s, settings, "HD-A-012", now - timedelta(days=100))
        new_id, new_path = _add_recording(s, settings, "HD-A-012", now - timedelta(days=1))

    app.state.retention_once()

    assert not Path(old_path).exists()
    assert Path(new_path).exists()
    with Session(create_engine(settings.database_url)) as s:
        assert s.get(Recording, old_id) is None
        assert s.get(Recording, new_id) is not None


def test_retention_skips_locked_segments(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    now = datetime.now(UTC)
    with Session(create_engine(settings.database_url)) as s:
        locked_id, locked_path = _add_recording(
            s, settings, "HD-A-012", now - timedelta(days=100), locked=True
        )

    app.state.retention_once()

    assert Path(locked_path).exists()
    with Session(create_engine(settings.database_url)) as s:
        assert s.get(Recording, locked_id) is not None


def test_retention_disabled_does_not_delete(tmp_path):
    settings = _settings(tmp_path, rollover_enabled=False)
    app = create_app(settings)
    now = datetime.now(UTC)
    with Session(create_engine(settings.database_url)) as s:
        old_id, old_path = _add_recording(s, settings, "HD-A-012", now - timedelta(days=100))

    app.state.retention_once()

    assert Path(old_path).exists()
    with Session(create_engine(settings.database_url)) as s:
        assert s.get(Recording, old_id) is not None


def test_upload_rejected_when_disk_critical(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    app = create_app(settings)
    monkeypatch.setattr("everrec_server.uploads.disk_usage_ratio", lambda _p: 0.96)
    req = UploadRequest(
        code="HD-A-012", filename="HD-A-012_091000.ts",
        started_at=datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 15, 9, 5, tzinfo=UTC),
        size=1, sha256=hashlib.sha256(b"x").hexdigest(), integrity="ok", channel="realtime",
    )
    with TestClient(app) as c:
        token = _register(c)
        r = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                   headers={"X-Agent-Token": token})
        assert r.status_code == 507


def test_upload_allowed_below_critical(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    app = create_app(settings)
    monkeypatch.setattr("everrec_server.uploads.disk_usage_ratio", lambda _p: 0.5)
    req = UploadRequest(
        code="HD-A-012", filename="HD-A-012_091000.ts",
        started_at=datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 15, 9, 5, tzinfo=UTC),
        size=1, sha256=hashlib.sha256(b"x").hexdigest(), integrity="ok", channel="realtime",
    )
    with TestClient(app) as c:
        token = _register(c)
        r = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                   headers={"X-Agent-Token": token})
        assert r.status_code == 200


def test_disk_warn_emits_storage_low_event(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    app = create_app(settings)
    monkeypatch.setattr("everrec_server.main.disk_usage_ratio", lambda _p: 0.86)
    app.state.retention_once()
    with Session(create_engine(settings.database_url)) as s:
        ev = s.scalar(select(Event).where(Event.type == "storage.low"))
        assert ev is not None
        assert ev.severity == "warning"


def test_disk_warn_event_only_once(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    app = create_app(settings)
    monkeypatch.setattr("everrec_server.main.disk_usage_ratio", lambda _p: 0.86)
    app.state.retention_once()
    app.state.retention_once()
    with Session(create_engine(settings.database_url)) as s:
        rows = s.scalars(select(Event).where(Event.type == "storage.low")).all()
        assert len(rows) == 1, "同一告警状态不重复产生事件"


def test_lock_and_unlock(tmp_path):
    settings = _settings(tmp_path, admin_password="test")
    app = create_app(settings)
    now = datetime.now(UTC)
    with Session(create_engine(settings.database_url)) as s:
        rid, _ = _add_recording(s, settings, "HD-A-012", now - timedelta(days=1))

    with TestClient(app) as c:
        assert c.post("/api/v1/login", json={"password": "test"}).status_code == 200
        assert c.post(f"/api/v1/recordings/{rid}/lock").json()["locked"] is True
        assert c.post(f"/api/v1/recordings/{rid}/unlock").json()["locked"] is False
        assert c.post("/api/v1/recordings/99999/lock").status_code == 404
