"""Agent 自动更新：检查、下载校验、静默安装（tasks.md WP9）。

下载与 SHA-256 校验用假 HTTP 服务器实测；安装器是 Windows 专用步骤，用假 runner 注入。
"""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from everrec_agent import updater


class _Handler(BaseHTTPRequestHandler):
    """极简 HTTP 服务：/version-check 返回 JSON，/package 返回固定字节。"""

    payload = b""
    plan: dict = {}

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/agent/v1/version-check"):
            body = updater_plan_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/agent/v1/update-package") or self.path.startswith("/package"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(self.payload)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # 静音
        pass


def updater_plan_bytes() -> bytes:
    import json

    return json.dumps(_Handler.plan).encode()


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()


def test_check_returns_plan(server):
    _Handler.plan = {"update_available": True, "should_update": True,
                     "latest_version": "9.9.9", "update_mode": "auto",
                     "sha256": "", "size": 0, "download_url": "/package", "notes": None}
    plan = updater.check(server, "012", "tok", "0.0.1")
    assert plan is not None and plan.should_update and plan.latest_version == "9.9.9"


def test_check_unreachable_returns_none():
    plan = updater.check("http://127.0.0.1:1", "012", "tok", "0.0.1")
    assert plan is None


def test_download_verifies_sha256(server):
    data = b"installer-bytes"
    _Handler.payload = data
    good = hashlib.sha256(data).hexdigest()
    path = updater.download(server, "012", "tok", "/package", good)
    assert path is not None and path.read_bytes() == data
    path.unlink(missing_ok=True)


def test_download_bad_sha_discards(server):
    _Handler.payload = b"installer-bytes"
    path = updater.download(server, "012", "tok", "/package", "0" * 64)
    assert path is None


def test_run_update_notify_does_not_download(server):
    _Handler.plan = {"update_available": True, "should_update": False,
                     "latest_version": "9.9.9", "update_mode": "notify",
                     "sha256": "", "size": 0, "download_url": "/package", "notes": None}
    # 只提醒：不该走到下载/安装，注入会失败的 runner 以防误调
    action = updater.run_update(server, "012", "tok", "0.0.1",
                                runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该安装")))
    assert action == "notify"


def test_run_update_installs_with_fake_runner(server):
    data = b"installer"
    _Handler.payload = data
    _Handler.plan = {"update_available": True, "should_update": True,
                     "latest_version": "9.9.9", "update_mode": "auto",
                     "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                     "download_url": "/package", "notes": None}
    calls: list[list[str]] = []

    def fake_runner(cmd, **kw):
        calls.append(cmd)
        return object()

    action = updater.run_update(server, "012", "tok", "0.0.1", runner=fake_runner)
    assert action == "installed"
    assert calls and calls[0][0].endswith(".exe") and "/VERYSILENT" in calls[0]


def test_run_update_no_update(server):
    _Handler.plan = {"update_available": False, "should_update": False,
                     "latest_version": "0.0.1", "update_mode": "auto",
                     "sha256": "", "size": 0, "download_url": "", "notes": None}
    assert updater.run_update(server, "012", "tok", "0.0.1") == "no-update"


def test_version_is_newer():
    from everrec_core.version import is_newer

    assert is_newer("1.0.1", "1.0.0")
    assert not is_newer("1.0.0", "1.0.1")
    assert not is_newer("1.0.0", "1.0")
    assert is_newer("0.10.0", "0.9.9")
