"""WP1 端到端：真服务端 + 3 个 test 摄像头 Agent，停服 1 分钟再恢复。

验证：分段全部到服务器、本地已删、哈希合法、每个 Agent 的索引段数 == 已同步 == 服务器段数。
用法：uv run python scripts/e2e_upload.py [--offline 60] [--run-seconds 20]
"""

from __future__ import annotations

import argparse
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from everrec_agent.agent import Agent
from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording
from everrec_core.segments import iter_archived
from everrec_server.db import Recording as ServerRecording
from everrec_server.main import create_app
from everrec_server.settings import Settings

# Windows 控制台默认 cp1252，中文输出会崩；统一成 UTF-8
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_app(db: Path, recordings: Path):
    return create_app(
        Settings(database_url=f"sqlite:///{db}", recordings_dir=recordings, sweep_seconds=5)
    )


def run_server(app, port: int):
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.01)
    return server, t


def make_agent(root: Path, port: int, number: str) -> AgentConfig:
    cfg = AgentConfig(
        server=f"http://127.0.0.1:{port}",
        location=Location(state="HD", building="A", number=number),
        camera=Camera(type=CameraType.TEST),
        recording=Recording(segment_seconds=5),
        heartbeat_seconds=1,
    )
    cfg.install_dir = root / number
    return cfg


def wait_synced(base: str, expected: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            body = httpx.get(f"{base}/api/v1/devices", timeout=2).json()
        except httpx.HTTPError:
            time.sleep(1)
            continue
        items = {d["code"]: d for d in body["items"]}
        if len(items) == expected and all(d["pending_sync"] == 0 for d in items.values()):
            return items
        time.sleep(1)
    raise RuntimeError("设备没在超时内全部同步完")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", type=int, default=60, help="停服秒数（模拟断网）")
    ap.add_argument("--run-seconds", type=int, default=20, help="停服前先正常录多少秒")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="everrec-e2e-"))
    port = free_port()
    db = tmp / "everrec.db"
    recordings = tmp / "recordings"
    base = f"http://127.0.0.1:{port}"
    print(f"临时目录：{tmp}")

    server, server_thread = run_server(make_app(db, recordings), port)
    print(f"服务端启动：{base}")

    agents: list[tuple[AgentConfig, Agent, threading.Thread]] = []
    for number in ("001", "002", "003"):
        cfg = make_agent(tmp / "agents", port, number)
        agent = Agent(cfg)
        t = threading.Thread(target=agent.run, daemon=True)
        t.start()
        agents.append((cfg, agent, t))
    print("3 个 Agent 已启动（testsrc2 测试画面，5 秒一段，实时上传）")

    print(f"\n① 正常录制 {args.run_seconds} 秒…")
    time.sleep(args.run_seconds)

    print(f"② 停服务端 {args.offline} 秒（模拟断网，Agent 本地继续录、积压）…")
    server.should_exit = True
    server_thread.join(timeout=5)

    time.sleep(args.offline)

    print("③ 恢复服务端，等待补传…")
    server, server_thread = run_server(make_app(db, recordings), port)
    items = wait_synced(base, 3, timeout=180)
    for code in sorted(items):
        d = items[code]
        print(f"   {code}: status={d['status']} pending_sync={d['pending_sync']}")

    # 停掉 Agent（正常停止会尽量把最后一段也送完）
    for _cfg, agent, t in agents:
        agent.stop_event.set()
        t.join(timeout=30)

    # 校验
    with Session(create_engine(f"sqlite:///{db}")) as s:
        rows = s.scalars(
            select(ServerRecording).order_by(ServerRecording.code, ServerRecording.start_at)
        ).all()

    per: dict[str, list] = {}
    for r in rows:
        per.setdefault(r.code, []).append(r)

    total = len(rows)
    print(f"\n服务器 recording 行数：{total}")

    ok = total > 0
    print("按设备核对（索引段数 == 已同步 == 服务器段数，本地已删，哈希合法）：")
    for cfg, agent, _t in agents:
        entries = agent.index.entries()
        synced = sum(1 for e in entries if e.status == "synced")
        server_n = len(per.get(cfg.code, []))
        local_left = len(list(iter_archived(cfg.recordings_dir)))
        shas_ok = all(
            len(r.sha256) == 64 and set(r.sha256) <= set("0123456789abcdef")
            for r in per.get(cfg.code, [])
        )
        print(
            f"   {cfg.code}: 索引 {len(entries)} / 已同步 {synced} / "
            f"服务器 {server_n} / 本地剩余 {local_left} / sha256 合法 {shas_ok}"
        )
        ok = ok and len(entries) == synced == server_n and local_left == 0 and shas_ok

    if ok:
        print("\n✅ 端到端通过：分段全部到服务器、本地已删、时间连续、哈希合法")
        return 0
    print("\n❌ 校验未通过，临时目录保留：", tmp)
    return 1


if __name__ == "__main__":
    sys.exit(main())
