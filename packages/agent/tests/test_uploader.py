"""上传器：上传成功后删本地文件、失败保留、限速生效。"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from everrec_agent.index import IndexEntry, SegmentIndex
from everrec_agent.uploader import Uploader
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording
from everrec_core.ffmpeg import ffmpeg_binary
from everrec_core.protocol import Checks, Heartbeat
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


def _register(base_url: str, code: str = "HD-A-012") -> str:
    """首次心跳注册设备，返回签发的设备令牌（tasks.md WP5）。"""
    r = httpx.post(f"{base_url}/agent/v1/heartbeat", json=Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now().astimezone(),
        recording=True, profile="720P / 10 帧", checks=Checks(),
    ).model_dump(mode="json"))
    r.raise_for_status()
    return r.json()["token"]


def test_uploads_then_deletes_local_file(tmp_path: Path, cfg):
    app, settings = _app(tmp_path)
    server = _Server(app)
    cfg.server = server.base_url
    try:
        index = SegmentIndex(cfg.state_dir)
        up = Uploader(cfg, index, realtime_bps=10**9, backfill_bps=10**9)
        up.set_token(_register(server.base_url))

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
        up.set_token(_register(server.base_url))

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


def test_missing_file_is_skipped(tmp_path: Path, cfg):
    uploaded: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/agent/v1/uploads":
            uploaded.append(json.loads(request.content)["filename"])
            return httpx.Response(200, json={"upload_id": "u", "received": 0, "done": False})
        if request.method == "PUT" and path == "/agent/v1/uploads/u":
            off = int(request.url.params["offset"])
            return httpx.Response(200, json={"received": off + len(request.content)})
        if request.method == "POST" and path == "/agent/v1/uploads/u/complete":
            return httpx.Response(200, json={"recording_id": 1})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    index = SegmentIndex(cfg.state_dir)
    up = Uploader(cfg, index, http=client, realtime_bps=10**9, backoff_min=0.05)

    missing = cfg.recordings_dir / "HD-A-012_091000.ts"  # 文件不存在
    good = cfg.recordings_dir / "HD-A-012_091005.ts"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"ok")

    missing_e = IndexEntry(str(missing), "2026-10-15T09:00:00", 10, "ok")
    good_e = IndexEntry(str(good), "2026-10-15T09:00:05", 2, "ok")
    index.add(missing_e)
    index.add(good_e)
    up.enqueue(missing_e, "realtime")
    up.enqueue(good_e, "realtime")
    up.start()

    _wait_until(lambda: not good.exists(), timeout=5)
    up.stop()
    assert uploaded == ["HD-A-012_091005.ts"], "不存在的文件不该被上传，也不能挡住后面的"


def test_permanent_rejection_marks_failed(tmp_path: Path, cfg):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        path = request.url.path
        if request.method == "POST" and path == "/agent/v1/uploads":
            attempts += 1
            return httpx.Response(400, json={"error": "bad"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    index = SegmentIndex(cfg.state_dir)
    up = Uploader(cfg, index, http=client, realtime_bps=10**9, backoff_min=0.05)

    seg = cfg.recordings_dir / "HD-A-012_091000.ts"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"x")
    entry = IndexEntry(str(seg), "2026-10-15T09:00:00", 1, "ok")
    index.add(entry)
    up.enqueue(entry, "realtime")
    up.start()

    _wait_until(lambda: index.entries()[0].status == "failed", timeout=5)
    up.stop()
    assert attempts == 3
    assert seg.exists(), "标 failed 的文件不删，留给下次启动重试"


def test_realtime_does_not_wait_for_backfill_slot(tmp_path: Path, cfg, monkeypatch):
    index = SegmentIndex(cfg.state_dir)
    waiting = threading.Event()
    uploaded = threading.Event()

    def handler(request):
        if request.url.path == "/agent/v1/backfill-slot":
            waiting.set()
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(404)

    up = Uploader(cfg, index, http=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(up, "_do_upload", lambda *args: uploaded.set())
    e_back = IndexEntry("HD-A-012_091000.ts", "2026-10-15T09:00:00", 1, "ok")
    e_rt = IndexEntry("HD-A-012_091005.ts", "2026-10-15T09:00:05", 1, "ok")
    up.enqueue(e_back, "backfill")
    up.start()
    try:
        assert waiting.wait(2)
        up.enqueue(e_rt, "realtime")
        assert uploaded.wait(2), "补传名额满不能阻塞实时上传"
    finally:
        up.stop(timeout=0)


def test_ended_at_uses_actual_duration(tmp_path: Path, cfg):
    seg = cfg.recordings_dir / "HD-A-012_091000.ts"
    seg.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_binary(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=5",
         "-t", "2", "-c:v", "libx264", "-f", "mpegts", str(seg)],
        check=True,
    )

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/agent/v1/uploads":
            body = json.loads(request.content)
            captured.update(started_at=body["started_at"], ended_at=body["ended_at"])
            return httpx.Response(200, json={"upload_id": "u", "received": 0, "done": False})
        if request.method == "PUT" and path == "/agent/v1/uploads/u":
            off = int(request.url.params["offset"])
            return httpx.Response(200, json={"received": off + len(request.content)})
        if request.method == "POST" and path == "/agent/v1/uploads/u/complete":
            return httpx.Response(200, json={"recording_id": 1})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    index = SegmentIndex(cfg.state_dir)
    up = Uploader(cfg, index, http=client, realtime_bps=10**9, backoff_min=0.05)

    entry = IndexEntry(str(seg), "2026-10-15T09:00:00", seg.stat().st_size, "ok")
    index.add(entry)
    up.enqueue(entry, "realtime")
    up.start()

    _wait_until(lambda: not seg.exists(), timeout=5)
    up.stop()

    started = datetime.fromisoformat(captured["started_at"])
    ended = datetime.fromisoformat(captured["ended_at"])
    dur = (ended - started).total_seconds()
    assert 1.5 <= dur <= 2.5, f"ended_at 没用实际时长（{dur:.1f} 秒，应为约 2 秒）"


def test_index_compacts(tmp_path: Path):
    idx = SegmentIndex(tmp_path / "state", compact_every=5)
    idx.add(IndexEntry("a.ts", "2026-10-15T09:00:00", 10, "ok"))
    idx.update("a.ts", status="synced")
    idx.add(IndexEntry("b.ts", "2026-10-15T09:00:00", 10, "ok"))
    idx.update("b.ts", status="synced")
    idx.add(IndexEntry("c.ts", "2026-10-15T09:00:00", 10, "ok"))  # 第 5 行触发压缩
    lines = [ln for ln in idx.file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 3, "压缩后每个路径只留最新一行"
    statuses = {e.path: e.status for e in idx.entries()}
    assert statuses == {"a.ts": "synced", "b.ts": "synced", "c.ts": "local"}


def test_torn_index_keeps_good_entries_and_accepts_new_records(tmp_path):
    index = SegmentIndex(tmp_path, compact_every=3)
    index.add(IndexEntry("a.ts", "2026-10-15T09:00:00", 10, "ok"))
    with index.file.open("ab") as f:
        f.write(b'{"path":"broken-\xe4')
    assert [e.path for e in index.entries()] == ["a.ts"]
    index.add(IndexEntry("b.ts", "2026-10-15T09:05:00", 20, "ok"))
    index.update("a.ts", status="synced")
    assert {e.path: e.status for e in index.entries()} == {"a.ts": "synced", "b.ts": "local"}
    assert len(index.file.read_text(encoding="utf-8").splitlines()) == 2


def test_unreadable_index_does_not_prevent_upload_workers_starting(tmp_path, cfg, monkeypatch):
    index = SegmentIndex(cfg.state_dir)

    def unreadable():
        raise OSError("索引暂时不可读")

    monkeypatch.setattr(index, "entries", unreadable)
    up = Uploader(cfg, index)
    up.start()
    try:
        assert all(t.is_alive() for t in up._threads)
    finally:
        up.stop(timeout=0)


def test_realtime_continues_while_backfill_retries(cfg, monkeypatch):
    index = SegmentIndex(cfg.state_dir)
    up = Uploader(cfg, index, backoff_min=60)
    failed = threading.Event()
    uploaded = threading.Event()

    def upload(job):
        if job.channel == "backfill":
            failed.set()
            raise httpx.ConnectError("补传连接失败")
        uploaded.set()

    monkeypatch.setattr(up, "_upload_one", upload)
    up.enqueue(IndexEntry("old.ts", "2026-10-15T09:00:00", 1, "ok"), "backfill")
    up.start()
    try:
        assert failed.wait(2)
        up.enqueue(IndexEntry("new.ts", "2026-10-15T09:05:00", 1, "ok"), "realtime")
        assert uploaded.wait(2)
    finally:
        up.stop(timeout=0)


def test_server_switch_keeps_file_until_new_server_confirms(cfg, monkeypatch):
    index = SegmentIndex(cfg.state_dir)
    seg = cfg.recordings_dir / "HD-A-012_091000.ts"
    seg.parent.mkdir(parents=True)
    seg.write_bytes(b"data")
    entry = IndexEntry(str(seg), "2026-10-15T09:00:00", 4, "ok")
    index.add(entry)
    hosts = []
    switched = threading.Event()

    def handler(request):
        if request.url.path == "/agent/v1/uploads":
            hosts.append(request.url.host)
            return httpx.Response(200, json={"upload_id": "u", "received": 0})
        if request.url.path.endswith("/complete"):
            if request.url.host == "test":
                up.set_server("")
                switched.set()
            return httpx.Response(200, json={"recording_id": 1})
        return httpx.Response(200, json={"received": 4})

    monkeypatch.setattr("everrec_agent.uploader.probe_duration", lambda _p: 1.0)
    up = Uploader(cfg, index, http=httpx.Client(transport=httpx.MockTransport(handler)),
                  realtime_bps=10**9)
    up.enqueue(entry, "realtime")
    up.start()
    try:
        assert switched.wait(2)
        assert seg.exists() and index.pending_count() == 1
        up.set_server("http://new-server")
        _wait_until(lambda: not seg.exists() and index.pending_count() == 0)
        assert hosts == ["test", "new-server"]
    finally:
        up.stop(timeout=0)
