"""录像客户端（设计 4.1、4.7）：主界面 + 设置。只读 status.json 和日志，不直接控制录制。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from everrec_core.config import load_config, save_config, validate_tolerant

from .common import (
    COLORS,
    LIGHT_COLORS,
    LIGHT_TEXT,
    Board,
    build_board,
    build_timeline,
    friendly_level,
    read_logs,
    read_status,
)

CHECK_LABEL = {"service": "录制服务", "device": "摄像头", "process": "录制进程",
               "dataflow": "文件增长", "storage": "存储"}


def _install_dir(args: argparse.Namespace) -> Path:
    default = "D:\\EverRec" if sys.platform == "win32" else str(Path.home() / "everrec")
    return Path(getattr(args, "install_dir", None) or default)


class TimelineWidget(QWidget):
    """把今天的录像画成一条 0-24 小时的条：深色=已上传，浅灰=仅本机。"""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(22)
        self.bars = []

    def set_bars(self, bars) -> None:
        self.bars = bars
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setPen(Qt.PenStyle.NoPen)
        w, h = self.width(), self.height()
        p.setBrush(Qt.GlobalColor.white)
        p.drawRoundedRect(0, 3, w, h - 6, 4, 4)
        for b in self.bars:
            x = int(b.start / 24 * w)
            width = max(2, int((b.end - b.start) / 24 * w))
            color = COLORS["accent"] if b.synced else "#A6A6A6"
            p.setBrush(color)
            p.drawRoundedRect(x, 3, width, h - 6, 2, 2)
        p.end()


class BoardPage(QWidget):
    """首页：看板大屏，只看红绿灯。有服务器显示全部电脑，没有就只显示本机。"""

    COLUMNS = 6

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(14)
        self.counts = QLabel("")
        self.counts.setStyleSheet("font-size: 22px; font-weight: 600;")
        lay.addWidget(self.counts)
        self.hint = QLabel("")
        self.hint.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(self.hint)
        self.grid_box = QWidget()
        self.grid = QGridLayout(self.grid_box)
        self.grid.setSpacing(12)
        lay.addWidget(self.grid_box)
        lay.addStretch(1)

    def show_board(self, board: Board) -> None:
        c = board.counts()
        if board.source == "server":
            self.counts.setText(
                f"正常 {c['green']}    离线本地录制 {c['yellow']}    需要处理 {c['red']}")
        else:
            self.counts.setText(f"本机：{LIGHT_TEXT[board.tiles[0].light]}")
        self.hint.setText(board.hint or (
            "数据来自服务器" if board.source == "server" else "未连接服务器，只显示本机"))
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # 红灯排前面，一眼看到要处理的
        order = {"red": 0, "yellow": 1, "green": 2}
        for i, t in enumerate(sorted(board.tiles, key=lambda t: (order[t.light], t.code))):
            tile = QLabel(f"●\n{t.code}\n{LIGHT_TEXT[t.light]}")
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setToolTip(t.note or LIGHT_TEXT[t.light])
            tile.setMinimumHeight(110)
            tile.setStyleSheet(
                f"border: 1px solid {COLORS['line']}; border-radius: 8px; font-size: 13px; "
                f"color: {LIGHT_COLORS[t.light]}; font-weight: 600;"
            )
            self.grid.addWidget(tile, i // self.COLUMNS, i % self.COLUMNS)


class ClientWindow(QWidget):
    def __init__(self, install_dir: Path):
        super().__init__()
        self.install_dir = install_dir
        self.state_dir = install_dir / "state"
        self.config_path = install_dir / "config" / "everrec.yaml"
        self._level = "all"
        self.setWindowTitle("EverRec 录像客户端")
        self.resize(960, 760)
        self.setStyleSheet(f"background: {COLORS['bg']}; color: {COLORS['text']}; font-size: 14px;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 0, 0, 0)
        self.menu_btn = QPushButton("EverRec")
        self.menu_btn.clicked.connect(self._toggle_settings)
        bar.addWidget(self.menu_btn)
        for label, idx in (("看板", 0), ("本机详情", 1)):
            nav = QPushButton(label)
            nav.clicked.connect(lambda _c=False, i=idx: self._show_page(i))
            bar.addWidget(nav)
        self.code_label = QLabel("")
        self.code_label.setStyleSheet("font-family: Consolas, monospace; color: #666;")
        bar.addWidget(self.code_label)
        bar.addStretch(1)
        close = QPushButton("✕")
        close.setFixedSize(46, 40)
        close.setStyleSheet("border: none; background: transparent; color: #666;")
        close.clicked.connect(self.hide)
        bar.addWidget(close)
        bar_widget = QWidget()
        bar_widget.setLayout(bar)
        bar_widget.setStyleSheet(f"background: {COLORS['bar']}; border-bottom: 1px solid {COLORS['line']};")
        root.addWidget(bar_widget)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.board_page = BoardPage()
        self._board_result: Board | None = None
        self._board_busy = False
        self.stack.addWidget(self.board_page)
        self.stack.addWidget(self._build_main())
        self.stack.addWidget(self._build_settings())

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.board_timer = QTimer(self)
        self.board_timer.timeout.connect(self._refresh_board)
        self.board_timer.start(10000)
        self.refresh()
        self._refresh_board()

    # ---------- 主界面 ----------

    def _build_main(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(12)

        # 横幅
        self.banner = QLabel("正在录像")
        self.banner.setStyleSheet(
            f"padding: 14px 18px; border: 1px solid #BFBFBF; border-radius: 6px; "
            f"background: {COLORS['chip']}; color: {COLORS['accent']}; font-size: 17px; font-weight: 600;"
        )
        lay.addWidget(self.banner)

        # 画面 + 检查
        mid = QHBoxLayout()
        self.thumb = QLabel("本机画面")
        self.thumb.setFixedSize(352, 198)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(
            f"background: {COLORS['thumb']}; color: {COLORS['note']}; border-radius: 6px;"
        )
        mid.addWidget(self.thumb)

        right = QVBoxLayout()
        self.checks_box = QWidget()
        self.checks_box.setStyleSheet(f"border: 1px solid {COLORS['line']}; border-radius: 6px;")
        self.checks_layout = QVBoxLayout(self.checks_box)
        right.addWidget(self.checks_box)

        info = QGridLayout()
        info_box = QWidget()
        info_box.setLayout(info)
        info_box.setStyleSheet(f"border: 1px solid {COLORS['line']}; border-radius: 6px;")
        self.server_val = QLabel("—")
        self.pending_val = QLabel("—")
        self.disk_val = QLabel("—")
        for r, (k, v) in enumerate((("服务器", self.server_val), ("待上传", self.pending_val),
                                    ("本地空间", self.disk_val))):
            kk = QLabel(k)
            kk.setStyleSheet(f"color: {COLORS['muted']};")
            info.addWidget(kk, r, 0)
            info.addWidget(v, r, 1)
        right.addWidget(info_box)
        mid.addLayout(right, 1)
        lay.addLayout(mid)

        # 时间轴
        tl_head = QHBoxLayout()
        t1 = QLabel("今天的录像")
        t1.setStyleSheet("font-weight: 600;")
        legend = QLabel("■ 已上传    ■ 仅在本机")
        legend.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        tl_head.addWidget(t1)
        tl_head.addStretch(1)
        tl_head.addWidget(legend)
        lay.addLayout(tl_head)
        self.timeline = TimelineWidget()
        lay.addWidget(self.timeline)
        self.tl_axis = QLabel("00:00    06:00    12:00    18:00    24:00")
        self.tl_axis.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 11px; font-family: Consolas, monospace;"
        )
        lay.addWidget(self.tl_axis)

        # 日志
        log_head = QHBoxLayout()
        lt = QLabel("日志")
        lt.setStyleSheet("font-weight: 600;")
        log_head.addWidget(lt)
        self.level_buttons: dict[str, QPushButton] = {}
        for lv, label in (("all", "全部"), ("warn", "警告及以上"), ("err", "错误")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _checked=False, level=lv: self._set_level(level))
            self.level_buttons[lv] = b
            log_head.addWidget(b)
        log_head.addStretch(1)
        open_logs = QPushButton("打开日志文件夹")
        open_logs.clicked.connect(lambda: os.startfile(str(self.install_dir / "logs")))
        open_logs.setStyleSheet("border: none; color: #666;")
        log_head.addWidget(open_logs)
        lay.addLayout(log_head)
        self.logs = QListWidget()
        self.logs.setStyleSheet(f"border: 1px solid {COLORS['line']}; border-radius: 6px; font-size: 13px;")
        lay.addWidget(self.logs, 1)

        # 底部
        foot = QHBoxLayout()
        folder_label = QLabel("录像文件夹")
        folder_label.setStyleSheet(f"color: {COLORS['muted']};")
        self.folder_val = QLabel("—")
        self.folder_val.setStyleSheet("font-family: Consolas, monospace;")
        foot.addWidget(folder_label)
        foot.addWidget(self.folder_val)
        foot.addStretch(1)
        open_dir = QPushButton("打开录像文件夹")
        open_dir.clicked.connect(self._open_recordings)
        repair_btn = QPushButton("修复工具")
        repair_btn.clicked.connect(lambda: subprocess.Popen(
            ["everrec-repair"]))
        foot.addWidget(open_dir)
        foot.addWidget(repair_btn)
        lay.addLayout(foot)
        return w

    # ---------- 设置 ----------

    def _build_settings(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(22, 14, 22, 14)
        lay.setSpacing(12)

        back = QHBoxLayout()
        b = QPushButton("← 返回")
        b.clicked.connect(self._toggle_settings)
        back.addWidget(b)
        back.addWidget(QLabel("设置"))
        back.addStretch(1)
        lay.addLayout(back)

        self.settings_box = QWidget()
        self.settings_grid = QGridLayout(self.settings_box)
        self.settings_grid.setHorizontalSpacing(14)
        self.settings_grid.setVerticalSpacing(10)
        self.settings_box.setStyleSheet(f"border: 1px solid {COLORS['line']}; border-radius: 6px;")
        lay.addWidget(self.settings_box)

        form = QGridLayout()
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("留空 = 不连服务器，只看本机")
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("看板读服务器用的只读令牌（可留空）")
        form.addWidget(QLabel("服务器地址"), 0, 0)
        form.addWidget(self.server_edit, 0, 1)
        form.addWidget(QLabel("访问令牌"), 1, 0)
        form.addWidget(self.token_edit, 1, 1)
        lay.addLayout(form)
        save = QPushButton("保存")
        save.clicked.connect(self._save_settings)
        lay.addWidget(save)
        self.save_msg = QLabel("")
        self.save_msg.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(self.save_msg)

        open_cfg = QPushButton("用记事本打开配置文件")
        open_cfg.clicked.connect(self._open_config)
        lay.addWidget(open_cfg)

        lay.addWidget(QLabel("也可以直接改配置文件；改动在录像下一段开始时生效。"))
        return w

    # ---------- 逻辑 ----------

    def _set_level(self, level: str) -> None:
        self._level = level
        for lv, b in self.level_buttons.items():
            b.setChecked(lv == level)
            b.setStyleSheet(
                f"border: 1px solid {COLORS['accent'] if lv == level else COLORS['line']}; "
                f"background: {COLORS['accent'] if lv == level else COLORS['bg']}; "
                f"color: {'#fff' if lv == level else COLORS['text']}; border-radius: 6px; padding: 4px 10px;"
            )
        self.refresh()

    def _show_page(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.menu_btn.setText("返回" if idx == 2 else "EverRec")
        if idx == 2:
            self._render_settings()

    def _toggle_settings(self) -> None:
        self._show_page(0 if self.stack.currentIndex() == 2 else 2)

    def _save_settings(self) -> None:
        """直接写配置文件（不需要管理员登录；文件没有写权限时给出人话提示）。"""
        try:
            cfg = load_config(self.config_path)
            data = cfg.model_dump(
                mode="json", exclude={"install_dir", "config_warnings", "config_path"})
            data["server"] = self.server_edit.text().strip()
            data["api_token"] = self.token_edit.text().strip()
            new_cfg, _warnings = validate_tolerant(data)
            save_config(new_cfg, self.config_path)
        except OSError:
            self.save_msg.setText("保存失败：当前账号没有改这个配置文件的权限，可以用管理员身份直接改文件")
            return
        self.save_msg.setText("已保存")
        self._refresh_board()

    def _refresh_board(self) -> None:
        """看板数据在后台线程取，服务器慢或不通都不卡界面。"""
        if self._board_busy:
            return
        cfg = load_config(self.config_path) if self.config_path.exists() else None
        server, token = (cfg.server, cfg.api_token) if cfg else ("", "")
        self._board_busy = True

        def work() -> None:
            try:
                self._board_result = build_board(server, token, self.state_dir)
            finally:
                self._board_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _render_settings(self) -> None:
        cfg = load_config(self.config_path) if self.config_path.exists() else None
        while self.settings_grid.count():
            item = self.settings_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = [
            ("位置编号", cfg.code if cfg else "—"),
            ("画质", cfg.recording.encoding.label if cfg else "—"),
            ("录像文件夹", str(cfg.recordings_dir if cfg else "—")),
            ("本地空间上限", f"{cfg.recording.local_quota_gb:g} GB" if cfg else "—"),
            ("开机自动启动", "开启（系统服务）" if cfg and cfg.autostart else "关闭"),
        ]
        for r, (k, v) in enumerate(rows):
            kk = QLabel(k)
            kk.setStyleSheet(f"color: {COLORS['muted']};")
            vv = QLabel(v)
            vv.setStyleSheet("font-family: Consolas, monospace;")
            self.settings_grid.addWidget(kk, r, 0)
            self.settings_grid.addWidget(vv, r, 1)
        if cfg:
            self.server_edit.setText(cfg.server)
            self.token_edit.setText(cfg.api_token)
        self.save_msg.setText("")

    def refresh(self) -> None:
        if self._board_result is not None:
            self.board_page.show_board(self._board_result)
            self._board_result = None
        st = read_status(self.state_dir)
        self.code_label.setText(st.get("code", ""))

        recording = bool(st.get("recording"))
        offline = st.get("offline_since") is not None and st.get("online") is False
        if st.get("stale") or not st:
            banner = "录制服务无响应"
        elif not recording:
            banner = "没在录像"
        elif offline:
            banner = "正在录像 · 离线本地录制"
        else:
            banner = "正在录像"
        self.banner.setText(banner)

        # 缩略图
        thumb = self.state_dir / "thumb.jpg"
        if thumb.exists():
            self.thumb.setPixmap(QPixmap(str(thumb)).scaled(
                352, 198, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("本机画面")

        # 检查
        while self.checks_layout.count():
            item = self.checks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        checks = st.get("checks", {})
        for key, label in CHECK_LABEL.items():
            val = checks.get(key, "ok")
            mark = "✓" if val == "ok" else ("!" if val == "warn" else "✕")
            row = QLabel(f"{mark}  {label}")
            row.setStyleSheet(
                f"color: {COLORS['text'] if val == 'ok' else COLORS['strong']}; font-size: 13px;"
            )
            self.checks_layout.addWidget(row)

        online = st.get("online")
        pending = int(st.get("pending_sync", 0))
        self.server_val.setText("未连接" if online is False else ("已连接" if online else "—"))
        self.pending_val.setText(f"{pending} 段")
        self.disk_val.setText(f"已用 {int(float(st.get('disk_used', 0)) * 100)}%")

        self.timeline.set_bars(build_timeline(self.state_dir))

        self._refresh_logs()

        folder = self.install_dir / "recordings"
        self.folder_val.setText(str(folder))

    def _refresh_logs(self) -> None:
        self.logs.clear()
        for log in read_logs(self.install_dir / "logs"):
            level = log["level"]
            if self._level == "warn" and level == "INFO":
                continue
            if self._level == "err" and level != "ERROR":
                continue
            item = QListWidgetItem(f"{log['time']}  {friendly_level(level)}  {log['msg']}")
            item.setForeground(Qt.GlobalColor.black)
            self.logs.addItem(item)

    def _open_recordings(self) -> None:
        d = self.install_dir / "recordings"
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))

    def _open_config(self) -> None:
        if self.config_path.exists():
            os.startfile(str(self.config_path))

def main() -> int:
    ap = argparse.ArgumentParser(prog="everrec-client")
    ap.add_argument("--install-dir", help="安装目录，默认 D:\\EverRec")
    args = ap.parse_args()
    app = QApplication(sys.argv)
    w = ClientWindow(_install_dir(args))
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
