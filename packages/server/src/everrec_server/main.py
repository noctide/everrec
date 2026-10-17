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
import re
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from everrec_core.protocol import Heartbeat, HeartbeatReply

from . import __version__, auth, config_push
from .db import (
    Agent,
    Event,
    LocationOption,
    Meta,
    OperationLog,
    Recording,
    Relay,
    Setting,
    make_session_factory,
    utcnow,
)
from .deps import get_db
from .retention import disk_info, disk_usage_ratio, sweep_retention
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
    camera_resolutions: list[str]


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
    session_secret = settings.secret_key or secrets.token_hex(32)  # 没配就用每次启动随机的密钥

    def _ensure_admin_hash() -> None:
        """把管理员密码哈希存进 meta 表（只存哈希）。"""
        if not settings.admin_password:
            return
        with SessionLocal() as s:
            h = auth.hash_password(settings.admin_password)
            row = s.get(Meta, "admin_password_hash")
            if row is not None:
                row.value = h
            else:
                s.add(Meta(key="admin_password_hash", value=h))
            s.commit()

    def _verify_admin(s: Session, password: str) -> bool:
        row = s.get(Meta, "admin_password_hash")
        return row is not None and auth.verify_password(password, row.value)

    def _valid_session(request: Request) -> bool:
        token = request.cookies.get(auth.SESSION_COOKIE)
        return bool(token) and auth.verify_session_token(token, session_secret)

    def _valid_bearer(request: Request) -> bool:
        if not settings.api_token:
            return False
        return request.headers.get("Authorization", "") == f"Bearer {settings.api_token}"

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
        """后台判定失联。失联只能由服务器发现。中转节点上的设备由 relay 判定并镜像事件，这里跳过。"""
        now = datetime.now(UTC)
        with SessionLocal() as s:
            agents = s.scalars(select(Agent).where(Agent.via == "local")).all()
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

    # ---------- 中转节点拉取（tasks.md WP10） ----------

    def _ensure_relay_rows() -> None:
        """启动时把配置里的中转节点同步进 relay 表（游标状态持久化）。"""
        if not settings.relays:
            return
        with SessionLocal() as s:
            for name, url in settings.relays.items():
                row = s.scalar(select(Relay).where(Relay.name == name))
                if row is None:
                    s.add(Relay(name=name, url=url))
                else:
                    row.url = url
            s.commit()

    def _relay_push_config() -> tuple[dict | None, dict | None]:
        """要下发给各中转节点的配置：主服务器全局设置 + 位置选项。"""
        with SessionLocal() as s:
            glob = s.scalar(select(Setting).where(Setting.scope == "global"))
            locations = config_push.location_options(s)
            return (dict(glob.data) if glob else None), (locations or None)

    def _upsert_relay_agent(s: Session, name: str, d: dict) -> None:
        agent = s.scalar(select(Agent).where(Agent.code == d["code"]))
        if agent is None:
            agent = Agent(code=d["code"], hostname=d["hostname"], version=d["version"],
                          status=Status.OK, last_payload={}, recent_logs=[],
                          token_hash=None, via="relay", relay=name)
            s.add(agent)
        agent.hostname, agent.version = d["hostname"], d["version"]
        agent.via, agent.relay = "relay", name
        agent.status = d["status"]
        agent.last_heartbeat = datetime.fromisoformat(d["last_heartbeat"])
        agent.recent_logs = d["recent_logs"]
        agent.last_payload = {
            "recording": d["recording"], "profile": d["profile"], "current_segment": d["current_segment"],
            "checks": d["checks"], "problems": d["problems"], "restarts": d["restarts"],
            "pending_sync": d["pending_sync"], "disk_used": d["disk_used"],
            "camera_resolutions": d["camera_resolutions"],
        }

    def _upsert_relay_recording(s: Session, name: str, rec: dict) -> None:
        # 主服务器只存索引，文件留在中转节点；用 (relay_id, relay_rec_id) 去重
        exists = s.scalar(select(Recording.id).where(
            Recording.relay_id == name, Recording.relay_rec_id == rec["id"]))
        if exists is not None:
            return
        s.add(Recording(
            code=rec["code"], path=rec["path"], source_filename=rec["source_filename"],
            start_at=datetime.fromisoformat(rec["start_at"]),
            end_at=datetime.fromisoformat(rec["end_at"]),
            size=rec["size"], sha256=rec["sha256"], integrity=rec["integrity"],
            status="synced", locked=False, relay_id=name, relay_rec_id=rec["id"],
        ))

    def _relay_failed(name: str, reason: str) -> None:
        now = datetime.now(UTC)
        with SessionLocal() as s:
            row = s.scalar(select(Relay).where(Relay.name == name))
            if row is None:
                return
            row.failures += 1
            # 连续 3 次拉不到：只出一条中转节点失联告警，不逐台报失联（tasks.md WP10）
            if row.failures == 3 and row.status != "lost":
                row.status = "lost"
                s.add(Event(type="relay.lost", severity="critical", code=None,
                            summary=f"子公司中转节点 {name} 失联，连续 3 次拉取失败",
                            occurred_at=now, payload={"reason": reason}))
                log.warning("中转节点 %s 失联：%s", name, reason)
            s.commit()

    def _relay_store(name: str, body: dict) -> None:
        now = datetime.now(UTC)
        with SessionLocal() as s:
            row = s.scalar(select(Relay).where(Relay.name == name))
            if row is None:
                return
            if row.status == "lost":
                s.add(Event(type="relay.recovered", severity="info", code=None,
                            summary=f"子公司中转节点 {name} 恢复连接", occurred_at=now, payload={}))
                log.info("中转节点 %s 恢复连接", name)
            row.failures = 0
            row.status = "ok"
            row.last_pull_at = now
            row.since_event = body["last_event_id"]
            row.since_recording = body["last_recording_id"]
            for d in body["devices"]:
                _upsert_relay_agent(s, name, d)
            for e in body["events"]:
                s.add(Event(type=e["type"], severity=e["severity"], code=e["code"],
                            summary=e["summary"], payload=e["payload"],
                            occurred_at=datetime.fromisoformat(e["occurred_at"])))
            for rec in body["recordings"]:
                _upsert_relay_recording(s, name, rec)
            s.commit()

    def _pull_one(name: str, url: str, push_settings: dict | None, push_locations: dict | None) -> None:
        with SessionLocal() as s:
            row = s.scalar(select(Relay).where(Relay.name == name))
            since_event = row.since_event if row else 0
            since_recording = row.since_recording if row else 0
        payload = {"since_event": since_event, "since_recording": since_recording,
                   "settings": push_settings, "location_options": push_locations}
        try:
            r = httpx.post(f"{url}/relay/v1/pull", json=payload,
                           headers={"Authorization": f"Bearer {settings.relay_pull_token}"},
                           timeout=15)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            _relay_failed(name, str(e))
            return
        _relay_store(name, body)

    def relay_pull_once() -> None:
        if not settings.relays:
            return
        push_settings, push_locations = _relay_push_config()
        for name, url in settings.relays.items():
            _pull_one(name, url, push_settings, push_locations)

    def relay_puller() -> None:
        while not stop.wait(settings.relay_pull_seconds):
            try:
                relay_pull_once()
            except Exception:
                log.exception("拉取中转节点出错")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _ensure_relay_rows()
        threads = [
            threading.Thread(target=sweeper, daemon=True, name="lost-sweeper"),
            threading.Thread(target=retention_worker, daemon=True, name="retention"),
            threading.Thread(target=relay_puller, daemon=True, name="relay-puller"),
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
    app.state.relay_pull_once = relay_pull_once  # 测试用
    app.include_router(uploads_router)
    _ensure_admin_hash()

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        """除 /healthz、登录接口外，/api/v1/* 都要登录；/devices 另接受只读 API 令牌。"""
        path = request.url.path
        if path.startswith("/api/v1/"):
            if path == "/api/v1/login":
                return await call_next(request)
            if path == "/api/v1/devices" and _valid_bearer(request):
                return await call_next(request)
            if not _valid_session(request):
                return JSONResponse(status_code=401, content={"detail": "未登录"})
        return await call_next(request)

    @app.post("/api/v1/login")
    def login(payload: dict, s: Session = Depends(get_db)):
        password = payload.get("password", "")
        if not _verify_admin(s, password):
            raise HTTPException(status_code=401, detail="密码不对")
        log_operation(s, "login", None, user="admin")
        s.commit()
        token = auth.make_session_token(session_secret)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
            max_age=auth.SESSION_TTL, path="/",
        )
        return resp

    @app.post("/api/v1/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    @app.post("/api/v1/devices/{code}/revoke")
    def revoke_device(code: str, s: Session = Depends(get_db)):
        """吊销设备令牌：该 Agent 之后心跳/上传被拒，但本地照常录制（tasks.md WP5）。"""
        agent = s.scalar(select(Agent).where(Agent.code == code))
        if agent is None:
            raise HTTPException(status_code=404, detail="没有这台设备")
        agent.token_hash = None
        log_operation(s, "settings", None, detail=f"吊销设备 {code}")
        s.commit()
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    @app.post("/agent/v1/heartbeat", response_model=HeartbeatReply)
    def heartbeat(hb: Heartbeat, request: Request, s: Session = Depends(get_db)):
        now = utcnow()
        agent = s.scalar(select(Agent).where(Agent.code == hb.code))
        token: str | None = None
        if agent is None:
            # 新设备：注册并签发设备令牌（只存哈希）
            token, token_hash = auth.new_agent_token()
            agent = Agent(code=hb.code, hostname=hb.hostname, version=hb.agent_version,
                          status=Status.OK, last_payload={}, recent_logs=[], token_hash=token_hash)
            s.add(agent)
            s.flush()
            log.info("新设备注册：%s（%s）", hb.code, hb.hostname)
        else:
            # 已注册：校验设备令牌（吊销或缺失都拒绝）
            presented = request.headers.get("X-Agent-Token", "")
            if not auth.verify_agent_token(presented, agent.token_hash):
                raise HTTPException(status_code=401, detail="设备凭据无效")
        agent.hostname, agent.version, agent.last_heartbeat = hb.hostname, hb.agent_version, now
        agent.last_payload = hb.model_dump(mode="json", exclude={"recent_logs"})
        if hb.recent_logs:
            logs = (agent.recent_logs or []) + [x.model_dump(mode="json") for x in hb.recent_logs]
            agent.recent_logs = logs[-100:]
        record_transition(s, agent, device_status(agent, now, settings.lost_after_seconds), now)
        reply = HeartbeatReply(
            config_version=config_push.get_config_version(s),
            settings=config_push.agent_settings(config_push.effective_settings(s, hb.code)),
            location_options=config_push.location_options(s),
            token=token,
        )
        s.commit()
        return reply

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
                camera_resolutions=p.get("camera_resolutions", []),
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
        if rec.relay_id:  # 文件在中转节点上：按需代理取并短期缓存（tasks.md WP10）
            return _serve_relay_file(s, rec, action, as_attachment)
        path = Path(rec.path)
        # 防越权：只服务录像目录内的文件（上传接口已保证，这里再兜底一次）
        if not path.resolve().is_relative_to(settings.recordings_dir.resolve()) or not path.exists():
            raise HTTPException(status_code=404, detail="录像文件不存在")
        log_operation(s, action, rec.id)
        s.commit()
        filename = rec.source_filename or path.name
        return FileResponse(path, media_type="video/mp4", filename=filename if as_attachment else None)

    # ---------- 中转节点分段代理缓存（tasks.md WP10：短期缓存 + 总量封顶） ----------

    def _relay_cache_dir() -> Path:
        d = settings.recordings_dir / "_relay_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _relay_cache_evict() -> None:
        d = _relay_cache_dir()
        files = sorted(d.glob("*.mp4"), key=lambda f: f.stat().st_mtime)  # 最久未用优先淘汰
        cap = settings.relay_cache_max_mb * 1024 * 1024
        total = sum(f.stat().st_size for f in files)
        for f in files:
            if total <= cap:
                break
            total -= f.stat().st_size
            f.unlink(missing_ok=True)

    def _serve_relay_file(s: Session, rec: Recording, action: str, as_attachment: bool) -> FileResponse:
        relay_row = s.scalar(select(Relay).where(Relay.name == rec.relay_id))
        if relay_row is None or rec.relay_rec_id is None:
            raise HTTPException(status_code=404, detail="中转节点不存在")
        cached = _relay_cache_dir() / f"{rec.relay_id}_{rec.relay_rec_id}.mp4"
        if not cached.exists():
            try:
                r = httpx.get(
                    f"{relay_row.url}/relay/v1/recordings/{rec.relay_rec_id}/file",
                    headers={"Authorization": f"Bearer {settings.relay_pull_token}"}, timeout=60,
                )
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise HTTPException(status_code=502, detail=f"取中转节点分段失败：{e}") from None
            cached.write_bytes(r.content)
            _relay_cache_evict()
        log_operation(s, action, rec.id)
        s.commit()
        filename = rec.source_filename or cached.name
        return FileResponse(cached, media_type="video/mp4", filename=filename if as_attachment else None)

    # ---------- 设置（设计文档 7、10 设置页；tasks.md WP4） ----------

    _CODE_RE = re.compile(r"^[A-Za-z0-9-]+$")

    @app.get("/api/v1/settings")
    def get_settings(s: Session = Depends(get_db)):
        devices = {r.scope[len("device:"):]: r.data for r in s.scalars(
            select(Setting).where(Setting.scope.like("device:%")))}
        prefixes = {r.scope[len("prefix:"):]: r.data for r in s.scalars(
            select(Setting).where(Setting.scope.like("prefix:%")))}
        global_row = s.scalar(select(Setting).where(Setting.scope == "global"))
        return {
            "config_version": config_push.get_config_version(s),
            "global": global_row.data if global_row else {},
            "devices": devices,
            "prefixes": prefixes,
            "location_options": config_push.location_options(s),
            "disk": disk_info(settings.recordings_dir),
        }

    @app.put("/api/v1/settings/global")
    def put_global_settings(data: dict, s: Session = Depends(get_db)):
        try:
            validated = config_push.validate_settings(data)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        row = s.scalar(select(Setting).where(Setting.scope == "global"))
        if row is None:
            row = Setting(scope="global", data={})
            s.add(row)
        row.data = {**row.data, **validated}
        config_push.bump_config_version(s)
        log_operation(s, "settings", None, detail="全局设置")
        s.commit()
        return {"ok": True, "config_version": config_push.get_config_version(s)}

    @app.put("/api/v1/settings/device/{code}")
    def put_device_settings(code: str, data: dict, s: Session = Depends(get_db)):
        if not _CODE_RE.fullmatch(code):
            raise HTTPException(status_code=422, detail="位置编号只能用英文、数字、连字符")
        try:
            validated = config_push.validate_settings(data)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        row = s.scalar(select(Setting).where(Setting.scope == f"device:{code}"))
        if row is None:
            row = Setting(scope=f"device:{code}", data={})
            s.add(row)
        row.data = {**row.data, **validated}
        config_push.bump_config_version(s)
        log_operation(s, "settings", None, detail=f"设备 {code} 设置")
        s.commit()
        return {"ok": True, "config_version": config_push.get_config_version(s)}

    @app.delete("/api/v1/settings/device/{code}")
    def delete_device_settings(code: str, s: Session = Depends(get_db)):
        row = s.scalar(select(Setting).where(Setting.scope == f"device:{code}"))
        if row is not None:
            s.delete(row)
            config_push.bump_config_version(s)
            log_operation(s, "settings", None, detail=f"删除设备 {code} 设置")
            s.commit()
        return {"ok": True}

    @app.get("/api/v1/quality")
    def get_quality():
        """画质挡位表（前端不写死，从这里取；tasks.md WP4）。"""
        from everrec_core import profiles

        return {
            "quality": profiles.quality_table(),
            "resolutions": [r.value for r in profiles.Resolution],
            "resolution_sizes": {r.value: list(profiles.RESOLUTION_SIZE[r]) for r in profiles.Resolution},
            "fps": list(profiles.FPS_STOPS),
            "recommended_quality": profiles.DEFAULT_QUALITY.value,
            "recommended_resolution": profiles.RECOMMENDED_RESOLUTION.value,
            "recommended_fps": profiles.RECOMMENDED_FPS,
        }

    @app.put("/api/v1/location-options")
    def put_location_options(data: dict[str, list[str]], s: Session = Depends(get_db)):
        for level in data:
            if level not in config_push.LEVELS:
                raise HTTPException(status_code=422, detail=f"不支持的位置层级：{level}")
        for row in s.scalars(select(LocationOption)).all():
            s.delete(row)
        for level, values in data.items():
            for v in values:
                if not _CODE_RE.fullmatch(v):
                    raise HTTPException(status_code=422, detail="位置选项只能用英文、数字、连字符")
                s.add(LocationOption(level=level, value=v))
        config_push.bump_config_version(s)
        log_operation(s, "settings", None, detail="位置选项")
        s.commit()
        return {"ok": True}

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
