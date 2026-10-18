"""录像客户端（设计 4.1、4.7）：主界面 + 设置。只读 status.json 和日志，不直接控制录制。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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

from everrec_core.config import load_config
from everrec_core.profiles import quality_table

from .common import (
    COLORS,
    SETTING_KEYS,
    apply_settings,
    build_timeline,
    friendly_level,
    needs_setup,
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
        self.stack.addWidget(self._build_main())
        self.stack.addWidget(self._build_settings())

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()
        if needs_setup(self.config_path):  # 还没配置：默认先打开设置界面
            self._toggle_settings()

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

        form_box = QWidget()
        form = QGridLayout(form_box)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form_box.setStyleSheet(f"border: 1px solid {COLORS['line']}; border-radius: 6px;")
        self.loc_edits: dict[str, QLineEdit] = {}
        r = 0
        for key, label in zip(SETTING_KEYS, ("州", "区", "楼栋", "楼层", "编号"), strict=True):
            self.loc_edits[key] = QLineEdit()
            self.loc_edits[key].setPlaceholderText("只能用英文和数字")
            form.addWidget(QLabel(f"位置 · {label}"), r, 0)
            form.addWidget(self.loc_edits[key], r, 1)
            r += 1
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("留空 = 不连服务器，本机独立录像")
        form.addWidget(QLabel("服务器地址"), r, 0)
        form.addWidget(self.server_edit, r, 1)
        r += 1
        self.quality_box = QComboBox()
        for row in quality_table():
            star = "（推荐）" if row["recommended"] else ""
            self.quality_box.addItem(f"{row['label']}{star}", row["quality"])
        form.addWidget(QLabel("画质"), r, 0)
        form.addWidget(self.quality_box, r, 1)
        r += 1
        self.folder_edit = QLineEdit()
        form.addWidget(QLabel("录像文件夹"), r, 0)
        form.addWidget(self.folder_edit, r, 1)
        r += 1
        self.autostart_box = QCheckBox("开机自动启动")
        form.addWidget(self.autostart_box, r, 1)
        lay.addWidget(form_box)

        save = QPushButton("保存")
        save.clicked.connect(self._save_settings)
        lay.addWidget(save)
        self.save_msg = QLabel("")
        self.save_msg.setWordWrap(True)
        self.save_msg.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(self.save_msg)

        open_cfg = QPushButton("用记事本打开配置文件")
        open_cfg.clicked.connect(self._open_config)
        lay.addWidget(open_cfg)

        lay.addWidget(QLabel("也可以直接改配置文件；改动在录像下一段开始时生效。"))
        lay.addStretch(1)
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

    def _toggle_settings(self) -> None:
        idx = 1 if self.stack.currentIndex() == 0 else 0
        self.stack.setCurrentIndex(idx)
        self.menu_btn.setText("EverRec" if idx == 0 else "返回")
        if idx == 1:
            self._render_settings()

    def _render_settings(self) -> None:
        cfg = load_config(self.config_path) if self.config_path.exists() else None
        for key in SETTING_KEYS:
            self.loc_edits[key].setText(getattr(cfg.location, key) if cfg else "")
        self.server_edit.setText(cfg.server if cfg else "")
        idx = self.quality_box.findData(cfg.recording.quality.value if cfg else "standard")
        self.quality_box.setCurrentIndex(max(idx, 0))
        self.folder_edit.setText(cfg.recording.folder if cfg else "recordings")
        self.autostart_box.setChecked(cfg.autostart if cfg else True)
        self.save_msg.setText("还没有配置：请先填写位置编号并保存。" if needs_setup(self.config_path) else "")

    def _save_settings(self) -> None:
        """直接写配置文件，不需要管理员登录；没有写入权限时给出提示。"""
        values = {k: e.text() for k, e in self.loc_edits.items()}
        values.update(server=self.server_edit.text(), quality=self.quality_box.currentData(),
                      folder=self.folder_edit.text(), autostart=self.autostart_box.isChecked())
        try:
            warnings = apply_settings(self.config_path, values)
        except OSError:
            self.save_msg.setText("保存失败：当前账号没有改这个配置文件的权限，请换有权限的账号，或直接改文件。")
            return
        self.save_msg.setText("已保存。" + ("".join(f" 注意：{w}" for w in warnings)))

    def refresh(self) -> None:
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
