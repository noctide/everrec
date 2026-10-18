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

import json
import logging
import platform
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from everrec_core import repair
from everrec_core.config import AgentConfig, load_config
from everrec_core.ffmpeg import camera_device, effective_encoding, list_camera_modes, spool_dir
from everrec_core.health import Check, GrowthWatcher, storage_check
from everrec_core.protocol import Checks, Heartbeat, HeartbeatReply
from everrec_core.segments import archive_segment, enforce_quota, list_spool

from . import __version__, child_guard, updater
from .index import IndexEntry, SegmentIndex
from .logs import RecentProblems
from .recorder import Recorder
from .uploader import Uploader

log = logging.getLogger("agent")

TICK_SECONDS = 2
UPDATE_CHECK_SECONDS = 3600  # 每小时问一次服务器是否该更新；真正装不装由服务端按时段判定

THUMBNAIL_SECONDS = 60  # 缩略图每分钟发一张（设计文档 6.2）


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
        self._last_update_check = 0.0
        self._update_thread: threading.Thread | None = None

        self._last_thumb = 0.0
        self._thumb_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._encoding_label = cfg.recording.encoding.label
        self._encoding_note: str | None = None
        self._camera_resolutions: list[str] = []
        self.uploader = Uploader(cfg, self.index) if cfg.server else None
        self.agent_token = self._load_agent_token()
        if self.uploader and self.agent_token:
            self.uploader.set_token(self.agent_token)
        self._config_version = 0
        self._server_settings: dict | None = None  # 待应用的下发配置
        self._load_cached_server_config()
        # 本地配置文件改动检测（WP8）：桌面端改设置后，下一分段边界重载
        self._config_mtime = (
            cfg.config_path.stat().st_mtime if cfg.config_path and cfg.config_path.exists() else 0.0
        )
        self._config_dirty = False

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
            self._archive(seg, res.integrity, "backfill")
            return
        log.warning("分段 %s：%s（方法 %s）", seg.path.name, res.friendly, res.method)
        quarantine.mkdir(parents=True, exist_ok=True)
        seg.path.replace(quarantine / seg.path.name)  # 原文件进隔离目录，不删除
        if res.output:
            fixed = type(seg)(res.output, seg.code, seg.started_at)
            self._archive(fixed, res.integrity, "backfill")

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

    # ---------- 服务器下发配置 ----------

    def _load_agent_token(self) -> str | None:
        """读设备令牌（tasks.md WP5）；没有则返回 None，等首次心跳签发。"""
        path = self.cfg.state_dir / "agent_token"
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _save_agent_token(self, token: str) -> None:
        path = self.cfg.state_dir / "agent_token"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(token, encoding="utf-8")
        tmp.replace(path)

    def _load_cached_server_config(self) -> None:
        """启动时读缓存的服务器配置：离线也沿用最后一次收到的档位。"""
        path = self.cfg.state_dir / "server_config.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("settings"):
                self._server_settings = data["settings"]
                self._config_version = int(data.get("config_version", 0))
        except (OSError, ValueError):
            log.warning("读取缓存的服务器配置失败，沿用本地配置")

    def _cache_server_config(self, reply: HeartbeatReply) -> None:
        """把下发的配置原子写入 state/server_config.json。"""
        path = self.cfg.state_dir / "server_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        data = {"config_version": reply.config_version, "settings": reply.settings}
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _apply_to_cfg(self, settings: dict) -> None:
        """把下发的设置套到本地配置（不重启录制）。"""
        rec = self.cfg.recording
        for key, value in settings.items():
            if key == "quality":
                rec.quality = value
            elif key == "resolution":
                rec.resolution = value
            elif key == "fps":
                rec.fps = value
            elif key == "watermark":
                rec.watermark = value
            elif key == "segment_seconds":
                rec.segment_seconds = value
            elif key == "local_quota_gb":
                rec.local_quota_gb = value
        if self.uploader and ("upload_realtime_bps" in settings or "upload_backfill_bps" in settings):
            self.uploader.set_rates(
                settings.get("upload_realtime_bps", 1_000_000),
                settings.get("upload_backfill_bps", 4_000_000),
            )
        self._probe_camera()  # 刷新画质标签与"摄像头达不到该分辨率"的提示

    def write_status(self) -> None:
        """每轮把状态原子写入 state/status.json，供桌面端托盘/客户端只读显示（WP8）。

        内容 = 心跳字段 + online；不含日志（界面从日志目录读）。
        """
        hb = Heartbeat(
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
        )
        data = hb.model_dump(mode="json")
        data["online"] = bool(self.online)
        path = self.cfg.state_dir / "status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # 原子替换，避免界面读到写一半的文件

    def _check_config_change(self) -> None:
        """配置文件被改动就标记，等下一个分段边界再应用（设计 4.6、WP8）。"""
        p = self.cfg.config_path
        if not p or not p.exists():
            return
        mtime = p.stat().st_mtime
        if mtime != self._config_mtime:
            self._config_mtime = mtime
            self._config_dirty = True

    def _reload_local_config(self) -> bool:
        """重载本地配置文件。录像目录是本地设置不热切换；服务器地址变化时切换上传目标（WP8）。"""
        try:
            new_cfg = load_config(self.cfg.config_path)
        except Exception as e:
            log.warning("重载配置失败，沿用旧配置：%s", e)
            return False
        old_server = self.cfg.server
        folder = self.cfg.recording.folder  # 录像目录是本地设置，不跟着配置热切换
        self.cfg.recording = new_cfg.recording
        self.cfg.recording.folder = folder
        self.cfg.camera = new_cfg.camera
        self.cfg.server = new_cfg.server
        self.cfg.location = new_cfg.location
        self.cfg.config_warnings = new_cfg.config_warnings
        if self.cfg.server != old_server:
            self.online = None
            self.offline_since = datetime.now().astimezone() if self.cfg.server else None
            self._last_hb = 0.0  # 立刻重新向新服务器心跳
            if self.uploader:
                self.uploader.set_server(self.cfg.server)
            elif self.cfg.server:
                self.uploader = Uploader(self.cfg, self.index)
                self.uploader.start()
        self._encoding_label = self.cfg.recording.encoding.label
        for w in self.cfg.config_warnings:
            log.warning("配置文件有误：%s", w)
        self._probe_camera()
        log.info("已重载本地配置文件")
        return True

    def _apply_pending_config(self) -> None:
        """分段边界应用新配置：先重载本地配置文件，再叠服务端下发（画质类以服务端为准），最后重启一次录制。"""
        applied = False
        if self._config_dirty:
            self._config_dirty = False
            applied = self._reload_local_config() or applied
        if self._server_settings is not None:
            self._apply_to_cfg(self._server_settings)  # 服务端设置叠在本地之上，画质类以服务端为准
            log.info("应用服务器下发配置：%s", self._server_settings)
            self._server_settings = None
            applied = True
        if applied:
            self.recorder.restart("配置已更新，按新档位录制")

    # ---------- 每一轮 ----------

    def _archive(self, seg, integrity: str = "ok", channel: str = "realtime") -> None:
        dest = archive_segment(self.cfg.recordings_dir, seg)
        entry = IndexEntry(str(dest), seg.started_at.isoformat(), dest.stat().st_size, str(integrity))
        self.index.add(entry)
        log.info("分段已归档：%s", dest.relative_to(self.cfg.recordings_dir))
        if self.uploader:
            if self.online is False:
                channel = "backfill"  # 断网期间积压走补传；恢复后的新分段仍走实时
            self.uploader.enqueue(entry, channel)

    def archive_finished(self) -> int:
        segs = list_spool(spool_dir(self.cfg))
        finished = segs[:-1] if self.recorder.running else []
        for seg in finished:
            self._archive(seg)
        return len(finished)

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
        server = self.cfg.server
        url = server.rstrip("/") + "/agent/v1/heartbeat"
        headers = {"Content-Type": "application/json"}
        if self.agent_token:
            headers["X-Agent-Token"] = self.agent_token
        try:
            r = httpx.post(url, content=hb.model_dump_json(), timeout=5, headers=headers)
            r.raise_for_status()
            reply = HeartbeatReply.model_validate(r.json())
            if server != self.cfg.server:
                return  # 旧服务器的回复不能覆盖切换后的连接状态
            if self.online is False:
                log.info("已重新连上服务器（离线开始于 %s），开始补传", self.offline_since)
            self.online = True
            # 首次心跳签发设备令牌：存下来，之后所有 /agent/v1/* 都带上（tasks.md WP5）
            if reply.token and reply.token != self.agent_token:
                self.agent_token = reply.token
                self._save_agent_token(reply.token)
                if self.uploader:
                    self.uploader.set_token(reply.token)
            # 配置版本变了就缓存并在下一分段边界应用（设计文档 7、tasks.md WP4）
            if reply.settings and reply.config_version != self._config_version:
                self._cache_server_config(reply)
                self._config_version = reply.config_version
                self._server_settings = reply.settings
            # 离线期间的积压补传完才算回到正常（设备墙上从黄色变绿色）
            if self.offline_since and hb.pending_sync <= 1:
                self.offline_since = None
        except (httpx.HTTPError, ValueError) as e:
            if server != self.cfg.server:
                return
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

    def maybe_update(self) -> None:
        """后台检查更新（WP9）。装不装由服务端按时段判定；这里只拉起后台线程，不阻塞录制。"""
        if not self.cfg.server or not self.agent_token:
            return  # 没配服务器或还没拿到设备令牌，跳过
        if time.monotonic() - self._last_update_check < UPDATE_CHECK_SECONDS:
            return
        if self._update_thread and self._update_thread.is_alive():
            return  # 上一次还没结束，不叠加
        self._last_update_check = time.monotonic()
        self._update_thread = threading.Thread(target=self._run_update, daemon=True)
        self._update_thread.start()

    def _run_update(self) -> None:
        try:
            action = updater.run_update(self.cfg.server, self.cfg.code, self.agent_token, __version__)
            if action == "installed":
                log.info("已安装新版本，安装器将接管并重启服务")
            elif action == "notify":
                log.info("发现新版本，等待自动更新时段")
        except Exception:  # 更新绝不能影响录制
            log.exception("更新检查异常（继续录制）")

    def send_thumbnail(self) -> None:
        """把最新缩略图发给服务器（小数据，不受补传排队影响）。"""
        thumb = self.cfg.state_dir / "thumb.jpg"
        if not thumb.exists():
            return
        url = f"{self.cfg.server.rstrip('/')}/agent/v1/thumbnail?code={self.cfg.code}"
        try:
            with thumb.open("rb") as f:
                httpx.put(url, content=f.read(), timeout=5,
                          headers={"Content-Type": "image/jpeg",
                                   "X-Agent-Token": self.agent_token or ""})
        except httpx.HTTPError:
            pass  # 缩略图发不出去无所谓，下一分钟再试

    def maybe_thumbnail(self) -> None:
        if not self.cfg.server:
            return
        if time.monotonic() - self._last_thumb < THUMBNAIL_SECONDS:
            return
        if self._thumb_thread and self._thumb_thread.is_alive():
            return
        self._last_thumb = time.monotonic()
        self._thumb_thread = threading.Thread(target=self.send_thumbnail, daemon=True)
        self._thumb_thread.start()

    # ---------- 运行 ----------

    def tick(self) -> None:
        self._check_config_change()  # 检测本地配置文件改动（WP8）
        self.run_checks()
        archived = self.archive_finished()
        # 分段边界：应用本地配置文件改动 + 服务端下发配置（WP8 + WP4；画质类以服务端为准）
        if archived > 0 and (self._config_dirty or self._server_settings is not None):
            self._apply_pending_config()
        self.enforce_local_quota()
        self.write_status()  # 每轮写状态文件给桌面端（WP8）
        self.maybe_heartbeat()
        self.maybe_update()  # 后台检查更新（WP9），不阻塞录制

        self.maybe_thumbnail()

    def run(self) -> None:
        log.info("EverRec Agent %s 启动，位置编号 %s，录像目录 %s，错峰偏移 %d 秒",
                 __version__, self.cfg.code, self.cfg.recordings_dir, self.cfg.stagger_seconds)
        child_guard.kill_orphans(spool_dir(self.cfg))  # 先放出摄像头，再处理上次留下的文件
        for w in self.cfg.config_warnings:
            log.warning("配置文件有误：%s", w)
        self._probe_camera()
        # 启动时先套用缓存的服务器配置（离线也沿用最后一次收到的档位），再开始录
        if self._server_settings:
            self._apply_to_cfg(self._server_settings)
            self._server_settings = None
            log.info("启动时套用缓存的服务器配置")
        self.recover_spool()
        self.recorder.start()
        self.watcher.reset()
        try:
            if self.uploader:
                self.uploader.start()  # 上传恢复失败不能阻止本地录制启动
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
            if self.uploader:
                self.uploader.stop()
            log.info("Agent 已停止")
