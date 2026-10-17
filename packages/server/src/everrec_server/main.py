"""EverRec 服务端。

内部接口（只给 Agent 用）：
  POST /agent/v1/heartbeat
  POST /agent/v1/uploads             开始/续传一个分段
  PUT  /agent/v1/uploads/{id}?offset= 分片续传
  POST /agent/v1/uploads/{id}/complete 校验并入库
  POST /agent/v1/backfill-slot        申请补传名额
对外接口（第一版只有这一个，设计文档 8.6）：
  GET  /api/v1/devices
另有：
  GET  /healthz
  GET  /api/docs                自动生成的接口文档
  /                             管理网站静态文件（设置了 EVERREC_WEB_DIST 时；正式部署由 Nginx 提供）
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from everrec_core.protocol import Heartbeat, HeartbeatReply

from . import __version__
from .db import Agent, Event, OperationLog, Recording, make_session_factory, utcnow
from .deps import get_db
from .retention import disk_usage_ratio, sweep_retention
from .settings import Settings
from .status import Status, aware, device_status, transition_event
from .uploads import UploadManager
from .uploads import router as uploads_router

log = logging.getLogger("everrec.server")


# ---------- 对外返回格式 ----------


class DeviceOut(BaseModel):
    id: int
    code: str
    hostname: str
    version: str
    status: Status
    last_heartbeat: datetime
    seconds_since_heartbeat: int
    recording: bool
    profile: str | None
    current_segment: str | None
    checks: dict
    problems: list[str]
    restarts: int
    pending_sync: int
    disk_used: float
    recent_logs: list[dict]


class DevicesOut(BaseModel):
    items: list[DeviceOut]
    summary: dict[str, int]
    mass_lost: bool  # 大批同时失联：多半是服务器或网络问题，告警合并为一条


class RecordingOut(BaseModel):
    id: int
    code: str
    start_at: datetime
    end_at: datetime
    size: int
    integrity: str
    status: str
    locked: bool


def log_operation(
    s: Session,
    action: str,
    recording_id: int | None,
    user: str | None = None,
    detail: str | None = None,
) -> None:
    """写操作日志（设计文档 15）。用户名在 WP5 登录后从会话传入，当前为空。"""
    s.add(OperationLog(action=action, recording_id=recording_id, user=user, detail=detail))


# ---------- 应用 ----------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    SessionLocal: sessionmaker = make_session_factory(settings.database_url)
    stop = threading.Event()

    def record_transition(s: Session, agent: Agent, new: Status, now: datetime) -> None:
        ev = transition_event(agent.status, new, settings.lost_after_seconds)
        agent.status = new
        if ev:
            etype, severity, summary = ev
            s.add(Event(type=etype, severity=severity, agent_id=agent.id, code=agent.code,
                        summary=summary, occurred_at=now,
                        payload={"problems": agent.last_payload.get("problems", [])}))
            log.info("事件 %s %s：%s", etype, agent.code, summary)
            # TODO 第一版：投递到推送地址（发件箱 + 重试，设计文档 8.5）

    def sweep_once() -> None:
        """后台判定失联。失联只能由服务器发现。"""
        now = datetime.now(UTC)
        with SessionLocal() as s:
            agents = s.scalars(select(Agent)).all()
            for a in agents:
                new = device_status(a, now, settings.lost_after_seconds)
                if new is Status.LOST and a.status != Status.LOST:
                    record_transition(s, a, new, now)
            s.commit()

    def sweeper() -> None:
        while not stop.wait(settings.sweep_seconds):
            try:
                sweep_once()
            except Exception:
                log.exception("失联判定出错")

    disk_state = "ok"  # ok / warn / critical：只在跨越阈值时产生一次 storage.low 事件

    def retention_once() -> None:
        """每小时一次：滚动覆盖删除过期分段，并检查服务器磁盘（设计文档 5.4）。"""
        nonlocal disk_state
        now = datetime.now(UTC)
        with SessionLocal() as s:
            deleted = sweep_retention(
                s, settings.recordings_dir, settings.retention_days,
                settings.rollover_enabled, now,
            )
            ratio = disk_usage_ratio(settings.recordings_dir)
            new_state = (
                "critical" if ratio >= settings.disk_critical_threshold
                else "warn" if ratio >= settings.disk_warn_threshold
                else "ok"
            )
            if new_state != disk_state:
                if new_state in ("warn", "critical"):
                    severity = "critical" if new_state == "critical" else "warning"
                    summary = f"服务器磁盘空间不足（已用 {ratio:.0%}）"
                    s.add(Event(type="storage.low", severity=severity, summary=summary,
                                occurred_at=now, payload={"disk_used": ratio, "level": new_state}))
                    log.info("事件 storage.low：%s", summary)
                disk_state = new_state
            s.commit()
            if deleted:
                log.info("滚动覆盖：本次删除 %d 个过期分段", deleted)

    def retention_worker() -> None:
        while not stop.wait(settings.retention_seconds):
            try:
                retention_once()
            except Exception:
                log.exception("滚动覆盖/磁盘检查出错")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        threads = [
            threading.Thread(target=sweeper, daemon=True, name="lost-sweeper"),
            threading.Thread(target=retention_worker, daemon=True, name="retention"),
        ]
        for t in threads:
            t.start()
        yield
        stop.set()

    app = FastAPI(
        title="EverRec 服务端", version=__version__, lifespan=lifespan,
        docs_url="/api/docs", openapi_url="/api/openapi.json",
    )
    app.state.SessionLocal = SessionLocal
    app.state.settings = settings
    # 上传会话与补传名额在内存里：首版单 worker；多 worker 需换成 Redis/DB 共享状态
    app.state.upload_manager = UploadManager(settings.recordings_dir, backfill_limit=settings.backfill_limit)
    app.state.sweep_once = sweep_once  # 测试用
    app.state.retention_once = retention_once  # 测试用
    app.include_router(uploads_router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    @app.post("/agent/v1/heartbeat", response_model=HeartbeatReply)
    def heartbeat(hb: Heartbeat, s: Session = Depends(get_db)):
        # TODO 第一版：Agent 注册后的独立凭据校验（设计文档第 15 节）
        now = utcnow()
        agent = s.scalar(select(Agent).where(Agent.code == hb.code))
        if agent is None:
            agent = Agent(code=hb.code, hostname=hb.hostname, version=hb.agent_version,
                          status=Status.OK, last_payload={}, recent_logs=[])
            s.add(agent)
            s.flush()
            log.info("新设备注册：%s（%s）", hb.code, hb.hostname)
        agent.hostname, agent.version, agent.last_heartbeat = hb.hostname, hb.agent_version, now
        agent.last_payload = hb.model_dump(mode="json", exclude={"recent_logs"})
        if hb.recent_logs:
            logs = (agent.recent_logs or []) + [x.model_dump(mode="json") for x in hb.recent_logs]
            agent.recent_logs = logs[-100:]
        record_transition(s, agent, device_status(agent, now, settings.lost_after_seconds), now)
        s.commit()
        return HeartbeatReply()

    @app.get("/api/v1/devices", response_model=DevicesOut)
    def list_devices(
        status: Status | None = None,
        prefix: str | None = Query(default=None, description="位置编号前缀，例如 HD-PD-A"),
        s: Session = Depends(get_db),
    ):
        now = datetime.now(UTC)
        q = select(Agent).order_by(Agent.code)
        if prefix:
            q = q.where(Agent.code.startswith(prefix))
        out: list[DeviceOut] = []
        summary = {st.value: 0 for st in Status}
        for a in s.scalars(q):
            st = device_status(a, now, settings.lost_after_seconds)
            summary[st] += 1
            if status and st != status:
                continue
            p = a.last_payload or {}
            last = aware(a.last_heartbeat)
            out.append(DeviceOut(
                id=a.id, code=a.code, hostname=a.hostname, version=a.version, status=st,
                last_heartbeat=last, seconds_since_heartbeat=int((now - last).total_seconds()),
                recording=bool(p.get("recording")), profile=p.get("profile"),
                current_segment=p.get("current_segment"), checks=p.get("checks", {}),
                problems=p.get("problems", []), restarts=p.get("restarts", 0),
                pending_sync=p.get("pending_sync", 0), disk_used=p.get("disk_used", 0),
                recent_logs=(a.recent_logs or [])[-20:],
            ))
        total = sum(summary.values())
        mass_lost = total >= 3 and summary[Status.LOST] / total >= settings.mass_lost_ratio
        return DevicesOut(items=out, summary=summary, mass_lost=mass_lost)

    @app.post("/api/v1/recordings/{recording_id}/lock")
    def lock_recording(recording_id: int, s: Session = Depends(get_db)):
        """加锁：被加锁的录像不参与滚动覆盖（事故留证，设计 5.4）。登录校验在 WP5 接入。"""
        rec = s.get(Recording, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="没有这段录像")
        rec.locked = True
        log_operation(s, "lock", recording_id)
        s.commit()
        return {"id": rec.id, "locked": True}

    @app.post("/api/v1/recordings/{recording_id}/unlock")
    def unlock_recording(recording_id: int, s: Session = Depends(get_db)):
        rec = s.get(Recording, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="没有这段录像")
        rec.locked = False
        log_operation(s, "unlock", recording_id)
        s.commit()
        return {"id": rec.id, "locked": False}

    @app.get("/api/v1/recordings", response_model=list[RecordingOut])
    def list_recordings(
        code: str = Query(..., description="位置编号，精确匹配"),
        from_: datetime = Query(..., alias="from"),
        to: datetime = Query(...),
        s: Session = Depends(get_db),
    ):
        """按位置编号和时间段查分段（设计文档 10 回放页）。"""
        q = (
            select(Recording)
            .where(Recording.code == code, Recording.start_at >= from_, Recording.start_at < to)
            .order_by(Recording.start_at)
        )
        return [
            RecordingOut(
                id=r.id, code=r.code, start_at=aware(r.start_at), end_at=aware(r.end_at),
                size=r.size, integrity=r.integrity, status=r.status, locked=r.locked,
            )
            for r in s.scalars(q)
        ]

    @app.get("/api/v1/recordings/{recording_id}/file")
    def recording_file(recording_id: int, s: Session = Depends(get_db)):
        rec = s.get(Recording, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="没有这段录像")
        return _serve_file(s, rec, "view")

    @app.get("/api/v1/recordings/{recording_id}/download")
    def recording_download(recording_id: int, s: Session = Depends(get_db)):
        rec = s.get(Recording, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="没有这段录像")
        return _serve_file(s, rec, "download", as_attachment=True)

    def _serve_file(s: Session, rec: Recording, action: str, as_attachment: bool = False) -> FileResponse:
        path = Path(rec.path)
        # 防越权：只服务录像目录内的文件（上传接口已保证，这里再兜底一次）
        if not path.resolve().is_relative_to(settings.recordings_dir.resolve()) or not path.exists():
            raise HTTPException(status_code=404, detail="录像文件不存在")
        log_operation(s, action, rec.id)
        s.commit()
        filename = rec.source_filename or path.name
        return FileResponse(path, media_type="video/mp4", filename=filename if as_attachment else None)

    if settings.web_dist and settings.web_dist.exists():
        app.mount("/", StaticFiles(directory=settings.web_dist, html=True), name="web")

    return app


def run() -> None:
    import argparse

    import uvicorn

    p = argparse.ArgumentParser(prog="everrec-server")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=args.host, port=args.port)
