"""桌面端统一外观：一份全局样式表 + 几个小组件，三个窗口共用。

要点：样式一律按 objectName / 属性选择，不再在父容器上写 border（会串到所有子控件）。
按钮有普通 / 悬停 / 按下 / 禁用四种状态；状态色用 `level` 属性切换（ok / info / warn / error / idle）。

配色与网页端同一套（web/src/styles.css 的 :root）：冷白页面底、白色内容面、发丝线边框、
状态色只出现在点、文字和浅底上。尺寸比网页略大一点，照顾桌面端的鼠标操作。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from .common import COLORS, LEVEL_COLORS

FONT_FAMILY = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"
MONO = "Consolas, 'DejaVu Sans Mono', monospace"


def _level_rules() -> str:
    out = []
    for lv, c in LEVEL_COLORS.items():
        out.append(
            f'QFrame#banner[level="{lv}"] {{ background: {c["bg"]}; border: 1px solid {c["line"]}; }}\n'
            f'QLabel#bannerTitle[level="{lv}"] {{ color: {c["fg"]}; }}\n'
            f'QLabel#dot[level="{lv}"] {{ background: {c["fg"]}; }}\n'
            f'QLabel#pill[level="{lv}"] {{ background: {c["bg"]}; color: {c["fg"]}; '
            f'border: 1px solid {c["line"]}; }}\n'
            f'QLabel#checkIcon[level="{lv}"] {{ background: {c["bg"]}; color: {c["fg"]}; }}\n'
            f'QLabel#checkState[level="{lv}"] {{ color: {c["fg"]}; }}\n'
        )
    return "".join(out)


STYLESHEET = f"""
QWidget {{ font-family: "{FONT_FAMILY}"; font-size: 13px; color: {COLORS['text']}; }}
QWidget#root, QWidget#page {{ background: {COLORS['canvas']}; }}
QLabel {{ background: transparent; border: none; }}

QFrame#header {{ background: {COLORS['bg']}; border-bottom: 1px solid {COLORS['line']}; }}
QLabel#brand {{ font-size: 14px; font-weight: 600; color: {COLORS['strong']}; }}
QLabel#code {{ font-family: {MONO}; color: {COLORS['muted']}; }}
QLabel#h1 {{ font-size: 16px; font-weight: 600; color: {COLORS['strong']}; }}
QLabel#h2 {{ font-size: 13px; font-weight: 600; color: {COLORS['strong']}; }}
QLabel#muted {{ color: {COLORS['muted']}; }}
QLabel#small {{ color: {COLORS['muted']}; font-size: 11px; }}
QLabel#mono {{ font-family: {MONO}; }}
QLabel#axis {{ color: {COLORS['muted']}; font-size: 11px; font-family: {MONO}; }}

QFrame#card {{ background: {COLORS['bg']}; border: 1px solid {COLORS['line']}; border-radius: 6px; }}
QFrame#note {{ background: {COLORS['bar']}; border-radius: 4px; }}
QFrame#sep {{ background: {COLORS['line']}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#banner {{ border-radius: 6px; }}
QLabel#bannerTitle {{ font-size: 18px; font-weight: 600; }}
QLabel#bannerDetail {{ color: {COLORS['text']}; }}
QLabel#bannerSide {{ color: {COLORS['muted']}; }}
QLabel#bannerSideValue {{ font-size: 15px; font-weight: 600; color: {COLORS['strong']}; }}
QLabel#dot {{ min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px; border-radius: 5px; }}
QLabel#pill {{ padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 500; }}
QLabel#checkIcon {{ min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;
    border-radius: 9px; font-weight: 600; font-size: 11px; qproperty-alignment: AlignCenter; }}
QLabel#thumb {{ background: {COLORS['thumb']}; color: {COLORS['note']}; border-radius: 6px; }}
QLabel#rec {{ background: rgba(0,0,0,0.65); color: #FFFFFF; border-radius: 3px; padding: 2px 8px;
    font-weight: 600; font-size: 11px; }}
{_level_rules()}

