"""真跑 FFmpeg：用测试画面录几秒，验证分段、格式和可解码。"""

import subprocess
import time

from everrec_core.ffmpeg import build_record_command, spool_dir
from everrec_core.health import Check, GrowthWatcher
from everrec_core.repair import inspect
from everrec_core.segments import list_spool


def test_records_segments_that_decode(test_cfg):
    spool_dir(test_cfg).mkdir(parents=True)
    p = subprocess.Popen(build_record_command(test_cfg), stderr=subprocess.PIPE, text=True)
    watcher = GrowthWatcher(spool_dir(test_cfg), stall_seconds=3)
    try:
        time.sleep(3)
        assert watcher.check()[0] is Check.OK
        time.sleep(4)
    finally:
        p.terminate()
        p.wait(10)

    segs = list_spool(spool_dir(test_cfg))
    assert len(segs) >= 2, p.stderr.read() if p.stderr else ""
    assert all(s.code == "HD-PD-A-3F-012" for s in segs)
    finished = segs[-2]  # 倒数第二段一定是完整写完的
    result = inspect(finished.path)
    assert result.healthy, result.detail
    assert 0.5 < (result.duration or 0) <= 3


def test_current_segment_grows_before_it_is_cut(test_cfg):
    """回归：分段写到一半时文件就得在变大，否则卡住要等到切段才发现。"""
    test_cfg.recording.segment_seconds = 60
    spool_dir(test_cfg).mkdir(parents=True)
    p = subprocess.Popen(build_record_command(test_cfg), stderr=subprocess.DEVNULL)
    try:
        sizes = []
        for _ in range(3):
            time.sleep(2.5)
            segs = list_spool(spool_dir(test_cfg))
            sizes.append(segs[-1].path.stat().st_size if segs else 0)
    finally:
        p.terminate()
        p.wait(10)
    # 头一两秒编码器还在起步，可能是 0；之后必须持续变大
    assert 0 < sizes[1] < sizes[2], sizes


def test_watcher_detects_stall(tmp_path):
    spool = tmp_path / "_spool"
    spool.mkdir()
    f = spool / "X_20261015_090000.ts"
    f.write_bytes(b"\0" * 100)
    w = GrowthWatcher(spool, stall_seconds=30)
    assert w.check(now=0)[0] is Check.OK
    assert w.check(now=20)[0] is Check.OK
    status, msg = w.check(now=31)
    assert status is Check.FAIL and "无增长" in msg
    f.write_bytes(b"\0" * 200)  # 又开始长了
    assert w.check(now=32)[0] is Check.OK
