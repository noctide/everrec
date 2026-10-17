"""修复工具（设计 9.6）：拖入文件/文件夹 → 检查并修复 → 看结果。

原文件不动，修复结果存到原文件旁的「已修复」文件夹。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from everrec_core import repair

from .common import COLORS, collect_files

# integrity → (图标, 图标底色, 文字色)
RESULT_STYLE = {
    "ok": ("✓", COLORS["chip"], COLORS["muted"]),
    "repaired": ("✓", COLORS["chip"], COLORS["text"]),
    "partial": ("!", COLORS["chip"], COLORS["accent"]),
    "broken": ("✕", COLORS["chip_alert"], COLORS["strong"]),
}


class RepairWorker(QObject):
    done_one = Signal(str, str, str)  # 文件名, integrity, 给人看的文字
    finished = Signal()

    def __init__(self, files: list[Path]):
        super().__init__()
        self.files = files
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        for f in self.files:
            if self._cancel.is_set():
                break
            out_dir = f.parent / "已修复"
            try:
                res = repair.repair_file(f, out_dir)
                integrity, text = res.integrity.value, res.friendly
            except Exception as e:  # noqa: BLE001  单个文件失败不中断整批
                integrity, text = "broken", f"无法修复（{e}）"
            self.done_one.emit(f.name, integrity, text)
        self.finished.emit()


class DropZone(QWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"border: 2px dashed {COLORS['line']}; background: {COLORS['bar']}; border-radius: 6px;"
        )
        icon = QLabel("⬆")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("border: none; font-size: 32px; color: #545454;")
        title = QLabel("把录像文件或整个文件夹拖到这里")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"border: none; font-size: 17px; font-weight: 600; color: {COLORS['text']};")
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pick_file = QPushButton("选择文件")
        pick_dir = QPushButton("选择文件夹")
        for b in (pick_file, pick_dir):
            b.setStyleSheet(
                f"border: 1px solid {COLORS['accent']}; background: {COLORS['bg']}; "
                f"color: {COLORS['accent']}; border-radius: 6px; padding: 10px 18px;"
            )
        pick_file.clicked.connect(self._pick_files)
        pick_dir.clicked.connect(self._pick_dir)
        row.addWidget(pick_file)
        row.addWidget(pick_dir)
        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addLayout(row)

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择录像文件")
        if files:
            self.files_dropped.emit([Path(f) for f in files])

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.files_dropped.emit([Path(d)])

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls()]
        self.files_dropped.emit(paths)


class RepairTool(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EverRec 视频修复工具")
        self.resize(960, 640)
        self._files: list[Path] = []
        self._thread: threading.Thread | None = None
        self._worker: RepairWorker | None = None
        self._out_dir: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 0, 0, 0)
        title = QLabel("EverRec 视频修复工具")
        title.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600; font-size: 14px;")
        bar.addWidget(title)
        bar.addStretch(1)
        close = QPushButton("✕")
        close.setFixedSize(46, 40)
        close.setStyleSheet("border: none; background: transparent; color: #666;")
        close.clicked.connect(self.close)
        bar.addWidget(close)
        bar_widget = QWidget()
        bar_widget.setLayout(bar)
        bar_widget.setStyleSheet(f"background: {COLORS['bar']}; border-bottom: 1px solid {COLORS['line']};")
        root.addWidget(bar_widget)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # 第 0 页：放入 + 队列
        drop_page = QWidget()
        dl = QVBoxLayout(drop_page)
        dl.setContentsMargins(28, 24, 28, 24)
        dl.setSpacing(14)
        self.drop = DropZone()
        self.drop.files_dropped.connect(self._add_files)
        dl.addWidget(self.drop)

        self.queue_label = QLabel("还没有放入文件")
        self.queue_label.setStyleSheet(f"color: {COLORS['muted']};")
        self.queue = QListWidget()
        self.queue.setStyleSheet(
            f"border: 1px solid {COLORS['line']}; border-radius: 6px; font-family: Consolas, monospace;"
        )
        dl.addWidget(self.queue_label)
        dl.addWidget(self.queue, 1)

        run_btn = QPushButton("检查并修复")
        run_btn.setStyleSheet(
            f"background: {COLORS['accent']}; color: #fff; font-size: 17px; font-weight: 600; "
            "border: none; border-radius: 6px; padding: 14px 36px;"
        )
        run_btn.clicked.connect(self._run)
        dl.addWidget(run_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.stack.addWidget(drop_page)

        # 第 1 页：结果
        done_page = QWidget()
        dl2 = QVBoxLayout(done_page)
        dl2.setContentsMargins(28, 24, 28, 24)
        dl2.setSpacing(14)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        dl2.addWidget(self.progress)
        self.results = QListWidget()
        self.results.setStyleSheet(f"border: 1px solid {COLORS['line']}; border-radius: 6px;")
        dl2.addWidget(self.results, 1)

        self.adv_check = QPushButton("▸ 高级（技术人员查看日志）")
        self.adv_check.setCheckable(True)
        self.adv_check.setStyleSheet(
            f"border: none; background: transparent; color: {COLORS['muted']}; text-align: left;"
        )
        self.adv_log = QTextEdit()
        self.adv_log.setReadOnly(True)
        self.adv_log.hide()
        self.adv_check.toggled.connect(lambda on: self.adv_log.setVisible(on))
        dl2.addWidget(self.adv_check)
        dl2.addWidget(self.adv_log)

        footer = QHBoxLayout()
        reset_btn = QPushButton("再处理一批")
        reset_btn.setStyleSheet(
            f"border: 1px solid {COLORS['line']}; border-radius: 6px; padding: 10px 16px;"
        )
        reset_btn.clicked.connect(self._reset)
        open_btn = QPushButton("打开文件夹")
        open_btn.setStyleSheet(
            f"background: {COLORS['accent']}; color: #fff; border: none; "
            "border-radius: 6px; padding: 10px 18px;"
        )
        open_btn.clicked.connect(self._open_out_dir)
        footer.addStretch(1)
        footer.addWidget(reset_btn)
        footer.addWidget(open_btn)
        dl2.addLayout(footer)
        self.stack.addWidget(done_page)

        self.setStyleSheet(f"background: {COLORS['bg']}; color: {COLORS['text']}; font-size: 15px;")

    def _add_files(self, paths: list[Path]) -> None:
        files = collect_files(paths)
        for f in files:
            if f not in self._files:
                self._files.append(f)
                self.queue.addItem(f"{f.name}    {f.stat().st_size / 1024 / 1024:.1f} MB")
        self.queue_label.setText(f"已放入 {len(self._files)} 个文件")

    def _run(self) -> None:
        if not self._files or (self._thread and self._thread.is_alive()):
            return
        self._out_dir = self._files[0].parent / "已修复"
        self.results.clear()
        self.adv_log.clear()
        self.stack.setCurrentIndex(1)
        self.progress.show()
        self._worker = RepairWorker(list(self._files))
        self._worker.done_one.connect(self._add_result)
        self._worker.finished.connect(self._on_finished)
        self._thread = threading.Thread(target=self._worker.run, daemon=True)
        self._thread.start()

    def _add_result(self, name: str, integrity: str, text: str) -> None:
        icon, bg, fg = RESULT_STYLE.get(integrity, RESULT_STYLE["broken"])
        item = QListWidgetItem(f"{icon}   {name}\n     {text}")
        item.setForeground(Qt.GlobalColor.black)
        self.results.addItem(item)
        self.adv_log.append(f"{name} → {text}")

    def _on_finished(self) -> None:
        self.progress.hide()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)

    def _open_out_dir(self) -> None:
        import os
        if self._out_dir and self._out_dir.exists():
            os.startfile(str(self._out_dir))  # noqa: S606  打开资源管理器

    def _reset(self) -> None:
        self._files.clear()
        self.queue.clear()
        self.queue_label.setText("还没有放入文件")
        self.stack.setCurrentIndex(0)


def main() -> int:
    app = QApplication(sys.argv)
    w = RepairTool()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
