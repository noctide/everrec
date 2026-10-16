"""命令行入口。

  everrec-agent init  D:\\EverRec          生成默认配置 config/everrec.yaml
  everrec-agent run   -c D:\\EverRec\\config\\everrec.yaml
  everrec-agent list-cameras              列出本机摄像头名称
  everrec-agent show-command -c ...       打印将要执行的 FFmpeg 命令（排查用）

Windows 上用 WinSW 把 `everrec-agent run -c ...` 注册成服务（见 deploy/windows）。
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from everrec_core.config import AgentConfig, load_config, save_config
from everrec_core.ffmpeg import build_record_command, list_cameras

from .agent import Agent
from .logs import setup_logging
from .single_instance import AlreadyRunning, SingleInstance


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.install_dir) / "config" / "everrec.yaml"
    if path.exists() and not args.force:
        print(f"已存在：{path}（加 --force 覆盖）")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    save_config(AgentConfig(), path)
    print(f"已生成：{path}")
    return 0


def _load(path: str) -> AgentConfig | None:
    if not Path(path).exists():
        install_dir = Path(path).parent.parent
        print(f"找不到配置文件：{path}\n先生成一份：everrec-agent init {install_dir}", file=sys.stderr)
        return None
    return load_config(path)


def cmd_show(args: argparse.Namespace) -> int:
    if (cfg := _load(args.config)) is None:
        return 1
    print(" ".join(f'"{a}"' if " " in a else a for a in build_record_command(cfg)))
    return 0


def cmd_cameras(_args: argparse.Namespace) -> int:
    cams = list_cameras()
    if not cams:
        print("没找到摄像头。检查设备管理器，以及 设置 → 隐私 → 相机 → 允许桌面应用访问相机。")
        return 1
    print("找到的摄像头（把名称填到配置文件的 camera.device）：")
    for c in cams:
        print(f"  {c}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if (cfg := _load(args.config)) is None:
        return 1
    recent = setup_logging(cfg.logs_dir, args.verbose)
    try:
        with SingleInstance(cfg.state_dir):
            agent = Agent(cfg, recent, stall_seconds=args.stall_seconds)

            def _stop(*_):
                agent.stop_event.set()

            signal.signal(signal.SIGINT, _stop)
            signal.signal(signal.SIGTERM, _stop)
            agent.run()
    except AlreadyRunning as e:
        print(e, file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="everrec-agent", description="EverRec 采集端")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="生成默认配置文件")
    s.add_argument("install_dir")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("list-cameras", help="列出本机摄像头")
    s.set_defaults(func=cmd_cameras)

    s = sub.add_parser("show-command", help="打印 FFmpeg 录制命令")
    s.add_argument("-c", "--config", required=True)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("run", help="开始录制（前台运行，服务管理器调用的也是这个）")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--stall-seconds", type=float, default=30, help="文件多少秒不增长算假录制")
    s.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
