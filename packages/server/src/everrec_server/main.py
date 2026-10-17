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

import httpx
from fastapi import Depends, FastAPI, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from everrec_core.protocol import Heartbeat, HeartbeatReply

from . import __version__, push
from .db import Agent, Event, make_session_factory, utcnow
from .deps import get_db
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
            detail = {"problems": agent.last_payload.get("problems", [])}
            s.add(Event(type=etype, severity=severity, agent_id=agent.id, code=agent.code,
                        summary=summary, occurred_at=now, payload=detail))
            push.enqueue(s, etype, severity, summary, agent.code, detail, now)
            log.info("事件 %s %s：%s", etype, agent.code, summary)

    def sweep_once() -> None:
        """后台判定失联。失联只能由服务器发现；大批同时失联合并成一条。"""
        now = datetime.now(UTC)
        with SessionLocal() as s:
            agents = s.scalars(select(Agent)).all()
            if not agents:
                return
            lost = [a for a in agents
                    if device_status(a, now, settings.lost_after_seconds) is Status.LOST]
            newly_lost = [a for a in lost if a.status != Status.LOST]
            mass = len(agents) >= 3 and len(lost) / len(agents) >= settings.mass_lost_ratio
            if newly_lost:
                if mass:
                    for a in newly_lost:
                        a.status = Status.LOST  # 只更新状态，不逐台推送
                    summary = f"疑似服务器或网络故障（{len(lost)}/{len(agents)} 台设备同时失联）"
                    detail = {"lost": len(lost), "total": len(agents)}
                    s.add(Event(type="mass_lost", severity="critical", summary=summary,
                                occurred_at=now, payload=detail))
                    push.enqueue(s, "mass_lost", "critical", summary, None, detail, now)
                    log.info("事件 mass_lost：%s", summary)
                else:
                    for a in newly_lost:
                        record_transition(s, a, Status.LOST, now)
            s.commit()

    def sweeper() -> None:
        while not stop.wait(settings.sweep_seconds):
            try:
                sweep_once()
            except Exception:
                log.exception("失联判定出错")

    def push_worker() -> None:
        push.worker(SessionLocal, settings, stop)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        threads = [
            threading.Thread(target=sweeper, daemon=True, name="lost-sweeper"),
            threading.Thread(target=push_worker, daemon=True, name="push-worker"),
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
    # 上传会话与补传名额在内存里：首版单 worker；多 worker 需换成 Redis/DB 共享状态
    app.state.upload_manager = UploadManager(settings.recordings_dir, backfill_limit=settings.backfill_limit)
    app.state.sweep_once = sweep_once  # 测试用
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
        old_storage = (agent.last_payload or {}).get("checks", {}).get("storage", "ok")
        agent.hostname, agent.version, agent.last_heartbeat = hb.hostname, hb.agent_version, now
        agent.last_payload = hb.model_dump(mode="json", exclude={"recent_logs"})
        if hb.recent_logs:
            logs = (agent.recent_logs or []) + [x.model_dump(mode="json") for x in hb.recent_logs]
            agent.recent_logs = logs[-100:]
        record_transition(s, agent, device_status(agent, now, settings.lost_after_seconds), now)
        # storage.low：存储检查从 ok 变 warn/fail（设计文档 8.1 存储类事件）
        new_storage = hb.checks.storage
        if old_storage == "ok" and new_storage in ("warn", "fail"):
            severity = "critical" if new_storage == "fail" else "warning"
            summary = f"磁盘空间不足（已用 {int(hb.disk_used * 100)}%）"
            detail = {"disk_used": hb.disk_used, "level": str(new_storage)}
            s.add(Event(type="storage.low", severity=severity, agent_id=agent.id, code=agent.code,
                        summary=summary, occurred_at=now, payload=detail))
            push.enqueue(s, "storage.low", severity, summary, agent.code, detail, now)
            log.info("事件 storage.low %s：%s", agent.code, summary)
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

    @app.post("/api/v1/push/test")
    def push_test():
        """测试推送接口：立即发一条测试消息，返回对方状态码和内容（设计文档 8.3）。"""
        if not settings.webhook_url:
            return {"ok": False, "error": "没有配置推送地址（EVERREC_WEBHOOK_URL）"}
        now = utcnow()
        msg = push.build_message("push.test", "info", "测试推送", None, {}, now)
        try:
            r = push.post_message(settings, msg["eventId"], msg)
            return {"ok": 200 <= r.status_code < 300, "status_code": r.status_code,
                    "body": r.text[:200]}
        except httpx.HTTPError as e:
            return {"ok": False, "error": str(e)}

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
