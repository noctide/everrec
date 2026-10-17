"""托盘状态窗（设计 4.1）：只显示状态，不提供停止录制。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .common import COLORS, build_timeline, format_duration, read_status


def _install_dir(args: argparse.Namespace) -> Path:
    default = "D:\\EverRec" if sys.platform == "win32" else str(Path.home() / "everrec")
    return Path(getattr(args, "install_dir", None) or default)


class TrayWindow(QWidget):
    def __init__(self, install_dir: Path):
        super().__init__()
        self.state_dir = install_dir / "state"
        self.setWindowTitle("EverRec 托盘状态")
        self.setFixedSize(360, 320)
        self.setStyleSheet(f"background: {COLORS['bg']}; color: {COLORS['text']}; font-size: 14px;")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        head = QHBoxLayout()
        self.dot = QLabel()
        self.dot.setFixedSize(14, 14)
        self.title = QLabel("正在录像")
        self.title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.subtitle = QLabel("本工位的画面正在被录制")
        self.subtitle.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        tt = QVBoxLayout()
        tt.setSpacing(0)
        tt.addWidget(self.title)
        tt.addWidget(self.subtitle)
        head.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addLayout(tt)
        root.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        box = QWidget()
        box.setLayout(grid)
        box.setStyleSheet(f"background: {COLORS['bar']}; border-radius: 6px;")
        self._vals: dict[str, QLabel] = {}
        for r, key in enumerate(("位置编号", "已连续录制", "服务器", "本地空间")):
            k = QLabel(key)
            k.setStyleSheet(f"color: {COLORS['muted']};")
            v = QLabel("—")
            v.setStyleSheet("font-family: Consolas, monospace;")
            self._vals[key] = v
            grid.addWidget(k, r, 0)
            grid.addWidget(v, r, 1)
        root.addWidget(box)

        self.note = QLabel("录像由公司统一管理，用于设备操作区安全记录。如有疑问请联系设备管理员。")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        root.addWidget(self.note)

        footer = QHBoxLayout()
        self.version = QLabel("EverRec Agent")
        self.version.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        self.open_client = QPushButton("打开客户端")
        self.open_client.setStyleSheet(
            f"color: {COLORS['accent']}; border: none; background: transparent; font-size: 12px;"
        )
        self.open_client.clicked.connect(lambda: subprocess.Popen(
            ["everrec-client", "--install-dir", str(install_dir)]))
        footer.addWidget(self.version)
        footer.addStretch(1)
        footer.addWidget(self.open_client)
        root.addLayout(footer)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def refresh(self) -> None:
        st = read_status(self.state_dir)
        recording = bool(st.get("recording"))
        self.dot.setStyleSheet(
            f"border-radius: 7px; background: {COLORS['accent'] if recording else COLORS['note']};"
        )
        self.title.setText("正在录像" if recording else "没在录像")
        self.subtitle.setText(
            "本工位的画面正在被录制" if recording else "录制已停止，请检查摄像头或联系管理员"
        )

        bars = build_timeline(self.state_dir)
        if bars:
            now_h = datetime.now().hour + datetime.now().minute / 60
            dur = format_duration((now_h - bars[0].start) * 3600)
        else:
            dur = "—"

        online = st.get("online")
        pending = int(st.get("pending_sync", 0))
        if online is False:
            server = "未连接"
        elif pending:
            server = f"已连接 · 待上传 {pending} 段"
        else:
            server = "已连接 · 已同步"

        self._vals["位置编号"].setText(st.get("code", "—"))
        self._vals["已连续录制"].setText(dur)
        self._vals["服务器"].setText(server)
        self._vals["本地空间"].setText(f"已用 {int(float(st.get('disk_used', 0)) * 100)}%")
        self.version.setText(f"EverRec Agent {st.get('agent_version', '')}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="everrec-tray")
    ap.add_argument("--install-dir", help="安装目录，默认 D:\\EverRec")
    args = ap.parse_args()
    app = QApplication(sys.argv)
    w = TrayWindow(_install_dir(args))
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
