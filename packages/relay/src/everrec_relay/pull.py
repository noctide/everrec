"""北向 /relay/v1/pull（tasks.md WP10）：主服务器每 30 秒拉一次。

增量返回子公司设备状态、新事件、新增录像索引；并接收主服务器下发的配置变更。
用独立密钥（Bearer）+ HTTPS 认证，不依赖域账号。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from everrec_server import config_push
from everrec_server.db import Agent, Event, Recording, Setting
from everrec_server.status import aware, device_status

from .settings import RelaySettings


class PullRequest(BaseModel):
    since_event: int = 0
    since_recording: int = 0
    # 主服务器下发的配置：settings 为全局设置，location_options 为位置选项；为 None 表示本次不下发
    settings: dict | None = None
    location_options: dict[str, list[str]] | None = None


class PullReply(BaseModel):
    devices: list[dict]
    events: list[dict]
    recordings: list[dict]
    last_event_id: int
    last_recording_id: int
    config_version: int


def _check_token(request: Request, settings: RelaySettings) -> None:
    presented = request.headers.get("Authorization", "")
    if not settings.pull_token or presented != f"Bearer {settings.pull_token}":
        raise HTTPException(status_code=401, detail="中转节点凭据无效")


def _apply_settings(s: Session, data: dict) -> bool:
    """把主服务器下发的全局设置写进 relay 自己的 setting 表；有变化才递增版本号。"""
    try:
        validated = config_push.validate_settings(data)
    except ValueError:
        return False  # 主服务器只会下发合法设置；兜底忽略非法项
    row = s.scalar(select(Setting).where(Setting.scope == "global"))
    if row is not None and row.data == validated:
        return False
    if row is None:
        row = Setting(scope="global", data={})
        s.add(row)
    row.data = validated
    config_push.bump_config_version(s)
    return True


def _apply_location_options(s: Session, data: dict[str, list[str]]) -> bool:
    """旧的平铺位置选项：组织树改版后，中转节点的树由本地设备心跳自动生成，这里只接收、不处理。"""
    return False


def _serialize_devices(s: Session, lost_after: int) -> list[dict]:
    now = datetime.now(UTC)
    out: list[dict] = []
    for a in s.scalars(select(Agent).order_by(Agent.code)):
        st = device_status(a, now, lost_after)
        p = a.last_payload or {}
        last = aware(a.last_heartbeat)
        out.append({
            "code": a.code, "hostname": a.hostname, "version": a.version, "status": st.value,
            "last_heartbeat": last.isoformat(),
            "seconds_since_heartbeat": int((now - last).total_seconds()),
            "recording": bool(p.get("recording")), "profile": p.get("profile"),
            "current_segment": p.get("current_segment"), "checks": p.get("checks", {}),
            "problems": p.get("problems", []), "restarts": p.get("restarts", 0),
            "pending_sync": p.get("pending_sync", 0), "disk_used": p.get("disk_used", 0),
            "recent_logs": (a.recent_logs or [])[-20:],
            "camera_resolutions": p.get("camera_resolutions", []),
            "location": p.get("location", {}),
        })
    return out


def _serialize_events(s: Session, since_event: int) -> list[dict]:
    rows = s.scalars(select(Event).where(Event.id > since_event).order_by(Event.id)).all()
    return [{
        "id": e.id, "type": e.type, "severity": e.severity, "code": e.code,
        "summary": e.summary, "payload": e.payload,
        "occurred_at": aware(e.occurred_at).isoformat(),
    } for e in rows]


def _serialize_recordings(s: Session, since_recording: int) -> list[dict]:
    rows = s.scalars(select(Recording).where(Recording.id > since_recording).order_by(Recording.id)).all()
    return [{
        "id": r.id, "code": r.code, "start_at": aware(r.start_at).isoformat(),
        "end_at": aware(r.end_at).isoformat(), "size": r.size, "sha256": r.sha256,
        "integrity": r.integrity, "source_filename": r.source_filename, "path": r.path,
    } for r in rows]


def mount_pull(app: FastAPI, settings: RelaySettings) -> None:
    SessionLocal: sessionmaker = app.state.SessionLocal  # 复用服务端的会话工厂
    lost_after = settings.server.lost_after_seconds

    @app.post("/relay/v1/pull", response_model=PullReply)
    def pull(req: PullRequest, request: Request) -> PullReply:
        _check_token(request, settings)
        with SessionLocal() as s:
            if req.settings is not None:
                _apply_settings(s, req.settings)
            if req.location_options is not None:
                _apply_location_options(s, req.location_options)
            s.commit()
            devices = _serialize_devices(s, lost_after)
            events = _serialize_events(s, req.since_event)
            recordings = _serialize_recordings(s, req.since_recording)
            last_ev = s.scalar(select(Event.id).order_by(Event.id.desc()).limit(1)) or 0
            last_rec = s.scalar(select(Recording.id).order_by(Recording.id.desc()).limit(1)) or 0
            return PullReply(
                devices=devices, events=events, recordings=recordings,
                last_event_id=last_ev, last_recording_id=last_rec,
                config_version=config_push.get_config_version(s),
            )

    @app.get("/relay/v1/recordings/{recording_id}/file")
    def relay_recording_file(recording_id: int, request: Request) -> FileResponse:
        """主服务器回放时按需取分段（tasks.md WP10）。"""
        _check_token(request, settings)
        with SessionLocal() as s:
            rec = s.get(Recording, recording_id)
            if rec is None:
                raise HTTPException(status_code=404, detail="没有这段录像")
        path = Path(rec.path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="录像文件不存在")
        return FileResponse(path, media_type="video/mp4")
