"""服务端接收 Agent 上传（设计文档 5.1、5.5）。

分段分片上传到 _incoming/，complete 时校验大小和 SHA-256，移入正式目录并写 recording 表。
补传名额（backfill slot）限制同一时刻并发补传的设备数。

上传会话和名额都在内存里——首版单 worker；真要水平扩展再换 Redis/DB（见 main.py 的注释）。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from everrec_core.ffmpeg import remux_to_mp4
from everrec_core.protocol import (
    BackfillSlotReply,
    UploadCompleteReply,
    UploadInitReply,
    UploadProgressReply,
    UploadRequest,
)

from . import auth
from .db import Agent, Recording
from .deps import get_db
from .retention import disk_usage_ratio

log = logging.getLogger("everrec.server.uploads")

CHUNK_MAX = 1_048_576  # 分片 ≤ 1 MB，在接口里校验，不依赖 Nginx（设计文档 5.5 措施 6）

INCOMING_DIRNAME = "_incoming"


@dataclass
class UploadSession:
    upload_id: str
    path: Path
    received: int
    meta: UploadRequest


class UploadManager:
    """上传会话（按 code+filename+sha256 幂等）与补传名额，全内存。"""

    def __init__(self, recordings_dir: Path, backfill_limit: int = 5, slot_ttl_seconds: float = 600):
        self.recordings_dir = recordings_dir
        self.incoming = recordings_dir / INCOMING_DIRNAME
        self.incoming.mkdir(parents=True, exist_ok=True)
        # 上次进程留下的半截文件（会话状态在内存里，重启即失效）直接清掉
        for stale in self.incoming.glob("*.part"):
            stale.unlink(missing_ok=True)
        self.backfill_limit = backfill_limit
        self.slot_ttl = slot_ttl_seconds
        self._sessions: dict[str, UploadSession] = {}
        self._by_key: dict[tuple[str, str, str], str] = {}
        self._slots: dict[str, float] = {}  # token -> 过期时刻（monotonic 秒）
        self._lock = threading.Lock()

    # ---------- 上传会话 ----------

    def init(self, req: UploadRequest) -> tuple[str, int]:
        """建会话或续用已有会话，返回 (upload_id, received)。"""
        key = (req.code, req.filename, req.sha256)
        with self._lock:
            if key in self._by_key:
                s = self._sessions[self._by_key[key]]
                return s.upload_id, s.received
            upload_id = uuid.uuid4().hex
            path = self.incoming / f"{upload_id}.part"
            self._sessions[upload_id] = UploadSession(upload_id, path, 0, req)
            self._by_key[key] = upload_id
            return upload_id, 0

    def get(self, upload_id: str) -> UploadSession | None:
        with self._lock:
            return self._sessions.get(upload_id)

    def put(self, upload_id: str, offset: int, data: bytes) -> int | None:
        """追加一片；offset 与已收到字节数不符返回 None（调用方回 409）。"""
        with self._lock:
            s = self._sessions.get(upload_id)
            if s is None:
                raise KeyError(upload_id)
            if offset != s.received:
                return None
            with s.path.open("ab") as f:
                f.write(data)
            s.received += len(data)
            return s.received

    def finish(self, upload_id: str) -> Path | None:
        """完成：从内存移除会话并返回 .part 路径，文件由调用方校验后移走。"""
        with self._lock:
            s = self._sessions.pop(upload_id, None)
            if s is None:
                return None
            for key, uid in list(self._by_key.items()):
                if uid == upload_id:
                    del self._by_key[key]
            return s.path

    def discard(self, upload_id: str) -> None:
        """校验失败：清掉会话和半成品文件，从头重传。"""
        with self._lock:
            s = self._sessions.pop(upload_id, None)
            if s is None:
                return
            for key, uid in list(self._by_key.items()):
                if uid == upload_id:
                    del self._by_key[key]
        s.path.unlink(missing_ok=True)

    # ---------- 补传名额 ----------

    def acquire_slot(self) -> tuple[str, datetime] | None:
        """拿到一个名额返回 (token, 过期时刻)，满了返回 None。"""
        with self._lock:
            now = time.monotonic()
            for token in [t for t, exp in self._slots.items() if exp <= now]:
                del self._slots[token]
            if len(self._slots) >= self.backfill_limit:
                return None
            token = uuid.uuid4().hex
            self._slots[token] = now + self.slot_ttl
            return token, datetime.now(UTC) + timedelta(seconds=self.slot_ttl)

    def release_slot(self, token: str) -> None:
        with self._lock:
            self._slots.pop(token, None)


router = APIRouter(prefix="/agent/v1")


def _manager(request: Request) -> UploadManager:
    return request.app.state.upload_manager


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK_MAX), b""):
            h.update(block)
    return h.hexdigest()


@router.post("/uploads", response_model=UploadInitReply)
def init_upload(
    req: UploadRequest, request: Request, s: Session = Depends(get_db)
) -> UploadInitReply | JSONResponse:
    # 校验设备令牌（tasks.md WP5）：吊销或缺失都拒绝
    agent = s.scalar(select(Agent).where(Agent.code == req.code))
    if agent is None or not auth.verify_agent_token(
        request.headers.get("X-Agent-Token", ""), agent.token_hash
    ):
        raise HTTPException(status_code=401, detail="设备凭据无效")
    # 同 code+filename+sha256 已完成直接 done，不重复传（设计文档 5.1）
    already = s.scalar(
        select(Recording.id).where(
            Recording.code == req.code,
            Recording.sha256 == req.sha256,
            or_(
                Recording.source_filename == req.filename,
                and_(
                    Recording.source_filename.is_(None),
                    or_(Recording.path.endswith("/" + req.filename, autoescape=True),
                        Recording.path.endswith("\\" + req.filename, autoescape=True)),
                ),
            ),
        )
    )
    if already is not None:
        return UploadInitReply(upload_id="", received=0, done=True)
    settings = request.app.state.settings
    # 磁盘达到危险阈值（默认 95%）时拒收，Agent 当作暂时不可用处理，录像留本地继续录（设计 5.4）
    if disk_usage_ratio(settings.recordings_dir) >= settings.disk_critical_threshold:
        return JSONResponse(status_code=507, content={"error": "服务器磁盘已满，暂时不能接收上传"})
    upload_id, received = _manager(request).init(req)
    return UploadInitReply(upload_id=upload_id, received=received, done=False)


@router.put("/uploads/{upload_id}", response_model=UploadProgressReply)
def put_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(...),
    payload: bytes = Body(...),
) -> UploadProgressReply | JSONResponse:
    if len(payload) > CHUNK_MAX:
        raise HTTPException(status_code=413, detail="分片超过 1 MB")
    manager = _manager(request)
    try:
        received = manager.put(upload_id, offset, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="没有这个上传会话") from None
    if received is None:
        session = manager.get(upload_id)
        assert session is not None
        body = UploadProgressReply(received=session.received).model_dump()
        return JSONResponse(status_code=409, content=body)
    return UploadProgressReply(received=received)


@router.post("/uploads/{upload_id}/complete", response_model=UploadCompleteReply)
def complete_upload(
    upload_id: str, request: Request, s: Session = Depends(get_db)
) -> UploadCompleteReply | JSONResponse:
    manager = _manager(request)
    session = manager.get(upload_id)
    if session is None:
        raise HTTPException(status_code=404, detail="没有这个上传会话")
    path = session.path
    meta = session.meta
    # 服务器校验大小和 SHA-256，不信任 Agent 报的数（设计文档 9.2「传输完整」）
    if path.stat().st_size != meta.size or _sha256(path) != meta.sha256:
        manager.discard(upload_id)
        return JSONResponse(status_code=422, content={"error": "大小或 SHA-256 不符，请重传"})
    started = meta.started_at
    dest = (
        manager.recordings_dir / meta.code
        / f"{started:%Y}" / f"{started:%m}" / f"{started:%d}" / meta.filename
    )
    # 兜底：协议校验之外再确认目标路径落在录像目录内，防任何越权
    if not dest.resolve().is_relative_to(manager.recordings_dir.resolve()):
        manager.discard(upload_id)
        raise HTTPException(status_code=400, detail="非法目标路径")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():  # 同一文件名（罕见）：加后缀，不覆盖
        dest = dest.with_name(f"{dest.stem}_{uuid.uuid4().hex[:6]}{dest.suffix}")
    path.replace(dest)
    manager.finish(upload_id)
    # 浏览器不能直接播 TS，转封装成 MP4（设计文档 12）。失败则保留 TS 供修复。
    final = dest
    mp4 = dest.with_suffix(".mp4")
    if remux_to_mp4(dest, mp4):
        dest.unlink(missing_ok=True)
        final = mp4
    else:
        log.warning("转封装 MP4 失败，保留 TS 待修复：%s", dest)
    rec = Recording(
        code=meta.code,
        path=str(final),
        source_filename=meta.filename,
        start_at=meta.started_at,
        end_at=meta.ended_at,
        size=meta.size,
        sha256=meta.sha256,
        integrity=meta.integrity,
        status="synced",
        locked=False,
    )
    s.add(rec)
    s.commit()
    s.refresh(rec)
    return UploadCompleteReply(recording_id=rec.id)


@router.post("/backfill-slot", response_model=BackfillSlotReply)
def acquire_backfill_slot(request: Request) -> BackfillSlotReply | JSONResponse:
    got = _manager(request).acquire_slot()
    if got is None:
        return JSONResponse(status_code=429, headers={"Retry-After": "5"},
                            content={"error": "补传名额已满"})
    token, expires_at = got
    return BackfillSlotReply(token=token, expires_at=expires_at)


@router.post("/backfill-slot/{token}/release")
def release_backfill_slot(token: str, request: Request) -> dict:
    _manager(request).release_slot(token)
    return {"ok": True}
