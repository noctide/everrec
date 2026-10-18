"""上传后台线程（设计文档 5.1、5.5）。

分段归档后进队列，后台线程负责上传；主循环不阻塞。
- 实时和补传各有后台线程，补传等待名额或退避不阻塞新分段。
- 补传先向服务器申请名额、默认 4 Mbps；实时始终允许、默认 1 Mbps。
- 分片续传：每片 ≤ 1 MB，中断后向服务器问已收到多少，从断点继续，不重传已完成部分。
- 限速用令牌桶，按字节计；连接失败指数退避 + 随机抖动（5 秒起，上限 5 分钟）。
- 校验成功后删本地文件、索引标 synced；文件没了直接跳过，永久性 4xx 标 failed 后继续下一个。
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

from everrec_core.config import AgentConfig, CameraType
from everrec_core.protocol import UploadCompleteReply, UploadInitReply, UploadRequest
from everrec_core.repair import probe_duration

from .index import IndexEntry, SegmentIndex

log = logging.getLogger("uploader")

CHUNK = 1024 * 1024  # 1 MB
REALTIME_BPS = 1_000_000  # 实时 1 Mbps
BACKFILL_BPS = 4_000_000  # 补传 4 Mbps
BACKOFF_MIN = 5.0
BACKOFF_MAX = 300.0
SLOT_RETRY_AFTER = 5.0  # 补传名额 429 时的默认等待（没有 Retry-After 头时用）
PERMANENT_RETRIES = 3  # 同一任务被永久性 4xx 拒绝这么多次就标 failed，不再挡后面的
# 会话丢了(404)/offset 不符(409)/名额满(429) 都是暂时性，不算永久失败
RETRYABLE_STATUS = {404, 409, 429}


class _Stopped(Exception):
    pass


class _TargetChanged(Exception):
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

    def set_rate(self, bytes_per_sec: float) -> None:
        """服务器下发限速时热更新（不会溢出已有令牌）。"""
        with self._lock:
            self.rate = max(1.0, bytes_per_sec)
            self.capacity = self.rate
            self.tokens = min(self.tokens, self.capacity)


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
        self._realtime: queue.Queue[UploadJob] = queue.Queue()
        self._backfill: queue.Queue[UploadJob] = queue.Queue()
        self._target_lock = threading.Lock()
        self._generation = 0
        self._queue_lock = threading.Lock()
        self._queued: set[str] = set()
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._run, args=(channel,), daemon=True, name=f"uploader-{channel}")
            for channel in self.buckets
        ]
        self._workers_left = len(self._threads)

    # ---------- 对外 ----------

    def start(self) -> None:
        # 先把上次运行没送出去的分段放进补传队列（旧到新）
        try:
            pending = [e for e in self.index.entries() if e.status != "synced" and Path(e.path).exists()]
        except OSError:
            log.exception("读取待传索引失败，继续本地录像")
            pending = []
        for e in sorted(pending, key=lambda x: x.started_at):
            self.enqueue(e, "backfill")
        for thread in self._threads:
            thread.start()

    def set_server(self, server: str) -> None:
        """切换上传目标；正在传的旧目标任务不能确认或删除本地文件。"""
        with self._target_lock:
            self.base = server.rstrip("/")
            self._generation += 1

    def set_rates(self, realtime_bps: float, backfill_bps: float) -> None:
        """服务器下发限速时热更新两个通道的令牌桶。"""
        self.buckets["realtime"].set_rate(realtime_bps / 8)
        self.buckets["backfill"].set_rate(backfill_bps / 8)

    def set_token(self, token: str) -> None:
        """设置设备令牌，所有上传请求带上 X-Agent-Token（tasks.md WP5）。"""
        self.http.headers["X-Agent-Token"] = token

    def stop(self, timeout: float = 15.0) -> None:
        """尽量把队列送完再退出；送不完的留在本地，下次启动补传。"""
        deadline = time.monotonic() + timeout
        while self._inflight() and time.monotonic() < deadline:
            time.sleep(0.05)
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.5)

    def _inflight(self) -> bool:
        with self._queue_lock:
            return bool(self._queued)

    def enqueue(self, entry: IndexEntry, channel: str) -> None:
        with self._queue_lock:
            if entry.path in self._queued:
                return
            self._queued.add(entry.path)
        q = self._realtime if channel == "realtime" else self._backfill
        q.put(UploadJob(entry, channel))

    # ---------- 内部 ----------

    def _run(self, channel: str) -> None:
        q = self._realtime if channel == "realtime" else self._backfill
        try:
            while not self._stop.is_set():
                try:
                    job = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    self._retry_until_done(job)
                finally:
                    with self._queue_lock:
                        self._queued.discard(job.entry.path)
        finally:
            with self._queue_lock:
                self._workers_left -= 1
                if not self._workers_left:
                    self.http.close()

    def _retry_until_done(self, job: UploadJob) -> None:
        backoff = self.backoff_min
        permanent = 0
        while not self._stop.is_set():
            generation = self._generation
            if not self.base:
                self._stop.wait(0.2)
                continue
            try:
                self._upload_one(job)
                return
            except _TargetChanged:
                backoff, permanent = self.backoff_min, 0
                continue
            except _Stopped:
                return
            except FileNotFoundError:
                # 文件已经不在（被隔离/手删）：直接跳过，别挡后面的
                log.warning("待传文件不存在，跳过：%s", job.entry.path)
                return
            except httpx.HTTPStatusError as e:
                if self._permanent(e):
                    permanent += 1
                    if permanent >= PERMANENT_RETRIES:
                        self.index.update(job.entry.path, status="failed")
                        log.error("服务器连续拒绝 %s（%s），标记失败，跳过", job.entry.path, e)
                        return
                err: Exception = e
            except (httpx.HTTPError, OSError) as e:
                err = e
            wait = backoff + random.uniform(0, backoff)
            log.warning("上传 %s 失败：%s；%.0f 秒后重试", job.entry.path, err, wait)
            self._wait_retry(wait, generation)
            backoff = min(backoff * 2, self.backoff_max)

    @staticmethod
    def _permanent(e: httpx.HTTPStatusError) -> bool:
        code = e.response.status_code
        return 400 <= code < 500 and code not in RETRYABLE_STATUS

    def _upload_one(self, job: UploadJob) -> None:
        with self._target_lock:
            base, generation = self.base, self._generation
        if not base:
            raise _TargetChanged
        token = self._acquire_slot(base, generation) if job.channel == "backfill" else None
        try:
            self._do_upload(job.entry, job.channel, base, generation)
        except (httpx.HTTPError, OSError):
            self._check_target(generation)
            raise
        finally:
            if token:
                self._release_slot(token, base)

    def _acquire_slot(self, base: str, generation: int) -> str:
        while not self._stop.is_set():
            self._check_target(generation)
            r = self.http.post(f"{base}/agent/v1/backfill-slot")
            if r.status_code == 200:
                return r.json()["token"]
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", SLOT_RETRY_AFTER))
                self._wait_retry(wait, generation)
                continue
            r.raise_for_status()
        raise _Stopped

    def _release_slot(self, token: str, base: str) -> None:
        try:
            self.http.post(f"{base}/agent/v1/backfill-slot/{token}/release", timeout=5)
        except httpx.HTTPError:
            pass  # 释放失败无所谓，名额会自己过期

    def _wait_retry(self, seconds: float, generation: int) -> None:
        deadline = time.monotonic() + seconds
        while generation == self._generation and not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._stop.wait(min(0.2, remaining))

    def _check_target(self, generation: int) -> None:
        if self._stop.is_set():
            raise _Stopped
        if generation != self._generation:
            raise _TargetChanged

    def _do_upload(self, entry: IndexEntry, channel: str, base: str, generation: int) -> None:
        path = Path(entry.path)
        sha, size = self._hash(path)
        started = datetime.fromisoformat(entry.started_at).astimezone()  # 本地墙钟 → 带时区
        dur = probe_duration(path)
        ended = started + timedelta(seconds=dur if dur else self.cfg.recording.segment_seconds)
        req = UploadRequest(
            code=self.cfg.code,
            filename=path.name,
            started_at=started,
            ended_at=ended,
            size=size,
            sha256=sha,
            integrity=entry.integrity,
            channel=channel,
            source="screen" if self.cfg.camera.type == CameraType.SCREEN else "camera",
        )

        self._check_target(generation)
        init = self._init(req, base)
        if init.done:
            self._finish(entry, path, generation)
            return

        bucket = self.buckets[channel]
        offset = init.received
        with path.open("rb") as f:
            f.seek(offset)
            while offset < size:
                self._check_target(generation)
                chunk = f.read(self.chunk)
                if not chunk:
                    break
                wait = bucket.take(len(chunk))
                if wait > 0:
                    self._stop.wait(wait)
                    if self._stop.is_set():
                        raise _Stopped
                r = self.http.put(
                    f"{base}/agent/v1/uploads/{init.upload_id}",
                    params={"offset": offset},
                    content=chunk,
                )
                if r.status_code == 409:
                    offset = r.json()["received"]  # 服务器告诉正确的已收字节数
                    f.seek(offset)
                    continue
                r.raise_for_status()
                offset += len(chunk)

        self._check_target(generation)
        r = self.http.post(f"{base}/agent/v1/uploads/{init.upload_id}/complete")
        r.raise_for_status()  # 422（校验失败）也抛 HTTPStatusError，外层退避后从头重传
        UploadCompleteReply.model_validate(r.json())
        self._finish(entry, path, generation)

    def _finish(self, entry: IndexEntry, path: Path, generation: int) -> None:
        """校验成功后删本地文件、标 synced（设计文档 5.1）。"""
        with self._target_lock:
            self._check_target(generation)
            path.unlink(missing_ok=True)
            self.index.update(entry.path, status="synced")
        log.info("上传完成并删除本地分段：%s", entry.path)

    def _init(self, req: UploadRequest, base: str) -> UploadInitReply:
        r = self.http.post(f"{base}/agent/v1/uploads", json=req.model_dump(mode="json"))
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
