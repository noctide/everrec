from datetime import datetime
from pathlib import Path

from everrec_core.repair import Integrity, repair_file
from everrec_core.segments import (
    SpoolSegment,
    archive_path,
    enforce_quota,
    parse_spool_name,
)


def test_healthy_file_is_left_alone(sample_ts, tmp_path):
    res = repair_file(sample_ts, tmp_path / "fixed")
    assert res.integrity is Integrity.OK
    assert res.output == sample_ts


def test_truncated_with_garbage_is_repaired(sample_ts, tmp_path):
    data = sample_ts.read_bytes()
    broken = tmp_path / "broken.ts"
    # 模拟断电：头上有残包、尾巴截在半个包上
    broken.write_bytes(b"\x13\x37" * 50 + data[: len(data) * 2 // 3 + 77])
    original = broken.read_bytes()

    res = repair_file(broken, tmp_path / "fixed", expected_sec=4)
    assert res.integrity in (Integrity.REPAIRED, Integrity.PARTIAL)
    assert res.output and res.output.exists()
    assert broken.read_bytes() == original, "原文件不能被改动"
    assert "可以正常播放" in res.friendly or "部分恢复" in res.friendly


def test_garbage_is_broken(tmp_path):
    junk = tmp_path / "junk.ts"
    junk.write_bytes(b"not a video" * 1000)
    res = repair_file(junk, tmp_path / "fixed")
    assert res.integrity is Integrity.BROKEN
    assert res.friendly.startswith("无法修复")


def test_archive_path_template(tmp_path):
    seg = parse_spool_name(Path("HD-PD-A-3F-012_20261015_091000.ts"))
    assert seg is not None and seg.code == "HD-PD-A-3F-012"
    assert archive_path(tmp_path, seg) == (
        tmp_path / "HD-PD-A-3F-012" / "2026" / "10" / "15" / "HD-PD-A-3F-012_091000.ts"
    )


def test_quota_deletes_oldest_and_respects_filter(tmp_path):
    paths = []
    for minute in range(5):
        seg = SpoolSegment(Path("x"), "C", datetime(2026, 10, 15, 9, minute))
        p = archive_path(tmp_path, seg)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * 100)
        paths.append(p)

    keep_first = paths[0]
    deleted = enforce_quota(tmp_path, 300, can_delete=lambda p: p != keep_first)
    assert deleted == paths[1:3]  # 最旧的不让删（比如还没同步），就跳过去删后面的
    assert keep_first.exists() and paths[3].exists() and paths[4].exists()