QPushButton {{ background: {COLORS['bg']}; border: 1px solid {COLORS['line2']}; border-radius: 6px;
    padding: 7px 16px; color: {COLORS['strong']}; }}
QPushButton:hover {{ background: {COLORS['bar']}; border-color: {COLORS['muted']}; }}
QPushButton:pressed {{ background: {COLORS['line']}; border-color: {COLORS['text']}; }}
QPushButton:focus {{ border: 2px solid {COLORS['focus']}; padding: 6px 15px; }}
QPushButton:disabled {{ background: {COLORS['bar']}; color: {COLORS['disabled']};
    border-color: {COLORS['line']}; }}

QPushButton[kind="primary"] {{ background: {COLORS['accent']}; border: 1px solid {COLORS['accent']};
    color: #FFFFFF; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {COLORS['accent_hover']};
    border-color: {COLORS['accent_hover']}; }}
QPushButton[kind="primary"]:pressed {{ background: {COLORS['accent_pressed']};
    border-color: {COLORS['accent_pressed']}; }}
QPushButton[kind="primary"]:disabled {{ background: {COLORS['line2']}; border-color: {COLORS['line2']};
    color: {COLORS['bg']}; }}

QPushButton[kind="link"] {{ border: none; background: transparent; color: {COLORS['muted']};
    text-decoration: underline; padding: 4px 6px; }}
QPushButton[kind="link"]:hover {{ color: {COLORS['strong']}; background: {COLORS['bar']}; }}
QPushButton[kind="link"]:pressed {{ background: {COLORS['line']}; }}

QPushButton[kind="seg"] {{ border-radius: 14px; padding: 4px 14px; }}
QPushButton[kind="seg"]:checked {{ background: {COLORS['accent']}; border-color: {COLORS['accent']};
    color: #FFFFFF; font-weight: 600; }}
QPushButton[kind="seg"]:checked:hover {{ background: {COLORS['accent_hover']}; }}

QLineEdit, QComboBox {{ background: {COLORS['bg']}; border: 1px solid {COLORS['line2']}; border-radius: 6px;
    padding: 7px 10px; selection-background-color: {COLORS['line2']};
    selection-color: {COLORS['strong']}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {COLORS['muted']}; }}
QLineEdit:focus, QComboBox:focus {{ border: 2px solid {COLORS['focus']}; padding: 6px 9px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {COLORS['bg']}; border: 1px solid {COLORS['line2']};
    selection-background-color: {COLORS['bar']}; selection-color: {COLORS['strong']}; outline: none; }}

QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 40px; height: 22px; border-radius: 11px; background: {COLORS['line2']}; }}
QCheckBox::indicator:checked {{ background: {LEVEL_COLORS['ok']['fg']}; }}

QTableWidget {{ background: {COLORS['bg']}; border: none; outline: none; font-size: 12px; }}
QTableWidget::item {{ padding: 6px 10px 6px 18px; border-bottom: 1px solid {COLORS['bar']}; }}
QTableWidget::item:selected {{ background: {COLORS['bar']}; color: {COLORS['text']}; }}
QListWidget {{ background: {COLORS['bg']}; border: 1px solid {COLORS['line']}; border-radius: 6px;
    outline: none; padding: 4px; }}
QListWidget::item {{ padding: 8px 10px; border-bottom: 1px solid {COLORS['bar']}; }}
QListWidget::item:selected {{ background: {COLORS['bar']}; color: {COLORS['text']}; }}
QTextEdit {{ background: {COLORS['bar']}; border: 1px solid {COLORS['line']}; border-radius: 6px;
    font-family: {MONO}; font-size: 12px; }}

QProgressBar {{ background: {COLORS['bar']}; border: none; border-radius: 4px;
    max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {LEVEL_COLORS['info']['fg']}; border-radius: 4px; }}

QScrollArea#scroll {{ background: {COLORS['canvas']}; border: none; }}
QScrollArea#scroll > QWidget > QWidget {{ background: {COLORS['canvas']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {COLORS['line2']}; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {COLORS['muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def install(app: QApplication) -> None:
    """给整个应用套上统一样式和字体。"""
    app.setStyle("Fusion")  # Fusion 会正确遵循样式表，各平台外观一致
    app.setFont(QFont(FONT_FAMILY, 10))
    app.setStyleSheet(STYLESHEET)


