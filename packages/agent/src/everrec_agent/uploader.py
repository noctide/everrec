"""上传后台线程（设计文档 5.1、5.5）。

分段归档后进队列，后台线程负责上传；主循环不阻塞。
- 两条通道：实时（刚归档、始终允许、默认 1 Mbps）与补传（积压、先申请名额、默认 4 Mbps）。
- 分片续传：每片 ≤ 1 MB，中断后向服务器问已收到多少，从断点继续，不重传已完成部分。
- 限速用令牌桶，按字节计；连接失败指数退避 + 随机抖动（5 秒起，上限 5 分钟）。
- 校验成功后删本地文件并把索引标 synced；失败保留文件，下轮重试。
"""

from __future__ import annotations

import hashlib
import logging
import queue
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from everrec_core.config import AgentConfig
from everrec_core.protocol import UploadCompleteReply, UploadInitReply, UploadRequest

from .index import IndexEntry, SegmentIndex

log = logging.getLogger("uploader")

CHUNK = 1024 * 1024  # 1 MB
REALTIME_BPS = 1_000_000  # 实时 1 Mbps
BACKFILL_BPS = 4_000_000  # 补传 4 Mbps
BACKOFF_MIN = 5.0
BACKOFF_MAX = 300.0
SLOT_RETRY_AFTER = 5.0  # 补传名额 429 时的默认等待（没有 Retry-After 头时用）


class _Stopped(Exception):
    pass


@dataclass(frozen=True)
class UploadJob:
    entry: IndexEntry
    channel: str  # realtime / backfill


class TokenBucket:
    """按字节计费的令牌桶：突发余量留 1 秒的量，避免瞬时打满链路。"""

    def __init__(self, bytes_per_sec: float):
        self.rate = max(1.0, bytes_per_sec)
        self.capacity = self.rate
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def take(self, nbytes: int) -> float:
        """取走 nbytes 个令牌，返回需要等待的秒数（0 表示直接放行）。"""
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            self.tokens -= nbytes
            if self.tokens >= 0:
                return 0.0
            return -self.tokens / self.rate


