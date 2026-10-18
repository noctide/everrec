"""存一张画面：一键截图抓当前屏幕，放大窗口里存正看到的那一帧；都烧编号和时间水印。

抓当前屏幕必须在登录用户的会话里做：Windows 服务跑在 Session 0，抓不到用户桌面，
所以这一路只在客户端进程里跑，不进 Agent。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPixmap

from .common import snapshot_path


def grab_screen() -> QPixmap:
    """抓主屏当前画面。多显示器只抓主屏；没有可用屏幕时抛 OSError。"""
    screen = QGuiApplication.primaryScreen()
    pix = screen.grabWindow(0) if screen else QPixmap()
    if pix.isNull():
        raise OSError("抓不到屏幕画面")
    return pix


def grab_thumbnail(state_dir: Path) -> QPixmap:
    """读最近一张录像画面（state/thumb.jpg）。

    FFmpeg 每分钟覆盖重写缩略图，读图可能正好撞上半截文件；读到损坏图时等 50ms 重试一次，
    再失败抛 OSError，由界面提示"保存失败"。
    """
    src = state_dir / "thumb.jpg"
    pix = QPixmap(str(src))
    if pix.isNull():
        time.sleep(0.05)  # 可能正赶上 FFmpeg 覆盖重写，稍等重试一次
        pix = QPixmap(str(src))
    if pix.isNull():
        raise OSError(f"读不到画面：{src}")
    return pix


def save_screen(dest_dir: Path, code: str, now: datetime | None = None) -> Path:
    """一键截图：把当前屏幕存成一张带水印的图，返回落盘路径。"""
    return save_pixmap(grab_screen(), dest_dir, code, now)


def save_thumbnail(state_dir: Path, dest_dir: Path, code: str,
                   now: datetime | None = None) -> Path:
    """把最近一张录像画面存成带水印的图（放大窗口里「保存这张画面」用）。"""
    return save_pixmap(grab_thumbnail(state_dir), dest_dir, code, now)


def save_pixmap(pix: QPixmap, dest_dir: Path, code: str,
                now: datetime | None = None) -> Path:
    """烧水印并存成 JPEG；dest_dir 不存在时自动建。"""
    now = now or datetime.now()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = snapshot_path(dest_dir, code, now)
    if not _watermark(pix, code, now).save(str(dest), "JPEG", 90):
        raise OSError(f"保存失败：{dest}")
    return dest


def _watermark(pix: QPixmap, code: str, now: datetime) -> QPixmap:
    """右下角烧一行 `编号  时间`，样式照录像水印（黑底半透明 + 白字）。"""
    out = QPixmap(pix)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    text = f"{code}  {now:%Y-%m-%d %H:%M:%S}"
    font = QFont()
    font.setPixelSize(max(12, pix.width() // 45))
    painter.setFont(font)
    fm = QFontMetrics(font)
    tw = fm.horizontalAdvance(text)
    th = fm.height()
    margin = font.pixelSize() // 2
    box = QRect(pix.width() - tw - margin * 2, pix.height() - th - margin * 2,
                tw + margin * 2, th + margin * 2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 128))
    painter.drawRect(box)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return out
