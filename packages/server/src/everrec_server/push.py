"""事件推送（设计文档 8.3–8.5）：组装消息、HMAC 签名、发件箱投递与重试。

第一版一个推送地址（环境变量），只做通用 JSON；企业微信 / 钉钉不做。
- 消息格式见设计 8.4；请求头 X-Event-Id（去重）、X-Timestamp、X-Signature。
- 先存后发：事件先写 delivery 表，后台线程投递，服务重启不丢。
- 至少送达一次：可能重复，接收方用 eventId 去重。
- 重试 1 分钟 / 5 分钟 / 30 分钟 / 之后每 2 小时；连续失败超过 24 小时暂停。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from .db import Delivery
from .status import aware

log = logging.getLogger("everrec.push")

TIMEOUT = 5.0
RETRY_DELAYS = [60, 300, 1800]  # 秒：1 分钟、5 分钟、30 分钟
RETRY_CAP = 7200  # 之后每 2 小时
PAUSE_AFTER = 24 * 3600  # 连续失败超过 24 小时暂停
WORKER_INTERVAL = 5.0  # 投递线程每 5 秒扫一次


def utcnow() -> datetime:
    return datetime.now(UTC)


def build_message(
    event_type: str,
    severity: str,
    summary: str,
    code: str | None,
    detail: dict,
    occurred_at: datetime,
) -> dict:
    """按设计 8.4 组一条完整消息，eventId 唯一（接收方据此去重）。"""
    return {
        "eventId": f"evt_{uuid.uuid4().hex[:16]}",
        "eventType": event_type,
        "severity": severity,
        "occurredAt": occurred_at.isoformat(),
        "detectedAt": utcnow().isoformat(),
        "location": {"code": code or ""},
        "device": {"code": code or ""},
        "summary": summary,
        "detail": detail or {},
        "link": "",
    }


def sign(secret: str, timestamp: str, body: str) -> str:
    """X-Signature: sha256=HMAC(secret, 时间戳 + "." + 消息体)。"""
    digest = hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def retry_delay(attempts: int) -> int:
    """attempts 是已失败次数（1-based），返回下次重试要等多久。"""
    if attempts <= len(RETRY_DELAYS):
        return RETRY_DELAYS[attempts - 1]
    return RETRY_CAP


def enqueue(
    session,
    event_type: str,
    severity: str,
    summary: str,
    code: str | None,
    detail: dict,
    occurred_at: datetime,
) -> None:
    """事件入发件箱。event_id 有 DB 唯一约束兜底，同一事件不会重复入箱。"""
    message = build_message(event_type, severity, summary, code, detail, occurred_at)
    session.add(Delivery(
        event_id=message["eventId"],
        event_type=event_type,
        severity=severity,
        code=code,
        summary=summary,
        payload=message,
        status="pending",
        attempts=0,
    ))


def post_message(settings, event_id: str, payload: dict) -> httpx.Response:
    """把一条消息 POST 到推送地址，带签名头。"""
    body = json.dumps(payload, ensure_ascii=False)
    ts = utcnow().isoformat()
    headers = {
        "Content-Type": "application/json",
        "X-Event-Id": event_id,
        "X-Timestamp": ts,
    }
    if settings.webhook_secret:
        headers["X-Signature"] = sign(settings.webhook_secret, ts, body)
    headers.update(settings.webhook_headers)
    return httpx.post(settings.webhook_url, content=body.encode("utf-8"),
                      headers=headers, timeout=TIMEOUT)


def deliver_one(settings, delivery: Delivery) -> int:
    """投递一条，返回对方 HTTP 状态码；网络错误向上抛 httpx.HTTPError。"""
    return post_message(settings, delivery.event_id, delivery.payload).status_code


def deliver_pending(session_factory, settings) -> None:
    """扫一遍发件箱投递到期的；2xx 算送达，其余标记失败并安排重试。"""
    if not settings.webhook_url:
        return
    now = utcnow()
    with session_factory() as s:
        due = s.scalars(
            select(Delivery).where(Delivery.status.in_(["pending", "failed"])).order_by(Delivery.id)
        ).all()
        for d in due:
            if d.next_retry_at is not None and aware(d.next_retry_at) > now:
                continue  # 还没到重试时间
            try:
                code = deliver_one(settings, d)
                if 200 <= code < 300:
                    d.status, d.http_code, d.delivered_at = "sent", code, now
                    log.info("推送成功 %s（%s，%d）", d.event_id, d.event_type, code)
                else:
                    _mark_failed(d, code)
            except httpx.HTTPError as e:
                log.warning("推送失败 %s：%s", d.event_id, e)
                _mark_failed(d, None)
        s.commit()


def _mark_failed(d: Delivery, http_code: int | None) -> None:
    d.attempts += 1
    d.http_code = http_code
    # 连续失败超过 24 小时：暂停，不再投（设备墙顶部提示留给 WP4 的界面）
    if (utcnow() - aware(d.created_at)).total_seconds() >= PAUSE_AFTER:
        d.status = "paused"
        log.error("推送连续失败超过 24 小时，暂停：%s", d.event_id)
    else:
        d.status = "failed"
        d.next_retry_at = utcnow() + timedelta(seconds=retry_delay(d.attempts))


def worker(session_factory, settings, stop_event) -> None:
    """后台投递线程。"""
    while not stop_event.wait(WORKER_INTERVAL):
        try:
            deliver_pending(session_factory, settings)
        except Exception:
            log.exception("投递扫描出错")
