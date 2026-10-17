"""服务端回放：上传转封装 MP4、按时间段查分段、Range 播放、下载、操作日志。"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from everrec_core.ffmpeg import ffmpeg_binary
from everrec_core.protocol import Checks, Heartbeat, UploadRequest
from everrec_server.db import OperationLog, Recording
from everrec_server.main import create_app
from everrec_server.settings import Settings


def _app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t.db",
        recordings_dir=tmp_path / "recordings",
        sweep_seconds=3600,
        retention_seconds=3600,
        admin_password="test",
        secret_key="test-secret",
    )
    return create_app(settings), settings


def _register(c: TestClient, code: str = "HD-A-012") -> str:
    """首次心跳注册设备，返回设备令牌（tasks.md WP5）。"""
    r = c.post("/agent/v1/heartbeat", json=Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=True, profile="720P / 10 帧", checks=Checks(),
    ).model_dump(mode="json"))
    return r.json()["token"]


def _login(c: TestClient) -> None:
    assert c.post("/api/v1/login", json={"password": "test"}).status_code == 200, "管理员登录失败"


def _make_ts(path: Path, seconds: int = 2) -> bytes:
    subprocess.run(
        [ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=5",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mpegts", str(path)],
        check=True,
    )
    return path.read_bytes()


def _req(content: bytes, **kw) -> UploadRequest:
    return UploadRequest(
        code="HD-A-012",
        filename="HD-A-012_091000.ts",
        started_at=datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 15, 9, 5, tzinfo=UTC),
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        integrity="ok",
        channel="realtime",
        **kw,
    )


def _upload(c: TestClient, content: bytes, req: UploadRequest) -> int:
    token = _register(c, req.code)
    uid = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                 headers={"X-Agent-Token": token}).json()["upload_id"]
    assert c.put(f"/agent/v1/uploads/{uid}?offset=0", content=content).status_code == 200
    comp = c.post(f"/agent/v1/uploads/{uid}/complete")
    assert comp.status_code == 200, comp.text
    return comp.json()["recording_id"]


def _add_recording(
    settings: Settings, code: str, started_at: datetime, content: bytes = b"x" * 100,
    integrity: str = "ok",
) -> tuple[int, Path]:
    d = settings.recordings_dir / code / f"{started_at:%Y}" / f"{started_at:%m}" / f"{started_at:%d}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{code}_{started_at:%H%M%S}.mp4"
    p.write_bytes(content)
    with Session(create_engine(settings.database_url)) as s:
        rec = Recording(
            code=code, path=str(p), start_at=started_at,
            end_at=started_at + timedelta(minutes=5), size=len(content),
            sha256=hashlib.sha256(content).hexdigest(), integrity=integrity,
            status="synced", locked=False,
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        return rec.id, p


def test_upload_transcodes_to_mp4(tmp_path):
    app, settings = _app(tmp_path)
    content = _make_ts(tmp_path / "sample.ts")
    req = _req(content)
    with TestClient(app) as c:
        rid = _upload(c, content, req)
    with Session(create_engine(settings.database_url)) as s:
        rec = s.get(Recording, rid)
        assert rec.path.endswith(".mp4"), "转封装后 recording.path 应指向 MP4"
    dest_dir = settings.recordings_dir / "HD-A-012" / "2026" / "10" / "15"
    assert (dest_dir / "HD-A-012_091000.mp4").exists()
    assert not (dest_dir / "HD-A-012_091000.ts").exists(), "转封装成功后应删除 TS"


def test_file_endpoint_serves_range_206(tmp_path):
    app, settings = _app(tmp_path)
    rid, p = _add_recording(settings, "HD-A-012", datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
                            content=b"0123456789" * 30)
    with TestClient(app) as c:
        _login(c)
        full = c.get(f"/api/v1/recordings/{rid}/file")
        assert full.status_code == 200 and len(full.content) == 300
        r = c.get(f"/api/v1/recordings/{rid}/file", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206, "浏览器拖进度条依赖 HTTP Range 206"
        assert r.content == b"0123456789" * 10


def test_list_recordings_filters(tmp_path):
    app, settings = _app(tmp_path)
    t = datetime(2026, 10, 15, tzinfo=UTC)
    _add_recording(settings, "HD-A-012", t.replace(hour=9, minute=0))
    _add_recording(settings, "HD-A-012", t.replace(hour=10, minute=0))
    _add_recording(settings, "HD-B-001", t.replace(hour=9, minute=0))

    with TestClient(app) as c:
        _login(c)
        r = c.get("/api/v1/recordings", params={
            "code": "HD-A-012",
            "from": t.replace(hour=8).isoformat(),
            "to": t.replace(hour=9, minute=30).isoformat(),
        })
        assert r.status_code == 200
        assert len(r.json()) == 1, "只应命中 09:00 那一段"
        assert r.json()[0]["code"] == "HD-A-012"


def test_download_writes_operation_log(tmp_path):
    app, settings = _app(tmp_path)
    rid, _ = _add_recording(settings, "HD-A-012", datetime(2026, 10, 15, 9, 0, tzinfo=UTC))
    with TestClient(app) as c:
        _login(c)
        r = c.get(f"/api/v1/recordings/{rid}/download")
        assert r.status_code == 200
        assert r.headers.get("content-disposition", "").startswith("attachment")
    with Session(create_engine(settings.database_url)) as s:
        log = s.scalar(select(OperationLog).where(OperationLog.action == "download"))
        assert log is not None and log.recording_id == rid


def test_view_writes_operation_log(tmp_path):
    app, settings = _app(tmp_path)
    rid, _ = _add_recording(settings, "HD-A-012", datetime(2026, 10, 15, 9, 0, tzinfo=UTC))
    with TestClient(app) as c:
        _login(c)
        assert c.get(f"/api/v1/recordings/{rid}/file").status_code == 200
    with Session(create_engine(settings.database_url)) as s:
        log = s.scalar(select(OperationLog).where(OperationLog.action == "view"))
        assert log is not None and log.recording_id == rid


def test_lock_writes_operation_log(tmp_path):
    app, settings = _app(tmp_path)
    rid, _ = _add_recording(settings, "HD-A-012", datetime(2026, 10, 15, 9, 0, tzinfo=UTC))
    with TestClient(app) as c:
        _login(c)
        assert c.post(f"/api/v1/recordings/{rid}/lock").status_code == 200
    with Session(create_engine(settings.database_url)) as s:
        log = s.scalar(select(OperationLog).where(OperationLog.action == "lock"))
        assert log is not None and log.recording_id == rid
