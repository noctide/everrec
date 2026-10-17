"""缩略图：Agent 上传、设备墙 snapshot 接口、has_thumbnail 标记。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from everrec_core.protocol import Heartbeat
from everrec_server.main import create_app
from everrec_server.settings import Settings


def _hb(code="HD-A-001") -> dict:
    return Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=True, profile="standard",
    ).model_dump(mode="json")


def _app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t.db",
        recordings_dir=tmp_path / "recordings",
        sweep_seconds=3600,
    )
    return create_app(settings), settings


def test_thumbnail_upload_and_snapshot(tmp_path):
    app, settings = _app(tmp_path)
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    with TestClient(app) as c:
        c.post("/agent/v1/heartbeat", json=_hb())
        dev = c.get("/api/v1/devices").json()["items"][0]
        assert dev["has_thumbnail"] is False and dev["thumbnail_at"] is None

        r = c.put("/agent/v1/thumbnail", params={"code": "HD-A-001"}, content=jpeg)
        assert r.status_code == 200

        dev = c.get("/api/v1/devices").json()["items"][0]
        assert dev["has_thumbnail"] is True and dev["thumbnail_at"] is not None

        snap = c.get(f"/api/v1/devices/{dev['id']}/snapshot")
        assert snap.status_code == 200
        assert snap.headers["content-type"].startswith("image/jpeg")
        assert snap.content == jpeg

    # 文件确实落盘在 _thumbnails/
    assert (settings.recordings_dir / "_thumbnails" / "HD-A-001.jpg").read_bytes() == jpeg


def test_snapshot_404_when_no_thumbnail(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/agent/v1/heartbeat", json=_hb())
        dev = c.get("/api/v1/devices").json()["items"][0]
        assert c.get(f"/api/v1/devices/{dev['id']}/snapshot").status_code == 404


def test_thumbnail_rejects_bad_code(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        assert c.put("/agent/v1/thumbnail", params={"code": "../../etc"}, content=b"x").status_code == 400
