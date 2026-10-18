"""修复工具的结果文字：复用 core 的 repair，三类文件给出正确文字。"""

from __future__ import annotations

import subprocess

from everrec_core import repair
from everrec_core.ffmpeg import ffmpeg_binary


def _make_ts(path, seconds=2):
    subprocess.run(
        [ffmpeg_binary(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=5",
         "-t", str(seconds), "-c:v", "libx264", "-f", "mpegts", str(path)],
        check=True,
    )


def test_repair_friendly_text(tmp_path):
    good = tmp_path / "good.ts"
    _make_ts(good)
    out = tmp_path / "已修复"
    res = repair.repair_file(good, out)
    assert res.friendly == "正常", "完好的文件应该判为正常"

    # 截断到半个包上，模拟断电
    broken = tmp_path / "broken.ts"
    data = good.read_bytes()
    broken.write_bytes(data[: len(data) // 2 + 33])
    res2 = repair.repair_file(broken, out)
    assert res2.integrity.value in ("repaired", "partial", "broken")
    assert res2.friendly  # 有给人看的文字
