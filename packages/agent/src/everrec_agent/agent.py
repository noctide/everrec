"""Agent 主循环。

每 2 秒一轮：
  ③ 看住 FFmpeg 进程，挂了就退避重启
  ④ 看当前分段是否在增长，30 秒不长就重启并告警
  归档已写完的分段，超出本地配额删最旧的
  ⑥ 检查磁盘
  到点了就在后台线程发心跳；连不上服务器照常录，记下离线开始时间

启动时先处理 _spool 里上次异常中断留下的分段：检查、必要时修复，再归档。
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from everrec_core import repair
from everrec_core.config import AgentConfig
from everrec_core.ffmpeg import camera_device, effective_encoding, list_camera_modes, spool_dir
from everrec_core.health import Check, GrowthWatcher, storage_check
from everrec_core.protocol import Checks, Heartbeat, HeartbeatReply
from everrec_core.segments import archive_segment, enforce_quota, list_spool

from . import __version__, child_guard
from .index import IndexEntry, SegmentIndex
from .logs import RecentProblems
from .recorder import Recorder

log = logging.getLogger("agent")

TICK_SECONDS = 2


class Agent:
    def __init__(self, cfg: AgentConfig, recent: RecentProblems | None = None, stall_seconds: float = 30):
        self.cfg = cfg
        self.recent = recent or RecentProblems()
        self.recorder = Recorder(cfg)
        self.watcher = GrowthWatcher(spool_dir(cfg), stall_seconds)
        self.index = SegmentIndex(cfg.state_dir)
        self.checks = Checks()
        self.problems: list[str] = []
        self.disk_used = 0.0
        self.online: bool | None = None
        self.offline_since: datetime | None = None
        self._last_hb = 0.0
        self._hb_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._encoding_label = cfg.recording.encoding.label
        self._encoding_note: str | None = None
        self._camera_resolutions: list[str] = []

    # ---------- 启动 ----------

    def recover_spool(self) -> None:
        """上次没正常结束的分段：先修复再归档（设计文档 4.5、9.5）。"""
        leftovers = list_spool(spool_dir(self.cfg))
        if not leftovers:
            return
        log.info("发现 %d 个上次中断留下的分段，检查并修复", len(leftovers))
        quarantine = self.cfg.recordings_dir / "_quarantine"
        for seg in leftovers:
            try:
                self._recover_one(seg, quarantine)
            except PermissionError:
                # 还被别的进程占着（例如没清掉的残留 FFmpeg）：先跳过，下次启动再处理
                log.warning("分段 %s 正被其他程序占用，暂不处理", seg.path.name)
            except Exception:
                log.exception("处理中断分段 %s 失败，保留原文件", seg.path.name)

    def _recover_one(self, seg, quarantine: Path) -> None:
        if seg.path.stat().st_size == 0:
            seg.path.unlink()
            return
        fixed_dir = spool_dir(self.cfg) / "_fixed"
        res = repair.repair_file(seg.path, fixed_dir)
        if res.integrity == repair.Integrity.OK:
            self._archive(seg, res.integrity)
            return
        log.warning("分段 %s：%s（方法 %s）", seg.path.name, res.friendly, res.method)
        quarantine.mkdir(parents=True, exist_ok=True)
        seg.path.replace(quarantine / seg.path.name)  # 原文件进隔离目录，不删除
        if res.output:
            fixed = type(seg)(res.output, seg.code, seg.started_at)
            self._archive(fixed, res.integrity)

    def _probe_camera(self) -> None:
        """记下实际录制画质和摄像头支持的分辨率（心跳上报，设置页据此置灰挡位）。"""
        try:
            enc, mode, note = effective_encoding(self.cfg)
            modes = list_camera_modes(camera_device(self.cfg))
        except Exception as e:  # 探测失败不影响录制
            log.warning("读取摄像头支持的格式失败：%s", e)
            return
        self._encoding_label, self._encoding_note = enc.label, note
        self._camera_resolutions = sorted({m.size for m in modes}, key=lambda x: int(x.split("x")[1]))
        src = f"（取流格式 {mode.size} {mode.codec or mode.pixel_format}）" if mode else ""
        log.info("录制画质：%s%s", enc.label, src)
        if note:
            log.warning(note)

    # ---------- 每一轮 ----------

    def _archive(self, seg, integrity: str = "ok") -> None:
        dest = archive_segment(self.cfg.recordings_dir, seg)
        self.index.add(
            IndexEntry(str(dest), seg.started_at.isoformat(), dest.stat().st_size, str(integrity))
        )
        log.info("分段已归档：%s", dest.relative_to(self.cfg.recordings_dir))

    def archive_finished(self) -> None:
        segs = list_spool(spool_dir(self.cfg))
        finished = segs[:-1] if self.recorder.running else []
        for seg in finished:
            self._archive(seg)

    def enforce_local_quota(self) -> None:
        quota = int(self.cfg.recording.local_quota_gb * 1024**3)
        if self.cfg.server:
            synced = self.index.synced_paths()
            can_delete = lambda p: str(p) in synced  # noqa: E731  有服务器时只删已同步的
        else:
            can_delete = lambda _p: True  # noqa: E731  纯本地运行：直接滚动覆盖
        for p in enforce_quota(self.cfg.recordings_dir, quota, can_delete):
            log.info("超出本地配额，删除最旧分段：%s", p.name)

    def run_checks(self) -> None:
        problems: list[str] = []
        self.recorder.supervise()

        process = Check.OK if self.recorder.running else Check.FAIL
        if process is Check.FAIL:
            problems.append("录制进程未运行")

        dataflow, msg = self.watcher.check()
        if not self.recorder.running:
            dataflow = Check.FAIL  # 进程都没在跑，文件不可能在长
            self.watcher.reset()
        elif dataflow is Check.FAIL:
            problems.append(msg)
            self.recorder.restart(msg)
            self.watcher.reset()
        elif self.watcher.grew_since_reset:
            self.recorder.device_error = None

        device = Check.FAIL if self.recorder.device_error else Check.OK
        if self.recorder.device_error:
            problems.append(f"摄像头异常：{self.recorder.device_error}")

        # 配置写错、摄像头达不到设定分辨率：不影响录制，但要让管理员看到
        problems.extend(self.cfg.config_warnings)
        if self._encoding_note:
            problems.append(self._encoding_note)

        storage, smsg, self.disk_used = storage_check(
            self.cfg.recordings_dir, self.cfg.recording.disk_usage_limit
        )
        if smsg:
            problems.append(smsg)

        self.checks = Checks(process=process, dataflow=dataflow, device=device, storage=storage)
        self.problems = problems

    @property
    def recording(self) -> bool:
        return (
            self.recorder.running
            and self.checks.dataflow is Check.OK
            and self.watcher.grew_since_reset
        )

    @property
    def starting(self) -> bool:
        """进程刚起来、还在宽限期内（数据流检查尚未判失败），只是文件还没长出来。"""
        return (
            self.recorder.running
            and self.checks.dataflow is Check.OK
            and not self.watcher.grew_since_reset
        )

    # ---------- 心跳 ----------

    def build_heartbeat(self) -> Heartbeat:
        return Heartbeat(
            code=self.cfg.code,
            hostname=platform.node(),
            agent_version=__version__,
            sent_at=datetime.now().astimezone(),
            recording=self.recording,
            starting=self.starting,
            profile=self._encoding_label,
            camera_resolutions=self._camera_resolutions,
            current_segment=self.watcher.current_segment,
            checks=self.checks,
            problems=self.problems,
            restarts=self.recorder.restarts,
            pending_sync=self.index.pending_count(),
            disk_used=round(self.disk_used, 3),
            offline_since=self.offline_since,
            recent_logs=self.recent.drain(),
        )

    def send_heartbeat(self) -> None:
        hb = self.build_heartbeat()
        url = self.cfg.server.rstrip("/") + "/agent/v1/heartbeat"
        try:
            r = httpx.post(url, content=hb.model_dump_json(), timeout=5,
                           headers={"Content-Type": "application/json"})
            r.raise_for_status()
            HeartbeatReply.model_validate(r.json())  # 第一版：下发配置在此接入
            if self.online is False:
                log.info("已重新连上服务器（离线开始于 %s），开始补传", self.offline_since)
            self.online = True
            # 离线期间的积压补传完才算回到正常（设备墙上从黄色变绿色）
            if self.offline_since and hb.pending_sync <= 1:
                self.offline_since = None
        except (httpx.HTTPError, ValueError) as e:
            if self.online is not False:
                log.warning("连不上服务器，继续在本地录制：%s", e)
                self.offline_since = self.offline_since or datetime.now().astimezone()
            self.online = False
            # 没送出去的日志放回去，下次一起报
            self.recent.buffer.extendleft(reversed(hb.recent_logs))

    def maybe_heartbeat(self) -> None:
        if not self.cfg.server:
            return
        if time.monotonic() - self._last_hb < self.cfg.heartbeat_seconds:
            return
        if self._hb_thread and self._hb_thread.is_alive():
            return  # 上一次还没返回，不叠加
        self._last_hb = time.monotonic()
        self._hb_thread = threading.Thread(target=self.send_heartbeat, daemon=True)
        self._hb_thread.start()

    # ---------- 运行 ----------

    def tick(self) -> None:
        self.run_checks()
        self.archive_finished()
        self.enforce_local_quota()
        self.maybe_heartbeat()

    def run(self) -> None:
        log.info("EverRec Agent %s 启动，位置编号 %s，录像目录 %s，错峰偏移 %d 秒",
                 __version__, self.cfg.code, self.cfg.recordings_dir, self.cfg.stagger_seconds)
        child_guard.kill_orphans(spool_dir(self.cfg))  # 先放出摄像头，再处理上次留下的文件
        for w in self.cfg.config_warnings:
            log.warning("配置文件有误：%s", w)
        self._probe_camera()
        self.recover_spool()
        self.recorder.start()
        self.watcher.reset()
        try:
            while not self.stop_event.wait(TICK_SECONDS):
                try:
                    self.tick()
                except Exception:  # 主循环不能因为单次异常退出
                    log.exception("主循环异常")
        finally:
            self.recorder.stop()
            # 正常停止时最后一段也是完整的
            for seg in list_spool(spool_dir(self.cfg)):
                self._archive(seg)
            log.info("Agent 已停止")
