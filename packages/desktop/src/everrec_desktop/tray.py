"""系统托盘图标（设计 4.1）：常驻右下角，悬浮看状态，单击恢复客户端，菜单退出。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from everrec_core.config import load_config

from .common import LEVEL_COLORS, open_url, overall_state, read_status, recording_duration


def _dot_icon(color: str) -> QIcon:
    """按状态色画一个圆点（16×16）当托盘图标。"""
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(2, 2, 12, 12)
    p.end()
    return QIcon(pm)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, install_dir: Path, on_open, on_quit):
        super().__init__()
        self.state_dir = install_dir / "state"
        self.config_path = install_dir / "config" / "everrec.yaml"
        self._on_open = on_open
        self._on_quit = on_quit

        self.setToolTip("EverRec")
        self.activated.connect(self._on_activated)

        menu = QMenu()
        self.state_item = menu.addAction("—")
        self.state_item.setEnabled(False)
        menu.addSeparator()
        open_action = menu.addAction("打开客户端")
        open_action.triggered.connect(self._on_open)
        website_action = menu.addAction("打开管理网站")
        website_action.triggered.connect(self._open_website)
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self._on_quit)
        self.setContextMenu(menu)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # 左键单击
            self._on_open()

    def _open_website(self) -> None:
        cfg = load_config(self.config_path) if self.config_path.exists() else None
        url = (cfg.server or "").strip() if cfg else ""
        if url:
            open_url(url)

    def refresh(self) -> None:
        st = read_status(self.state_dir)
        state = overall_state(st)
        self.setIcon(_dot_icon(LEVEL_COLORS.get(state["level"], LEVEL_COLORS["idle"])["fg"]))

        lines = [state["title"]]
        online = st.get("online")
        pending = int(st.get("pending_sync", 0))
        if st.get("stale") or online is False:
            lines.append("未连接服务器")
        elif online is None:
            lines.append("本机独立录像")
        elif pending:
            lines.append(f"待上传 {pending} 段")
        else:
            lines.append("已同步")
        if st.get("recording"):
            lines.append(recording_duration(self.state_dir))
        summary = " · ".join(lines)
        self.setToolTip(summary)
        self.state_item.setText(summary)