class Uploader:
    def __init__(
        self,
        cfg: AgentConfig,
        index: SegmentIndex,
        *,
        http: httpx.Client | None = None,
        realtime_bps: float = REALTIME_BPS,
        backfill_bps: float = BACKFILL_BPS,
        chunk: int = CHUNK,
        backoff_min: float = BACKOFF_MIN,
        backoff_max: float = BACKOFF_MAX,
    ):
        self.cfg = cfg
        self.index = index
        self.base = cfg.server.rstrip("/")
        self.chunk = chunk
        self.backoff_min = backoff_min
        self.backoff_max = backoff_max
        self.http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
        self.buckets = {
            "realtime": TokenBucket(realtime_bps / 8),
            "backfill": TokenBucket(backfill_bps / 8),
        }
        self._queue: queue.Queue[UploadJob | None] = queue.Queue()
        self._active = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="uploader")

    # ---------- 对外 ----------

    def start(self) -> None:
        # 先把上次运行没送出去的分段放进队列（补传，旧到新）
        pending = [e for e in self.index.entries() if e.status != "synced" and Path(e.path).exists()]
        for e in sorted(pending, key=lambda x: x.started_at):
            self._queue.put(UploadJob(e, "backfill"))
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        """尽量把队列送完再退出；送不完的留在本地，下次启动补传。"""
        deadline = time.monotonic() + timeout
        while self._inflight() and time.monotonic() < deadline:
            time.sleep(0.05)
        self._stop.set()
        self._queue.put(None)
        self._thread.join(timeout=3)
        self.http.close()

    def _inflight(self) -> bool:
        return self._queue.qsize() > 0 or self._active

    def enqueue(self, entry: IndexEntry, channel: str) -> None:
        self._queue.put(UploadJob(entry, channel))

    def has_backlog(self) -> bool:
        return self._queue.qsize() > 0

    # ---------- 内部 ----------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if job is None:
                break
            self._active = True
            try:
                self._retry_until_done(job)
            finally:
                self._active = False

    def _retry_until_done(self, job: UploadJob) -> None:
        backoff = self.backoff_min
        while not self._stop.is_set():
            try:
                self._upload_one(job)
                return
            except _Stopped:
                return
            except (httpx.HTTPError, OSError) as e:
                wait = backoff + random.uniform(0, backoff)
                log.warning("上传 %s 失败：%s；%.0f 秒后重试", job.entry.path, e, wait)
                self._stop.wait(wait)
                backoff = min(backoff * 2, self.backoff_max)

    def _upload_one(self, job: UploadJob) -> None:
        token = self._acquire_slot() if job.channel == "backfill" else None
        try:
            self._do_upload(job.entry, job.channel)
        finally:
            if token:
                self._release_slot(token)

    def _acquire_slot(self) -> str:
        while not self._stop.is_set():
            r = self.http.post(f"{self.base}/agent/v1/backfill-slot")
            if r.status_code == 200:
                return r.json()["token"]
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", SLOT_RETRY_AFTER))
                self._stop.wait(wait)
                continue
            r.raise_for_status()
        raise _Stopped

    def _release_slot(self, token: str) -> None:
        try:
            self.http.post(f"{self.base}/agent/v1/backfill-slot/{token}/release")
        except httpx.HTTPError:
            pass  # 释放失败无所谓，名额会自己过期

    def _do_upload(self, entry: IndexEntry, channel: str) -> None:
        path = Path(entry.path)
        sha, size = self._hash(path)
        started = datetime.fromisoformat(entry.started_at).astimezone()  # 本地墙钟 → 带时区
        ended = started + timedelta(seconds=self.cfg.recording.segment_seconds)
        req = UploadRequest(
            code=self.cfg.code,
            filename=path.name,
            started_at=started,
            ended_at=ended,
            size=size,
            sha256=sha,
            integrity=entry.integrity,
            channel=channel,
        )

        init = self._init(req)
        if init.done:
            self._finish(entry, path)
            return

        bucket = self.buckets[channel]
        offset = init.received
        with path.open("rb") as f:
            f.seek(offset)
            while offset < size:
                chunk = f.read(self.chunk)
                if not chunk:
                    break
                wait = bucket.take(len(chunk))
                if wait > 0:
                    self._stop.wait(wait)
                    if self._stop.is_set():
                        raise _Stopped
                r = self.http.put(
                    f"{self.base}/agent/v1/uploads/{init.upload_id}",
                    params={"offset": offset},
                    content=chunk,
                )
                if r.status_code == 409:
                    offset = r.json()["received"]  # 服务器告诉正确的已收字节数
                    f.seek(offset)
                    continue
                r.raise_for_status()
                offset += len(chunk)

        r = self.http.post(f"{self.base}/agent/v1/uploads/{init.upload_id}/complete")
        r.raise_for_status()  # 422（校验失败）也抛 HTTPStatusError，外层退避后从头重传
        UploadCompleteReply.model_validate(r.json())
        self._finish(entry, path)

    def _finish(self, entry: IndexEntry, path: Path) -> None:
        """校验成功后删本地文件、标 synced（设计文档 5.1）。"""
        path.unlink(missing_ok=True)
        self.index.update(entry.path, status="synced")
        log.info("上传完成并删除本地分段：%s", entry.path)

    def _init(self, req: UploadRequest) -> UploadInitReply:
        r = self.http.post(f"{self.base}/agent/v1/uploads", json=req.model_dump(mode="json"))
        r.raise_for_status()
        return UploadInitReply.model_validate(r.json())

    @staticmethod
    def _hash(path: Path) -> tuple[str, int]:
        h = hashlib.sha256()
        size = 0
        with path.open("rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
                size += len(block)
        return h.hexdigest(), size
