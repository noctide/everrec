"""修复工具（设计 9.6）：拖入文件/文件夹 → 检查并修复 → 看结果。

原文件不动，修复结果存到原文件旁的「已修复」文件夹。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from everrec_core import repair

from . import theme
from .common import COLORS, LEVEL_COLORS, collect_files, open_path

# integrity → (图标, 状态色)
RESULT_STYLE = {
    "ok": ("✓", "ok"),
    "repaired": ("✓", "info"),
    "partial": ("!", "warn"),
    "broken": ("✕", "error"),
}

EXTRA_STYLE = f"""
QFrame#drop {{ background: {COLORS['bar']}; border: 2px dashed {COLORS['line2']}; border-radius: 6px; }}
QFrame#drop[active="true"] {{ background: {LEVEL_COLORS['info']['bg']};
    border: 2px dashed {LEVEL_COLORS['info']['fg']}; }}
QLabel#dropIcon {{ font-size: 34px; color: {COLORS['accent']}; }}
"""


class RepairWorker(QObject):
    done_one = Signal(str, str, str)  # 文件名, integrity, 给人看的文字
    finished = Signal()

    def __init__(self, files: list[Path]):
        super().__init__()
        self.files = files

    def run(self) -> None:
        for f in self.files:
            out_dir = f.parent / "已修复"
            try:
                res = repair.repair_file(f, out_dir)
                integrity, text = res.integrity.value, res.friendly
            except Exception as e:  # noqa: BLE001  单个文件失败不中断整批
                integrity, text = "broken", f"无法修复（{e}）"
            self.done_one.emit(f.name, integrity, text)
        self.finished.emit()


class DropZone(QFrame):
    """虚线框拖放区；拖到上面时高亮。"""

    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(20, 22, 20, 22)
        lay.setSpacing(8)
        icon = QLabel("⇪")
        icon.setObjectName("dropIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = theme.label("把录像文件或整个文件夹拖到这里", "h1")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = theme.label("支持 ts / mp4 / mkv 等常见格式；原文件不会被改动，修复结果另存", "muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(theme.button("选择文件", on_click=self._pick_files))
        row.addWidget(theme.button("选择文件夹", on_click=self._pick_dir))
        for w in (icon, title, hint):
            lay.addWidget(w)
        lay.addLayout(row)

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择录像文件")
        if files:
            self.files_dropped.emit([Path(f) for f in files])

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.files_dropped.emit([Path(d)])

    def _set_active(self, on: bool) -> None:
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            self._set_active(True)
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e) -> None:  # noqa: N802
        self._set_active(False)

    def dropEvent(self, e) -> None:  # noqa: N802
        self._set_active(False)
        self.files_dropped.emit([Path(u.toLocalFile()) for u in e.mimeData().urls()])


class RepairTool(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("EverRec 视频修复工具")
        self.resize(960, 660)
        self.setStyleSheet(EXTRA_STYLE)
        self._files: list[Path] = []
        self._thread: threading.Thread | None = None
        self._worker: RepairWorker | None = None
        self._out_dir: Path | None = None
        self._done = 0
        self._counts: dict[str, int] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("header")
        bar.setFixedHeight(52)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 0, 20, 0)
        bl.addWidget(theme.label("● EverRec", "brand"))
        bl.addWidget(theme.label("视频修复工具", "muted"))
        bl.addStretch(1)
        self.step = theme.label("第 1 步：放入文件", "pill")
        theme.set_level(self.step, "idle")
        bl.addWidget(self.step, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(bar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # 第 0 页：放入 + 队列
        drop_page = QWidget()
        drop_page.setObjectName("page")
        dl = QVBoxLayout(drop_page)
        dl.setContentsMargins(28, 22, 28, 22)
        dl.setSpacing(14)
        self.drop = DropZone()
        self.drop.files_dropped.connect(self._add_files)
        dl.addWidget(self.drop)

        head = QHBoxLayout()
        self.queue_label = theme.label("还没有放入文件", "h2")
        head.addWidget(self.queue_label)
        head.addStretch(1)
        self.clear_btn = theme.button("清空", "link", self._clear_queue)
        self.clear_btn.setEnabled(False)
        head.addWidget(self.clear_btn)
        dl.addLayout(head)
        self.queue = QListWidget()
        dl.addWidget(self.queue, 1)

        self.run_btn = theme.button("检查并修复", "primary", self._run)
        self.run_btn.setMinimumSize(180, 44)
        self.run_btn.setEnabled(False)
        dl.addWidget(self.run_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.stack.addWidget(drop_page)

        # 第 1 页：结果
        done_page = QWidget()
        done_page.setObjectName("page")
        dl2 = QVBoxLayout(done_page)
        dl2.setContentsMargins(28, 22, 28, 22)
        dl2.setSpacing(14)
        self.summary = theme.label("正在检查…", "h1")
        dl2.addWidget(self.summary)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        dl2.addWidget(self.progress)
        self.results = QListWidget()
        dl2.addWidget(self.results, 1)

        self.adv_check = theme.button("▸ 高级（技术人员查看日志）", "link")
        self.adv_check.setCheckable(True)
        self.adv_log = QTextEdit()
        self.adv_log.setReadOnly(True)
        self.adv_log.setMaximumHeight(140)
        self.adv_log.hide()
        self.adv_check.toggled.connect(self._toggle_adv)
        dl2.addWidget(self.adv_check, 0, Qt.AlignmentFlag.AlignLeft)
        dl2.addWidget(self.adv_log)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.reset_btn = theme.button("再处理一批", on_click=self._reset)
        self.open_btn = theme.button("打开已修复文件夹", "primary", self._open_out_dir)
        self.open_btn.setMinimumHeight(40)
        footer.addWidget(self.reset_btn)
        footer.addWidget(self.open_btn)
        dl2.addLayout(footer)
        self.stack.addWidget(done_page)

    def _toggle_adv(self, on: bool) -> None:
        self.adv_log.setVisible(on)
        self.adv_check.setText(("▾" if on else "▸") + " 高级（技术人员查看日志）")

    def _clear_queue(self) -> None:
        self._files.clear()
        self.queue.clear()
        self._sync_queue_state()

    def _sync_queue_state(self) -> None:
        n = len(self._files)
        self.queue_label.setText(f"已放入 {n} 个文件" if n else "还没有放入文件")
        self.run_btn.setEnabled(n > 0)
        self.clear_btn.setEnabled(n > 0)
        self.run_btn.setText(f"检查并修复（{n} 个）" if n else "检查并修复")

    def _add_files(self, paths: list[Path]) -> None:
        for f in collect_files(paths):
            if f not in self._files:
                self._files.append(f)
                self.queue.addItem(f"{f.name}    {f.stat().st_size / 1024 / 1024:.1f} MB")
        self._sync_queue_state()

    def _run(self) -> None:
        if not self._files or (self._thread and self._thread.is_alive()):
            return
        self._out_dir = self._files[0].parent / "已修复"
        self.results.clear()
        self.adv_log.clear()
        self._done, self._counts = 0, {}
        self.progress.setRange(0, len(self._files))
        self.progress.setValue(0)
        self.summary.setText(f"正在检查，共 {len(self._files)} 个文件…")
        self.reset_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.step.setText("第 2 步：检查修复中")
        theme.set_level(self.step, "info")
        self.stack.setCurrentIndex(1)
        self._worker = RepairWorker(list(self._files))
        self._worker.done_one.connect(self._add_result)
        self._worker.finished.connect(self._on_finished)
        self._thread = threading.Thread(target=self._worker.run, daemon=True)
        self._thread.start()

    def _add_result(self, name: str, integrity: str, text: str) -> None:
        icon, level = RESULT_STYLE.get(integrity, RESULT_STYLE["broken"])
        item = QListWidgetItem(f"{icon}   {name}\n      {text}")
        item.setForeground(QColor(LEVEL_COLORS[level]["fg"]))
        if level in ("warn", "error"):
            item.setBackground(QColor(LEVEL_COLORS[level]["bg"]))
        self.results.addItem(item)
        self.adv_log.append(f"{name} → {text}")
        self._done += 1
        self.progress.setValue(self._done)
        self._counts[level] = self._counts.get(level, 0) + 1

    def _on_finished(self) -> None:
        bad = self._counts.get("error", 0) + self._counts.get("warn", 0)
        self.summary.setText(f"完成：共 {self._done} 个文件，其中 {bad} 个需要留意" if bad
                             else f"完成：{self._done} 个文件都没有问题")
        self.step.setText("第 3 步：看结果")
        theme.set_level(self.step, "error" if self._counts.get("error") else "warn" if bad else "ok")
        self.reset_btn.setEnabled(True)
        self.open_btn.setEnabled(True)

    def _open_out_dir(self) -> None:
        if self._out_dir and self._out_dir.exists():
            open_path(self._out_dir)

    def _reset(self) -> None:
        self._clear_queue()
        self._counts = {}
        self.step.setText("第 1 步：放入文件")
        theme.set_level(self.step, "idle")
        self.stack.setCurrentIndex(0)
