"""上传器：上传成功后删本地文件、失败保留、限速生效。"""

from __future__ import annotations

import hashlib
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from everrec_agent.index import IndexEntry, SegmentIndex
from everrec_agent.uploader import Uploader
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording
from everrec_server.db import Recording as ServerRecording
from everrec_server.main import create_app
from everrec_server.settings import Settings


@pytest.fixture
def cfg(tmp_path: Path) -> AgentConfig:
    c = AgentConfig(
        server="http://test",
        location=Location(state="HD", building="A", number="012"),
        camera=Camera(type=CameraType.TEST),
        recording=Recording(segment_seconds=5),
    )
    c.install_dir = tmp_path / "agent"
    return c


class _Server:
    """在后台线程起一个真 uvicorn，供上传器用同步 httpx 连。"""

    def __init__(self, app):
        self.port = self._free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.01)

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def _app(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/db.sqlite",
        recordings_dir=tmp_path / "server" / "recordings",
        sweep_seconds=3600,
    )
    return create_app(settings), settings


def _wait_until(cond, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("条件在超时内没有满足")


def test_uploads_then_deletes_local_file(tmp_path: Path, cfg):
    app, settings = _app(tmp_path)
    server = _Server(app)
    cfg.server = server.base_url
    try:
        index = SegmentIndex(cfg.state_dir)
        up = Uploader(cfg, index, realtime_bps=10**9, backfill_bps=10**9)

        seg = cfg.recordings_dir / "HD-A-012" / "2026" / "10" / "15" / "HD-A-012_091000.ts"
        seg.parent.mkdir(parents=True, exist_ok=True)
        content = b"z" * 5000
        seg.write_bytes(content)
        entry = IndexEntry(str(seg), "2026-10-15T09:00:00", len(content), "ok")
        index.add(entry)
        up.enqueue(entry, "realtime")
        up.start()

        _wait_until(lambda: not seg.exists() and index.pending_count() == 0)
        up.stop()

        with Session(create_engine(f"sqlite:///{tmp_path}/db.sqlite")) as s:
            recs = s.scalars(select(ServerRecording)).all()
            assert len(recs) == 1
            assert recs[0].sha256 == hashlib.sha256(content).hexdigest()
            assert recs[0].status == "synced"

        dest = settings.recordings_dir / "HD-A-012" / "2026" / "10" / "15" / "HD-A-012_091000.ts"
        assert dest.exists() and dest.read_bytes() == content
    finally:
        server.stop()


def test_failure_keeps_local_file(tmp_path: Path, cfg):
    def unreachable(_request):
        raise httpx.ConnectError("连不上")

    client = httpx.Client(transport=httpx.MockTransport(unreachable))
    index = SegmentIndex(cfg.state_dir)
    up = Uploader(cfg, index, http=client, backoff_min=0.1)

    seg = cfg.recordings_dir / "HD-A-012_091000.ts"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"data")
    entry = IndexEntry(str(seg), "2026-10-15T09:00:00", 4, "ok")
    index.add(entry)
    up.enqueue(entry, "realtime")
    up.start()

    time.sleep(0.5)
    assert seg.exists(), "上传失败不能删本地文件"
    assert index.pending_count() == 1
    up.stop(timeout=0.5)


def test_rate_limit_throttles(tmp_path: Path, cfg):
    app, _settings = _app(tmp_path)
    server = _Server(app)
    cfg.server = server.base_url
    try:
        index = SegmentIndex(cfg.state_dir)
        # 8 KB/s 的限速，两片 8 KB 共 16 KB：第二片要等约 1 秒
        up = Uploader(cfg, index, realtime_bps=65536, chunk=8192)

        seg = cfg.recordings_dir / "HD-A-012_091000.ts"
        seg.parent.mkdir(parents=True, exist_ok=True)
        seg.write_bytes(b"y" * 16384)
        entry = IndexEntry(str(seg), "2026-10-15T09:00:00", 16384, "ok")
        index.add(entry)
        up.enqueue(entry, "realtime")

        start = time.monotonic()
        up.start()
        _wait_until(lambda: not seg.exists(), timeout=10)
        elapsed = time.monotonic() - start
        up.stop()
        assert elapsed >= 0.5, f"限速没生效，只用了 {elapsed:.2f} 秒"
    finally:
        server.stop()


def test_resume_does_not_reupload_finished_chunks(tmp_path: Path, cfg):
    state = {"received": 0, "dropped": False}
    offsets: list[int] = []
    chunk = 4

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/agent/v1/uploads":
            return httpx.Response(200, json={"upload_id": "u1", "received": state["received"], "done": False})
        if request.method == "POST" and path == "/agent/v1/uploads/u1/complete":
            return httpx.Response(200, json={"recording_id": 1})
        if request.method == "PUT" and path == "/agent/v1/uploads/u1":
            offset = int(request.url.params["offset"])
            offsets.append(offset)
            if offset != state["received"]:
                return httpx.Response(409, json={"received": state["received"]})
            if not state["dropped"] and state["received"] >= chunk:
                state["dropped"] = True
                raise httpx.ConnectError("模拟中途断线")
            state["received"] += len(request.content)
            return httpx.Response(200, json={"received": state["received"]})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    index = SegmentIndex(cfg.state_dir)
    up = Uploader(cfg, index, http=client, realtime_bps=10**9, chunk=chunk, backoff_min=0.05)

    seg = cfg.recordings_dir / "HD-A-012_091000.ts"
    seg.parent.mkdir(parents=True, exist_ok=True)
    content = b"abcdefghijkl"  # 12 字节 = 3 片
    seg.write_bytes(content)
    entry = IndexEntry(str(seg), "2026-10-15T09:00:00", len(content), "ok")
    index.add(entry)
    up.enqueue(entry, "realtime")
    up.start()

    _wait_until(lambda: not seg.exists(), timeout=5)
    up.stop()

    assert offsets.count(0) == 1, "已完成的第一片被重复上传了"
    assert state["received"] == len(content)
    assert index.pending_count() == 0
