"""生成桌面端三个窗口的截图（PR 用）。先造假数据，再 grab 每个窗口。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtWidgets import QApplication

from everrec_core.ffmpeg import ffmpeg_binary
from everrec_desktop.client import ClientWindow
from everrec_desktop.repair_tool import RepairTool
from everrec_desktop.tray import TrayWindow

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="wp8-shots-"))
OUT.mkdir(parents=True, exist_ok=True)


def make_fake_install() -> Path:
    root = Path(tempfile.mkdtemp(prefix="wp8-install-"))
    (root / "state").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    (root / "recordings").mkdir(parents=True)
    (root / "config").mkdir(parents=True)

    now = datetime.now()
    status = {
        "code": "HD-PD-A-3F-012", "hostname": "OP-PC-012", "agent_version": "0.1.0",
        "sent_at": now.isoformat(), "recording": True, "starting": False,
        "profile": "720P / 10 帧", "camera_resolutions": ["1280x720", "640x480"],
        "current_segment": "HD-PD-A-3F-012_104012.ts",
        "checks": {"service": "ok", "device": "ok", "process": "ok", "dataflow": "ok", "storage": "ok"},
        "problems": [], "restarts": 0, "pending_sync": 0, "disk_used": 0.06,
        "offline_since": None, "online": True,
    }
    (root / "state" / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")

    subprocess.run(
        [ffmpeg_binary(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=10",
         "-frames:v", "1", "-q:v", "5", str(root / "state" / "thumb.jpg")],
        check=True,
    )

    segs = []
    for i, synced in enumerate([True, True, True, False]):
        t = now.replace(hour=9, minute=0, second=0) + timedelta(minutes=i * 40)
        segs.append({"path": f"HD-PD-A-3F-012_{t:%H%M%S}.ts", "started_at": t.isoformat(),
                     "size": 1000000, "integrity": "ok", "status": "synced" if synced else "local"})
    (root / "state" / "segments.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in segs), encoding="utf-8")

    logs = [
        "2026-10-17 10:40:05,123 INFO    agent: 分段 012_103500.ts 已上传（2.2 MB）",
        "2026-10-17 10:35:00,000 INFO    agent: 开始新分段 012_103500.ts",
        "2026-10-17 10:08:14,000 INFO    uploader: 服务器已连接，开始补传 46 段",
        "2026-10-17 06:16:40,000 WARNING agent: 连接服务器失败：超时，转为离线本地录制",
    ]
    (root / "logs" / "agent.log").write_text("\n".join(logs), encoding="utf-8")
    return root


def main() -> int:
    app = QApplication(sys.argv)
    root = make_fake_install()

    repair = RepairTool()
    repair.resize(960, 640)
    repair.show()
    app.processEvents()
    repair.grab().save(str(OUT / "repair-tool.png"))

    tray = TrayWindow(root)
    tray.show()
    app.processEvents()
    tray.grab().save(str(OUT / "tray.png"))

    client = ClientWindow(root)
    client.show()
    app.processEvents()
    client.grab().save(str(OUT / "client.png"))

    print(f"截图保存到 {OUT}")
    for f in sorted(OUT.glob("*.png")):
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
