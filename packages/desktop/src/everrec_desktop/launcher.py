"""桌面端唯一入口：默认客户端 + 托盘，--tray 只托盘，--repair 只修复工具。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from . import theme
from .client import ClientWindow
from .common import default_install_dir
from .repair_tool import RepairTool
from .screenshots import TimedScreenshots
from .tray import TrayIcon


def _install_dir(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "install_dir", None) or default_install_dir())


def main() -> int:
    ap = argparse.ArgumentParser(prog="everrec-desktop")
    ap.add_argument("--install-dir", help="安装目录，默认是客户端所在的文件夹")
    ap.add_argument("--repair", action="store_true", help="只打开修复工具")
    ap.add_argument("--tray", action="store_true", help="只常驻托盘图标（开机自启用）")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    theme.install(app)

    if args.repair:  # 修复工具：关窗即退出
        w = RepairTool()
        w.show()
        return app.exec()

    app.setQuitOnLastWindowClosed(False)  # 关客户端窗口只隐藏，等托盘菜单「退出」
    client = ClientWindow(_install_dir(args))
    tray = TrayIcon(_install_dir(args), on_open=client.show_and_raise, on_quit=client.quit)
    client.set_tray(tray)
    tray.show()
    # 定时截屏挂在客户端进程里：只有登录用户的会话才抓得到屏幕，服务抓不到（Session 0）
    TimedScreenshots(_install_dir(args), parent=client)
    if not args.tray:  # 默认弹客户端窗口；--tray 只托盘
        client.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
