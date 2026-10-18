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

from everrec_core.config import AgentConfig, load_config, save_config, validate_tolerant
from everrec_core.ffmpeg import build_record_command, list_camera_modes, list_cameras

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
        for m in list_camera_modes(c):
            print(f"      {m.size:>10}  最高 {m.max_fps:g} 帧  {m.codec or m.pixel_format}")
    return 0


def _set_nested(data: dict, keys: list[str], value: str) -> None:
    node = data
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value  # 保持字符串，类型交给 pydantic 校验时转换（保留前导零等）


def cmd_set_config(args: argparse.Namespace) -> int:
    """修改配置文件。桌面端改设置时经 UAC 提权调用（设计 4.6、WP8）。"""
    if (cfg := _load(args.config)) is None:
        return 1
    data = cfg.model_dump(mode="json", exclude={"install_dir", "config_warnings", "config_path"})
    for kv in args.keyvalues:
        if "=" not in kv:
            print(f"参数格式应为 键=值：{kv}", file=sys.stderr)
            return 1
        key, value = kv.split("=", 1)
        _set_nested(data, key.split("."), value)
    new_cfg, warnings = validate_tolerant(data)
    save_config(new_cfg, args.config)
    for w in warnings:
        print(f"警告：{w}", file=sys.stderr)
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


def _use_utf8_console() -> None:
    """让命令行输出用 UTF-8。

    Windows 上的控制台/重定向输出可能不是 UTF-8（英文系统是 cp1252），这时 print 中文会抛
    UnicodeEncodeError：命令明明已经把配置生成好了，最后却带着报错退出，现场看着像失败。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):  # 没有这俩流（pythonw）或流不可配置
            pass


def main(argv: list[str] | None = None) -> int:
    _use_utf8_console()
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

    s = sub.add_parser("set-config", help="修改配置文件（键=值，桌面端改设置用）")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("keyvalues", nargs="+", metavar="KEY=VALUE",
                   help="如 server=https://x location.number=012 recording.quality=standard")
    s.set_defaults(func=cmd_set_config)

    s = sub.add_parser("run", help="开始录制（前台运行，服务管理器调用的也是这个）")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--stall-seconds", type=float, default=30, help="文件多少秒不增长算假录制")
    s.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
