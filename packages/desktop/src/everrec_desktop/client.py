"""录像客户端（设计 4.1、4.7）：主界面 + 设置。只读 status.json 和日志，不直接控制录制。"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from everrec_core.config import load_config
from everrec_core.profiles import quality_table

from . import theme
from .common import (
    CHECK_LABEL,
    COLORS,
    LEVEL_COLORS,
    SNAPSHOT_DIR,
    apply_settings,
    build_timeline,
    ensure_location_file,
    friendly_level,
    load_location_levels,
    needs_setup,
    open_path,
    open_url,
    overall_state,
    read_logs,
    read_status,
    recording_duration,
)
from .snapshot import save_screen, save_thumbnail

THUMB_W, THUMB_H = 352, 198


def _fit_to_screen(width: int, height: int, minimum: QSize) -> tuple[int, int]:
    """窗口开多大：不超过屏幕可用区域的 92%，也不小于最小尺寸。"""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return width, height
    avail = screen.availableGeometry()
    w = min(width, max(minimum.width(), int(avail.width() * 0.92)))
    h = min(height, max(minimum.height(), int(avail.height() * 0.92)))
    return w, h

# 定时截屏的间隔挡位（秒）。只给挡位，不让人手填秒数；与 core 的 Screenshot 范围一致。
SCREENSHOT_INTERVAL_STOPS = (60, 120, 300, 600, 900, 1800)


def _hline() -> QFrame:
    f = QFrame()
    f.setObjectName("sep")
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


class ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class ThumbLabel(ClickableLabel):
    """本机画面：宽高固定 16:9，窗口怎么拉都只是整体变大变小，不变形。

    宽度由布局决定，高度按 16:9 反算并锁死（setFixedHeight）：
    这样只有一格比例固定的视频框，不会出现"宽扁的框里居中一条画面"。
    """

    RATIO = 9 / 16
    # 440*9/16≈248，等于右栏自然高度；再窄视频框就比右栏矮，下面会空一截
    MIN_W, MAX_W = 440, 560

    def __init__(self) -> None:
        super().__init__()
        self._source = QPixmap()
        self.setMinimumWidth(self.MIN_W)
        self.setMaximumWidth(self.MAX_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("暂无画面")
        self.setFixedHeight(int(self.MIN_W * self.RATIO))

    def set_source(self, pix: QPixmap) -> None:
        self._source = pix
        self.setText("" if pix.isNull() else "暂无画面")
        self._apply()

    def resizeEvent(self, e) -> None:  # noqa: N802
        h = max(1, int(self.width() * self.RATIO))
        if self.height() != h:
            self.setFixedHeight(h)  # 高度跟着宽度走，比例永远 16:9
        super().resizeEvent(e)
        self._apply()

    def _apply(self) -> None:
        if self._source.isNull():
            self.clear()  # 先清掉 pixmap，否则"暂无画面"这几个字会被空图顶掉
            self.setText("暂无画面")
            return
        # 视频框本身已经是 16:9，画面完整显示（4:3 的源会留窄边，不裁切）
        self.setPixmap(self._source.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))


class ZoomDialog(QDialog):
    """放大看本机画面；每 2 秒重读一次，点一下或按 Esc 关闭，底部可保存当前画面。"""

    def __init__(self, state_dir: Path, code: str, dest_dir: Path,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.state_dir = state_dir
        self.code = code
        self.dest_dir = dest_dir
        self.path = state_dir / "thumb.jpg"
        self.setWindowTitle("本机画面")
        self.resize(960, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = ClickableLabel("暂无画面")
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setStyleSheet(f"background:{COLORS['thumb']};color:{COLORS['note']};")
        self.view.clicked.connect(self.accept)
        lay.addWidget(self.view, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(12, 8, 12, 8)
        foot.addStretch(1)
        self.save_btn = theme.button("保存当前画面", on_click=self._save)
        foot.addWidget(self.save_btn)
        lay.addLayout(foot)
        self._pix = QPixmap()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._reload)
        self.timer.start(2000)
        self._reload()

    def _save(self) -> None:
        """保存正看到的这一帧（录像画面）；按钮临时反馈，不弹窗、不关窗口。"""
        try:
            path = save_thumbnail(self.state_dir, self.dest_dir, self.code)
        except OSError as exc:
            self.save_btn.setText("保存失败")
            self.save_btn.setToolTip(str(exc))
        else:
            self.save_btn.setText("已保存 ✓")
            self.save_btn.setToolTip(str(path))
        QTimer.singleShot(2000, lambda: self.save_btn.setText("保存当前画面"))

    def _reload(self) -> None:
        self._pix = QPixmap(str(self.path)) if self.path.exists() else QPixmap()
        self._fit()

    def _fit(self) -> None:
        if self._pix.isNull():
            self.view.setPixmap(QPixmap())
            self.view.setText("暂无画面")
            return
        self.view.setPixmap(self._pix.scaled(
            self.view.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit()


class LevelField(QWidget):
    """一级位置：只能从下拉里选（选项来自配置文件）。当前值不在选项里也保留显示，标为待确认。"""

    changed = Signal()

    def __init__(self, level: dict):
        super().__init__()
        self.label = level["label"]
        self.has_options = bool(level["options"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(theme.label(self.label, "muted"))
        self.widget = QComboBox()
        self.widget.addItem("请选择" if self.has_options else "没有可选项", "")
        for o in level["options"]:
            self.widget.addItem(o["label"], o["value"])
        self.widget.currentIndexChanged.connect(lambda _i: self.changed.emit())
        lay.addWidget(self.widget)

    def value(self) -> str:
        return str(self.widget.currentData() or "")

    def set_value(self, value: str) -> None:
        idx = self.widget.findData(value)
        if idx < 0 and value:  # 本机的值不在选项里：保留，标出来
            self.widget.addItem(f"{value}（待确认）", value)
            idx = self.widget.count() - 1
        self.widget.setCurrentIndex(max(idx, 0))


class ClientWindow(QWidget):
    def __init__(self, install_dir: Path):
        super().__init__()
        self.install_dir = install_dir
        self.state_dir = install_dir / "state"
        self.config_path = install_dir / "config" / "everrec.yaml"
        self._level = "all"
        self._log_sig: object = None
        self._quitting = False
        self._tray = None
        self.setObjectName("root")
        self.setWindowTitle("EverRec 录像客户端")
        # 开窗尺寸受屏幕可用区域限制：高 DPI 缩放下（150%/200%）逻辑屏幕只有 1248x720 / 936x540，
        # 硬开 980x780 会比屏幕还高，看着"缩不下去"。
        # 700 是内容的最小宽度，再加 20 给窗口边框和滚动条，避免最小尺寸下反而挤出横向滚动条
        self.setMinimumSize(720, 480)
        self.resize(*_fit_to_screen(980, 780, self.minimumSize()))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._scroll(self._build_main()))
        self.stack.addWidget(self._scroll(self._build_settings()))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()
        if needs_setup(self.config_path):  # 还没配置：默认先打开设置界面
            self._toggle_settings()

    @staticmethod
    def _scroll(page: QWidget) -> QScrollArea:
        """页面套一层滚动条：窗口缩小时内容滚，不把检查项压扁。"""
        area = QScrollArea()
        area.setObjectName("scroll")
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidgetResizable(True)
        area.setWidget(page)
        return area

    # ---------- 顶栏 ----------

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("header")
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 16, 0)
        lay.setSpacing(14)
        lay.addWidget(theme.label("● EverRec", "brand"))
        self.code_label = theme.label("", "code")
        lay.addWidget(self.code_label)
        lay.addStretch(1)
        self.pill = theme.label("", "pill")  # 整体运行状态，一眼能看出好坏
        lay.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self.menu_btn = theme.button("设置", on_click=self._toggle_settings)
        lay.addWidget(self.menu_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    # ---------- 主界面 ----------

    def _build_main(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 18, 24, 16)
        lay.setSpacing(14)

        lay.addWidget(self._build_banner())

        mid = QHBoxLayout()
        mid.setSpacing(14)
        mid.addWidget(self._build_thumb(), 1)
        mid.addLayout(self._build_side(), 1)
        lay.addLayout(mid)

        lay.addWidget(self._build_timeline_card())
        lay.addWidget(self._build_log_card(), 1)

        foot = QHBoxLayout()
        foot.addWidget(theme.label("录像文件夹", "muted"))
        self.folder_val = theme.label("—", "mono")
        # 路径可以短显示，鼠标悬停看全：否则这行把最小宽度顶到 785px，窗口缩不下去
        self.folder_val.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        foot.addWidget(self.folder_val, 1)
        foot.addStretch(1)
        foot.addWidget(theme.button("打开录像文件夹", on_click=self._open_recordings))
        self.snapshot_btn = theme.button("保存当前屏幕", on_click=self._save_snapshot)
        self.snapshot_btn.setToolTip("把当前屏幕存成图片（存到 screenshots 文件夹）")
        foot.addWidget(self.snapshot_btn)
        foot.addWidget(theme.button("打开管理网站", on_click=self._open_website))
        foot.addWidget(theme.button("修复工具", "link", self._open_repair))
        lay.addLayout(foot)
        return page

    def _build_banner(self) -> QWidget:
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        lay = QHBoxLayout(self.banner)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(16)
        self.banner_dot = theme.status_dot()
        lay.addWidget(self.banner_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.banner_title = theme.label("", "bannerTitle")
        self.banner_detail = theme.label("", "bannerDetail", wrap=True)
        text.addWidget(self.banner_title)
        text.addWidget(self.banner_detail)
        lay.addLayout(text, 1)
        side = QVBoxLayout()
        side.setSpacing(0)
        side.addWidget(theme.label("今天已连续录制", "bannerSide"), 0, Qt.AlignmentFlag.AlignRight)
        self.duration_val = theme.label("—", "bannerSideValue")
        side.addWidget(self.duration_val, 0, Qt.AlignmentFlag.AlignRight)
        lay.addLayout(side)
        return self.banner

    def _zoom_thumb(self) -> None:
        """点击缩略图：弹出大图窗口，画面随录像刷新。"""
        ZoomDialog(self.state_dir, self.code_label.text(),
                   self.install_dir / SNAPSHOT_DIR, self).exec()

    def _build_thumb(self) -> QWidget:
        self.thumb = ThumbLabel()
        self.thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumb.setToolTip("点击放大")
        self.thumb.clicked.connect(self._zoom_thumb)
        self.thumb.setObjectName("thumb")
        self.rec_badge = QLabel("● REC", self.thumb)
        self.rec_badge.setObjectName("rec")
        self.rec_badge.move(10, 10)
        return self.thumb

    def _build_side(self) -> QVBoxLayout:
        side = QVBoxLayout()
        side.setSpacing(12)

        checks = theme.card()
        cl = QVBoxLayout(checks)
        cl.setContentsMargins(18, 12, 18, 12)
        cl.setSpacing(0)
        self.check_rows = {key: theme.CheckRow(label) for key, label in CHECK_LABEL.items()}
        for row in self.check_rows.values():
            cl.addWidget(row)
        side.addWidget(checks)

        info = theme.card()
        grid = QGridLayout(info)
        grid.setContentsMargins(18, 12, 18, 12)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)  # 16+8=24，跟上面检查卡的 24 行距对上
        self.server_val = theme.label("—", "checkState")
        self.pending_val = theme.label("—", "checkState")
        self.disk_val = theme.label("—", "checkState")
        for r, (k, v) in enumerate((("服务器", self.server_val), ("待上传", self.pending_val),
                                    ("本地空间", self.disk_val))):
            name = theme.label(k, "muted")
            for w in (name, v):  # 高度固定，多出来的高度留给卡片底部
                w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            grid.addWidget(name, r, 0)
            grid.addWidget(v, r, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(3, 1)  # 多出来的高度留到卡片底部，三行文字别被撑散
        side.addWidget(info, 1)  # 吃掉剩余高度，右栏底边和缩略图对齐
        return side

    def _build_timeline_card(self) -> QWidget:
        box = theme.card()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(theme.label("今天的录像", "h2"))
        head.addStretch(1)
        ok, warn = LEVEL_COLORS["ok"]["fg"], LEVEL_COLORS["warn"]["fg"]
        legend = QLabel(f'<span style="color:{ok}">■</span> 已上传　'
                        f'<span style="color:{warn}">■</span> 仅在本机')
        legend.setObjectName("small")
        head.addWidget(legend)
        lay.addLayout(head)
        self.timeline = theme.TimelineWidget()
        lay.addWidget(self.timeline)
        return box

    def _build_log_card(self) -> QWidget:
        box = theme.card()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(18, 10, 12, 10)
        head.setSpacing(8)
        head.addWidget(theme.label("日志", "h2"))
        self.level_buttons: dict[str, object] = {}
        for lv, label in (("all", "全部"), ("warn", "警告及以上"), ("err", "错误")):
            b = theme.button(label, "seg", lambda level=lv: self._set_level(level))
            b.setCheckable(True)
            b.setChecked(lv == self._level)
            self.level_buttons[lv] = b
            head.addWidget(b)
        head.addStretch(1)
        head.addWidget(theme.button("打开日志文件夹", "link", self._open_logs))
        lay.addLayout(head)
        lay.addWidget(_hline())

        self.logs = QTableWidget(0, 3)
        self.logs.horizontalHeader().setVisible(False)
        self.logs.verticalHeader().setVisible(False)
        self.logs.setShowGrid(False)
        self.logs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.logs.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.logs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self.logs.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.logs.setColumnWidth(0, 170)
        self.logs.setColumnWidth(1, 64)
        self.logs.verticalHeader().setDefaultSectionSize(34)
        lay.addWidget(self.logs, 1)
        self.logs_empty = theme.label("还没有日志", "muted")
        self.logs_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.logs_empty)
        return box

    # ---------- 设置 ----------

    def _build_settings(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        outer = QHBoxLayout(page)
        outer.setContentsMargins(24, 18, 24, 16)
        body = QWidget()
        body.setMaximumWidth(760)  # 窗口拉宽时内容不跟着拉散，居中显示
        outer.addWidget(body, 1)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        lay.addWidget(theme.label("设置", "h1"))

        # 位置（只能从下拉里选：连着服务器用服务器的选项，没有就用 config/location_options.json）
        loc = theme.card()
        ll = QVBoxLayout(loc)
        ll.setContentsMargins(20, 14, 20, 14)
        ll.setSpacing(10)
        ensure_location_file(self.config_path.parent)
        self.levels = load_location_levels(self.state_dir, self.config_path.parent)
        self.loc_edits: dict[str, LevelField] = {}
        grid_loc = QGridLayout()
        grid_loc.setHorizontalSpacing(12)
        grid_loc.setVerticalSpacing(10)
        for i, lv in enumerate(self.levels):  # 部门/科室/区域/属地/位置/编号，一行 3 个
            field = LevelField(lv)
            field.changed.connect(self._update_code_preview)
            self.loc_edits[lv["key"]] = field
            grid_loc.addWidget(field, i // 3, i % 3)
            grid_loc.setColumnStretch(i % 3, 1)
        ll.addLayout(grid_loc)
        prev = QHBoxLayout()
        prev.addWidget(theme.label("生成的位置编号", "muted"))
        self.code_preview = theme.label("—", "mono")
        prev.addWidget(self.code_preview)
        prev.addStretch(1)
        ll.addLayout(prev)
        hint = "选项来自服务器；没连服务器时来自 config/location_options.json，要改选项就改这个文件"
        ll.addWidget(theme.label(hint, "small", wrap=True))
        lay.addWidget(loc)

        # 连接与录像
        conn = theme.card()
        grid = QGridLayout(conn)
        grid.setContentsMargins(20, 14, 20, 14)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("留空 = 不连服务器，本机独立录像")
        self.quality_box = QComboBox()
        for qrow in quality_table():
            star = "（推荐）" if qrow["recommended"] else ""
            self.quality_box.addItem(f"{qrow['label']}{star}", qrow["quality"])
        self.folder_edit = QLineEdit()
        self.autostart_box = QCheckBox("开启（开机后自动开始录像）")
        self.screenshot_box = QCheckBox("开启（按下面的间隔把当前屏幕存成图片）")
        self.screenshot_box.toggled.connect(self._sync_screenshot_controls)
        self.screenshot_interval_box = QComboBox()
        for sec in SCREENSHOT_INTERVAL_STOPS:
            self.screenshot_interval_box.addItem(f"{sec // 60} 分钟", sec)
        for r, (label, w) in enumerate((("服务器地址", self.server_edit), ("画质", self.quality_box),
                                        ("录像文件夹", self.folder_edit),
                                        ("开机自动启动", self.autostart_box),
                                        ("定时截屏", self.screenshot_box),
                                        ("截图间隔", self.screenshot_interval_box))):
            grid.addWidget(theme.label(label, "muted"), r, 0)
            grid.addWidget(w, r, 1)
            grid.setRowMinimumHeight(r, 40)  # 输入框和开关行一样高，上下节奏才齐
        self.managed_note = theme.label("", "small", wrap=True)
        grid.addWidget(self.managed_note, 6, 0, 1, 2)
        lay.addWidget(conn)

        # 配置文件
        cfg = theme.card()
        cl = QHBoxLayout(cfg)
        cl.setContentsMargins(20, 12, 20, 12)
        cl.addWidget(theme.label("配置文件", "muted"))
        cl.addWidget(theme.label(str(self.config_path), "mono"), 1)
        cl.addWidget(theme.button("打开截图文件夹", on_click=self._open_screenshots))
        cl.addWidget(theme.button("用记事本打开", on_click=self._open_config))
        lay.addWidget(cfg)

        lay.addStretch(1)

        foot = QHBoxLayout()
        foot.setSpacing(12)
        self.save_msg = theme.label("", "pill", wrap=True)
        self.save_msg.hide()
        foot.addWidget(self.save_msg, 0, Qt.AlignmentFlag.AlignLeft)
        foot.addStretch(1)
        foot.addWidget(theme.label("录像相关改动在下一段开始时生效；截屏开关立即生效", "small"))
        self.save_btn = theme.button("保存", "primary", self._save_settings)
        self.save_btn.setMinimumWidth(120)
        foot.addWidget(self.save_btn)
        lay.addLayout(foot)
        return page

    # ---------- 逻辑 ----------

    def _set_level(self, level: str) -> None:
        self._level = level
        for lv, b in self.level_buttons.items():
            b.setChecked(lv == level)
        self._log_sig = None
        self._refresh_logs()

    def _toggle_settings(self) -> None:
        idx = 1 if self.stack.currentIndex() == 0 else 0
        self.stack.setCurrentIndex(idx)
        self.menu_btn.setText("← 返回" if idx == 1 else "设置")
        if idx == 1:
            self._render_settings()

    def _update_code_preview(self) -> None:
        parts = [f.value() for f in self.loc_edits.values()]
        self.code_preview.setText("-".join(p for p in parts if p) or "—")

    def _show_msg(self, text: str, level: str) -> None:
        self.save_msg.setText(text)
        theme.set_level(self.save_msg, level)
        self.save_msg.setVisible(bool(text))

    def _server_quality(self) -> str | None:
        """连着服务器时，服务器下发的画质（Agent 缓存在 state/server_config.json）；没有就返回 None。"""
        try:
            data = json.loads((self.state_dir / "server_config.json").read_text(encoding="utf-8"))
            return data["settings"].get("quality")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _render_settings(self) -> None:
        cfg = load_config(self.config_path) if self.config_path.exists() else None
        for key, field in self.loc_edits.items():
            field.set_value(getattr(cfg.location, key, "") if cfg else "")
        self._update_code_preview()
        self.server_edit.setText(cfg.server if cfg else "")
        quality = cfg.recording.quality.value if cfg else "standard"
        # 有服务器就以服务器下发的为准（画质等由服务器统一管理），没有才在本机改
        managed = self._server_quality() if (cfg and cfg.server) else None
        if managed:
            quality = managed
        idx = self.quality_box.findData(quality)
        self.quality_box.setCurrentIndex(max(idx, 0))
        self.quality_box.setEnabled(not managed)
        self.managed_note.setText("画质由服务器统一设置，本机不能改；服务器地址、录像文件夹、开机自启仍在本机设置"
                                  if managed else "")
        self.managed_note.setVisible(bool(managed))
        self.folder_edit.setText(cfg.recording.folder if cfg else "recordings")
        self.autostart_box.setChecked(cfg.autostart if cfg else True)
        self.screenshot_box.setChecked(cfg.screenshot.enabled if cfg else False)
        stop = self.screenshot_interval_box.findData(cfg.screenshot.interval_seconds if cfg else 300)
        self.screenshot_interval_box.setCurrentIndex(max(stop, 0))
        self._sync_screenshot_controls()
        if needs_setup(self.config_path):
            self._show_msg("还没有配置：请先填写位置编号并保存", "info")
        else:
            self._show_msg("", "idle")

    def _sync_screenshot_controls(self) -> None:
        """关掉定时截屏时间隔置灰，免得以为还在抓。"""
        self.screenshot_interval_box.setEnabled(self.screenshot_box.isChecked())

    def _save_settings(self) -> None:
        """直接写配置文件，不需要管理员登录；没有写入权限时给出提示。"""
        missing = [f.label for f in self.loc_edits.values() if f.has_options and not f.value()]
        if missing:
            self._show_msg("请先选择：" + "、".join(missing), "warn")
            return
        values = {k: f.value() for k, f in self.loc_edits.items()}
        values.update(server=self.server_edit.text(), folder=self.folder_edit.text(),
                      autostart=self.autostart_box.isChecked(),
                      screenshot_enabled=self.screenshot_box.isChecked(),
                      screenshot_interval_seconds=self.screenshot_interval_box.currentData())
        if self.quality_box.isEnabled():  # 服务器管理画质时不写本机
            values["quality"] = self.quality_box.currentData()
        try:
            warnings = apply_settings(self.config_path, values)
        except OSError:
            self._show_msg("保存失败：当前账号没有改这个配置文件的权限，请换有权限的账号，或直接改文件",
                           "error")
            return
        if warnings:
            self._show_msg("已保存。注意：" + "；".join(warnings), "warn")
        else:
            self._show_msg("✓ 已保存", "ok")

    def refresh(self) -> None:
        st = read_status(self.state_dir)
        self.code_label.setText(st.get("code", ""))

        state = overall_state(st)
        level = state["level"]
        self.banner_title.setText(state["title"])
        self.banner_detail.setText(state["detail"])
        self.pill.setText(state["title"].split(" · ")[0])
        for w in (self.banner, self.banner_title, self.banner_dot, self.pill):
            theme.set_level(w, level)
        self.duration_val.setText(recording_duration(self.state_dir) if st.get("recording") else "—")

        # 缩略图
        thumb = self.state_dir / "thumb.jpg"
        pix = QPixmap(str(thumb)) if thumb.exists() and not st.get("stale") else QPixmap()
        self.thumb.set_source(pix)
        recording = bool(st.get("recording"))
        self.rec_badge.setText("● REC" if recording else "■ 未录像")
        self.rec_badge.setStyleSheet(
            "" if recording else f'background: {LEVEL_COLORS["error"]["fg"]}; color: #FFFFFF;')
        self.rec_badge.adjustSize()

        # 检查项
        checks = st.get("checks", {})
        for key, row in self.check_rows.items():
            row.set_value(checks.get(key, "ok" if st and not st.get("stale") else "fail"))

        online = st.get("online")
        pending = int(st.get("pending_sync", 0))
        if online is False:
            self._set_value(self.server_val, "未连接", "warn")
        elif online:
            self._set_value(self.server_val, "已连接", "ok")
        else:
            self._set_value(self.server_val, "未配置，本机独立录像", "idle")
        self._set_value(self.pending_val, f"{pending} 段", "info" if pending else "ok")
        used = float(st.get("disk_used", 0))
        self._set_value(self.disk_val, f"已用 {int(used * 100)}%",
                        "error" if used >= 0.9 else "warn" if used >= 0.8 else "ok")

        self.timeline.set_bars(build_timeline(self.state_dir),
                               datetime.now().hour + datetime.now().minute / 60)
        self._refresh_logs()
        self.folder_val.setText(str(self.install_dir / "recordings"))
        self.folder_val.setToolTip(str(self.install_dir / "recordings"))

    @staticmethod
    def _set_value(label: QLabel, text: str, level: str) -> None:
        label.setText(text)
        theme.set_level(label, level)

    def _refresh_logs(self) -> None:
        all_logs = read_logs(self.install_dir / "logs")
        warn = [x for x in all_logs if x["level"] != "INFO" and x["level"] != "DEBUG"]
        err = [x for x in all_logs if x["level"] in ("ERROR", "CRITICAL")]
        for lv, n in (("all", len(all_logs)), ("warn", len(warn)), ("err", len(err))):
            base = {"all": "全部", "warn": "警告及以上", "err": "错误"}[lv]
            self.level_buttons[lv].setText(f"{base} {n}")
        shown = {"all": all_logs, "warn": warn, "err": err}[self._level]
        sig = (self._level, tuple((x["time"], x["msg"]) for x in shown))
        if sig == self._log_sig:  # 没变化就不重画，避免滚动位置被重置
            return
        self._log_sig = sig
        self.logs.setRowCount(len(shown))
        for r, log in enumerate(shown):
            level = log["level"]
            cells = (log["time"], friendly_level(level), log["msg"])
            tone = "warn" if level == "WARNING" else "error" if level in ("ERROR", "CRITICAL") else ""
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if tone:
                    item.setBackground(QColor(LEVEL_COLORS[tone]["bg"]))
                    if c == 1:
                        f = item.font()
                        f.setBold(True)
                        item.setFont(f)
                        item.setForeground(QColor(LEVEL_COLORS[tone]["fg"]))
                self.logs.setItem(r, c, item)
        self.logs_empty.setVisible(not shown)
        self.logs.setVisible(bool(shown))

    def _open_logs(self) -> None:
        d = self.install_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        open_path(d)

    def _open_recordings(self) -> None:
        d = self.install_dir / "recordings"
        d.mkdir(parents=True, exist_ok=True)
        open_path(d)

    def _open_screenshots(self) -> None:
        d = self.install_dir / SNAPSHOT_DIR
        d.mkdir(parents=True, exist_ok=True)
        open_path(d)

    def _save_snapshot(self) -> None:
        """一键截图：抓当前屏幕存到截图目录；按钮临时反馈，不弹窗。"""
        try:
            path = save_screen(self.install_dir / SNAPSHOT_DIR, self.code_label.text())
        except OSError as exc:
            self.snapshot_btn.setText("保存失败")
            self.snapshot_btn.setToolTip(str(exc))
        else:
            self.snapshot_btn.setText("已保存 ✓")
            self.snapshot_btn.setToolTip(str(path))
        QTimer.singleShot(2000, lambda: self.snapshot_btn.setText("保存当前屏幕"))

    def _open_repair(self) -> None:
        subprocess.Popen(["everrec-desktop", "--repair"])  # noqa: S603, S607

    def _open_config(self) -> None:
        if self.config_path.exists():
            open_path(self.config_path)
        else:
            self._show_msg("还没有配置文件，请先填写并保存", "info")

    def _open_website(self) -> None:
        cfg = load_config(self.config_path) if self.config_path.exists() else None
        url = (cfg.server or "").strip() if cfg else ""
        if not url:
            self._show_msg("还没配置服务器地址，先到设置里填上服务器", "info")
            return
        open_url(url)

    # ---------- 托盘与关闭 ----------

    def set_tray(self, tray) -> None:
        self._tray = tray

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def quit(self) -> None:
        self._quitting = True
        QApplication.quit()

    def closeEvent(self, e) -> None:  # noqa: N802
        if self._tray is not None and not self._quitting:
            e.ignore()
            self.hide()
        else:
            e.accept()
