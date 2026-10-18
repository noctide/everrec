"""事件推送：签名、重试、去重、重启续投、批量失联合并、storage.low、测试推送接口。"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from everrec_core.protocol import Checks, Heartbeat
from everrec_server import push
from everrec_server.db import Delivery, make_session_factory
from everrec_server.main import create_app
from everrec_server.settings import Settings


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append({"body": json.loads(body), "headers": dict(self.headers)})
        code = self.server.status_codes.pop(0) if self.server.status_codes else 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args):
        pass


class FakeWebhook:
    """本地假接收端，记录收到的请求，可指定依次返回的状态码。"""

    def __init__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.requests: list[dict] = []
        self._server.status_codes: list[int] = []
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def webhook():
    fw = FakeWebhook()
    yield fw
    fw.stop()


def _settings(tmp_path, webhook, secret=""):
    return Settings(
        database_url=f"sqlite:///{tmp_path}/t.db",
        webhook_url=webhook.url,
        webhook_secret=secret,
        sweep_seconds=3600, admin_password="test", secret_key="test-secret",
    )


def _hb(code="HD-A-001", recording=True, **kw) -> dict:
    return Heartbeat(
        code=code, hostname="PC-01", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=recording, profile="standard", checks=Checks(**kw.pop("checks", {})), **kw,
    ).model_dump(mode="json")


def test_signature_verifiable(tmp_path, webhook):
    settings = _settings(tmp_path, webhook, secret="s3cret")
    sf = make_session_factory(settings.database_url)
    with sf() as s:
        push.enqueue(s, "device.lost", "critical", "设备失联", "HD-A-001", {}, datetime.now(UTC))
        s.commit()
    push.deliver_pending(sf, settings)

    assert len(webhook._server.requests) == 1
    req = webhook._server.requests[0]
    ts = req["headers"]["X-Timestamp"]
    body = json.dumps(req["body"], ensure_ascii=False)
    assert req["headers"]["X-Signature"] == push.sign("s3cret", ts, body)
    assert req["headers"]["X-Event-Id"] == req["body"]["eventId"]


def test_retry_then_success(tmp_path, webhook):
    webhook._server.status_codes = [500, 200]
    settings = _settings(tmp_path, webhook)
    sf = make_session_factory(settings.database_url)
    with sf() as s:
        push.enqueue(s, "device.lost", "critical", "设备失联", "HD-A-001", {}, datetime.now(UTC))
        s.commit()

    push.deliver_pending(sf, settings)  # 第一次 500 → failed
    with sf() as s:
        d = s.scalar(select(Delivery))
        assert d.status == "failed" and d.attempts == 1
        d.next_retry_at = None  # 提前重试
        s.commit()

    push.deliver_pending(sf, settings)  # 第二次 200 → sent
    with sf() as s:
        assert s.scalar(select(Delivery)).status == "sent"
    assert len(webhook._server.requests) == 2


def test_restart_continues_delivery(tmp_path, webhook):
    settings = _settings(tmp_path, webhook)
    sf = make_session_factory(settings.database_url)
    with sf() as s:
        push.enqueue(s, "device.lost", "critical", "设备失联", "HD-A-001", {}, datetime.now(UTC))
        s.commit()
    # 模拟服务重启：新建 session factory，未投递的仍在库里
    sf2 = make_session_factory(settings.database_url)
    push.deliver_pending(sf2, settings)
    assert len(webhook._server.requests) == 1


def test_mass_lost_merges_into_one(tmp_path, webhook):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t.db", webhook_url=webhook.url,
        lost_after_seconds=0, sweep_seconds=3600,
        admin_password="test", secret_key="test-secret",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        for i in range(5):
            c.post("/agent/v1/heartbeat", json=_hb(code=f"HD-{i}"))
        app.state.sweep_once()
        push.deliver_pending(app.state.SessionLocal, settings)
    types = [r["body"]["eventType"] for r in webhook._server.requests]
    assert types == ["mass_lost"], "大批失联只应推一条合并事件"


def test_storage_low_event(tmp_path, webhook):
    settings = _settings(tmp_path, webhook)
    app = create_app(settings)
    with TestClient(app) as c:
        token = c.post("/agent/v1/heartbeat", json=_hb()).json()["token"]  # storage=ok
        # 用 warn：不会触发设备异常（只有 fail 才算 ABNORMAL → recording.stopped）
        c.post("/agent/v1/heartbeat", json=_hb(checks={"storage": "warn"}, disk_used=0.88),
               headers={"X-Agent-Token": token})
        push.deliver_pending(app.state.SessionLocal, settings)
    reqs = webhook._server.requests
    assert [r["body"]["eventType"] for r in reqs] == ["storage.low"]
    assert reqs[0]["body"]["severity"] == "warning"


def test_push_test_endpoint(tmp_path, webhook):
    settings = _settings(tmp_path, webhook, secret="s3cret")
    app = create_app(settings)
    with TestClient(app) as c:
        c.post("/api/v1/login", json={"password": "test"})
        body = c.post("/api/v1/push/test").json()
        assert body["ok"] is True and body["status_code"] == 200
    assert len(webhook._server.requests) == 1
    assert webhook._server.requests[0]["body"]["eventType"] == "push.test"


def test_dedup_rejects_duplicate_event_id(tmp_path):
    sf = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    with sf() as s:
        s.add(Delivery(event_id="evt_x", event_type="device.lost", severity="critical",
                       code="HD-A-001", summary="x", payload={}, status="pending", attempts=0))
        s.commit()
        s.add(Delivery(event_id="evt_x", event_type="device.lost", severity="critical",
                       code="HD-A-001", summary="x", payload={}, status="pending", attempts=0))
        with pytest.raises(IntegrityError):
            s.commit()
