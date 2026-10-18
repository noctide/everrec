"""服务端上传接口：分片续传、校验、去重、补传名额。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from everrec_core.protocol import Checks, Heartbeat, UploadRequest
from everrec_server.db import Recording
from everrec_server.main import create_app
from everrec_server.migrations import _ALEMBIC_DIR, run_migrations
from everrec_server.settings import Settings


def _app(tmp_path, backfill_limit=5):
    url = f"sqlite:///{tmp_path}/t.db"
    settings = Settings(
        database_url=url,
        recordings_dir=tmp_path / "recordings",
        backfill_limit=backfill_limit,
        sweep_seconds=3600,
    )
    return create_app(settings), url, settings


def _register(c: TestClient, code: str = "HD-A-012") -> str:
    """首次心跳注册设备，返回签发的设备令牌（tasks.md WP5）。"""
    r = c.post("/agent/v1/heartbeat", json=Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=True, profile="720P / 10 帧", checks=Checks(),
    ).model_dump(mode="json"))
    return r.json()["token"]


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


def test_upload_roundtrip_verifies_and_archives(tmp_path):
    app, url, settings = _app(tmp_path)
    content = b"x" * 2000
    req = _req(content)
    with TestClient(app) as c:
        token = _register(c)
        hdr = {"X-Agent-Token": token}
        init = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"), headers=hdr).json()
        assert init["received"] == 0 and not init["done"]
        uid = init["upload_id"]
        half = len(content) // 2
        first = c.put(f"/agent/v1/uploads/{uid}", params={"offset": 0}, content=content[:half])
        assert first.json()["received"] == half
        r = c.put(f"/agent/v1/uploads/{uid}", params={"offset": half}, content=content[half:])
        assert r.json()["received"] == len(content)
        comp = c.post(f"/agent/v1/uploads/{uid}/complete")
        assert comp.status_code == 200
        recording_id = comp.json()["recording_id"]

    with Session(create_engine(url)) as s:
        rec = s.scalar(select(Recording).where(Recording.id == recording_id))
        assert rec is not None
        assert rec.sha256 == req.sha256 and rec.code == "HD-A-012"
        assert rec.path.endswith("HD-A-012_091000.ts")
        assert rec.integrity == "ok"

    dest = settings.recordings_dir / "HD-A-012" / "2026" / "10" / "15" / "HD-A-012_091000.ts"
    assert dest.exists() and dest.read_bytes() == content


def test_upload_keeps_source(tmp_path):
    """录像来源随上传落库：screen = 桌面录屏，缺省按 camera（tasks.md WP12）。"""
    app, url, _settings = _app(tmp_path)
    content = b"x" * 1000
    req = _req(content, source="screen")
    with TestClient(app) as c:
        uid = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                     headers={"X-Agent-Token": _register(c)}).json()["upload_id"]
        assert c.put(f"/agent/v1/uploads/{uid}", params={"offset": 0},
                     content=content).status_code == 200
        assert c.post(f"/agent/v1/uploads/{uid}/complete").status_code == 200
    with Session(create_engine(url)) as s:
        assert s.scalar(select(Recording)).source == "screen"


def test_offset_mismatch_returns_409_with_correct_received(tmp_path):
    app, *_ = _app(tmp_path)
    content = b"abcdef"
    with TestClient(app) as c:
        uid = c.post("/agent/v1/uploads", json=_req(content).model_dump(mode="json"),
                     headers={"X-Agent-Token": _register(c)}).json()["upload_id"]
        r = c.put(f"/agent/v1/uploads/{uid}", params={"offset": 3}, content=content)
        assert r.status_code == 409
        assert r.json()["received"] == 0


def test_complete_with_wrong_content_returns_422(tmp_path):
    app, *_ = _app(tmp_path)
    with TestClient(app) as c:
        token = _register(c)
        uid = c.post("/agent/v1/uploads", json=_req(b"real").model_dump(mode="json"),
                     headers={"X-Agent-Token": token}).json()["upload_id"]
        c.put(f"/agent/v1/uploads/{uid}", params={"offset": 0}, content=b"evil")  # 内容被换
        assert c.post(f"/agent/v1/uploads/{uid}/complete").status_code == 422


def test_same_segment_is_done_without_reupload(tmp_path):
    app, *_ = _app(tmp_path)
    content = b"same"
    req = _req(content)
    with TestClient(app) as c:
        token = _register(c)
        uid = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                     headers={"X-Agent-Token": token}).json()["upload_id"]
        c.put(f"/agent/v1/uploads/{uid}", params={"offset": 0}, content=content)
        assert c.post(f"/agent/v1/uploads/{uid}/complete").status_code == 200
        again = c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                       headers={"X-Agent-Token": token}).json()
        assert again["done"] is True


def test_backfill_slot_limit_and_release(tmp_path):
    app, *_ = _app(tmp_path, backfill_limit=2)
    with TestClient(app) as c:
        tokens = [c.post("/agent/v1/backfill-slot").json()["token"] for _ in range(2)]
        r = c.post("/agent/v1/backfill-slot")
        assert r.status_code == 429 and r.headers["Retry-After"]
        c.post(f"/agent/v1/backfill-slot/{tokens[0]}/release")
        assert c.post("/agent/v1/backfill-slot").status_code == 200


def test_chunk_over_1mb_rejected(tmp_path):
    app, *_ = _app(tmp_path)
    big = b"x" * (1024 * 1024 + 1)
    with TestClient(app) as c:
        uid = c.post("/agent/v1/uploads", json=_req(big).model_dump(mode="json"),
                     headers={"X-Agent-Token": _register(c)}).json()["upload_id"]
        assert c.put(f"/agent/v1/uploads/{uid}", params={"offset": 0}, content=big).status_code == 413


def test_rejects_path_traversal_code(tmp_path):
    app, *_ = _app(tmp_path)
    data = _req(b"x").model_dump(mode="json")
    data["code"] = "../../etc"
    with TestClient(app) as c:
        assert c.post("/agent/v1/uploads", json=data).status_code == 422


def test_rejects_bad_filename(tmp_path):
    app, *_ = _app(tmp_path)
    for bad in ("../../evil.ts", "evil.ts", "HD-A-012_09.ts", "HD-A-012_091000.mp4"):
        data = _req(b"x").model_dump(mode="json")
        data["filename"] = bad
        with TestClient(app) as c:
            assert c.post("/agent/v1/uploads", json=data).status_code == 422, bad


def test_startup_cleans_stale_incoming(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t.db",
        recordings_dir=tmp_path / "recordings",
        sweep_seconds=3600,
    )
    incoming = settings.recordings_dir / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    stale = incoming / "leftover.part"
    stale.write_bytes(b"half-done")
    create_app(settings)
    assert not stale.exists()


def test_duplicate_after_filename_collision_is_done(tmp_path):
    app, url, settings = _app(tmp_path)
    with TestClient(app) as c:
        token = _register(c)
        hdr = {"X-Agent-Token": token}
        for content in (b"original", b"different"):
            req = _req(content).model_dump(mode="json")
            uid = c.post("/agent/v1/uploads", json=req, headers=hdr).json()["upload_id"]
            assert c.put(f"/agent/v1/uploads/{uid}?offset=0", content=content).status_code == 200
            assert c.post(f"/agent/v1/uploads/{uid}/complete").status_code == 200
        again = c.post("/agent/v1/uploads", json=req, headers=hdr).json()
        assert again["done"] is True
    assert len(list(settings.recordings_dir.rglob("*.ts"))) == 2
    with Session(create_engine(url)) as s:
        rows = s.scalars(select(Recording)).all()
        assert len(rows) == 2
        assert all(r.source_filename == req["filename"] for r in rows)


def test_legacy_recording_still_deduplicates_after_migration(tmp_path):
    url = f"sqlite:///{tmp_path}/legacy.db"
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0001")
    content = b"old"
    req = _req(content)
    with create_engine(url).begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO recording (code, path, start_at, end_at, size, sha256, integrity, status, locked) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (req.code, "/recordings/" + req.filename, req.started_at.isoformat(), req.ended_at.isoformat(),
             len(content), req.sha256, "ok", "synced", False),
        )
    run_migrations(url)
    app = create_app(Settings(database_url=url, recordings_dir=tmp_path / "recordings"))
    with TestClient(app) as c:
        token = _register(c)
        assert c.post("/agent/v1/uploads", json=req.model_dump(mode="json"),
                      headers={"X-Agent-Token": token}).json()["done"]