def set_level(widget: QWidget, level: str) -> None:
    """切换控件的状态色（改属性后要重新套一次样式才生效）。"""
    if widget.property("level") == level:
        return
    widget.setProperty("level", level)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def button(text: str, kind: str = "", on_click=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        b.clicked.connect(lambda _checked=False: on_click())
    return b


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def status_dot(level: str = "idle") -> QLabel:
    d = QLabel()
    d.setObjectName("dot")
    set_level(d, level)
    return d


class CheckRow(QWidget):
    """一行检查项：左边圆形图标，中间名称，右边状态文字。

    正常时只有图标是绿的，文字用中性灰 —— 五行都绿会淹掉真正要看的那个红。
    """

    ICON = {"ok": "✓", "warn": "!", "fail": "✕", "error": "✕"}
    LEVEL = {"ok": "ok", "warn": "warn", "fail": "error", "error": "error"}
    TEXT = {"ok": "正常", "warn": "要留意", "fail": "出问题", "error": "出问题"}

    def __init__(self, name: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(12)
        self.icon = QLabel("✓")
        self.icon.setObjectName("checkIcon")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name = QLabel(name)
        self.state = QLabel("正常")
        self.state.setObjectName("checkState")
        lay.addWidget(self.icon)
        lay.addWidget(self.name, 1)
        lay.addWidget(self.state)
        self.set_value("ok")

    def set_value(self, value: str, detail: str = "") -> None:
        level = self.LEVEL.get(value, "error")
        self.icon.setText(self.ICON.get(value, "✕"))
        self.state.setText(detail or self.TEXT.get(value, "出问题"))
        set_level(self.icon, level)
        set_level(self.state, "idle" if level == "ok" else level)


class TimelineWidget(QWidget):
    """今天 0–24 点的录像条：绿 = 已上传，琥珀 = 仅在本机，竖线 = 现在。

    刻度和下面的时刻文字都自己画，保证文字中心正对刻度（用布局排会偏半格）。
    """

    TRACK_TOP = 2
    TRACK_H = 22
    AXIS_H = 18

    def __init__(self):
        super().__init__()
        self.setFixedHeight(self.TRACK_TOP + self.TRACK_H + self.AXIS_H)
        self.bars: list = []
        self.now_hour: float | None = None

    def set_bars(self, bars, now_hour: float | None = None) -> None:
        self.bars, self.now_hour = bars, now_hour
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        top, h = self.TRACK_TOP, self.TRACK_H
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(COLORS["bar"]))
        p.drawRoundedRect(QRectF(0, top, w, h), 3, 3)
        for b in self.bars:
            color = LEVEL_COLORS["ok"]["fg"] if b.synced else LEVEL_COLORS["warn"]["fg"]
            p.setBrush(QColor(color))
            x = b.start / 24 * w
            p.drawRect(QRectF(x, top, max(2.0, (b.end - b.start) / 24 * w), h))
        p.setPen(QPen(QColor(COLORS["line"]), 1))
        for hour in (6, 12, 18):  # 刻度线
            x = int(hour / 24 * w)
            p.drawLine(x, top, x, top + h)
        if self.now_hour is not None:
            p.setPen(QPen(QColor(COLORS["strong"]), 2))
            x = int(self.now_hour / 24 * w)
            p.drawLine(x, top - 2, x, top + h + 2)

        # 时刻文字：首尾贴边，中间按刻度居中
        f = p.font()
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(COLORS["muted"]))
        base = top + h + 13
        for hour, text in ((0, "00:00"), (6, "06:00"), (12, "12:00"), (18, "18:00"), (24, "24:00")):
            x = hour / 24 * w
            tw = p.fontMetrics().horizontalAdvance(text)
            if hour == 0:
                p.drawText(int(x), base, text)
            elif hour == 24:
                p.drawText(int(x) - tw, base, text)
            else:
                p.drawText(int(x - tw / 2), base, text)
        p.end()
