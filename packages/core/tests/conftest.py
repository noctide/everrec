import subprocess
from pathlib import Path

import pytest

from everrec_core.config import AgentConfig, Camera, CameraType, Location, Recording
from everrec_core.ffmpeg import ffmpeg_binary


@pytest.fixture
def test_cfg(tmp_path: Path) -> AgentConfig:
    cfg = AgentConfig(
        location=Location(state="HD", district="PD", building="A", floor="3F", number="012"),
        camera=Camera(type=CameraType.TEST),
        recording=Recording(segment_seconds=2, watermark=True),
    )
    cfg.install_dir = tmp_path
    return cfg


@pytest.fixture
def sample_ts(tmp_path: Path) -> Path:
    """一段 4 秒的正常 TS 录像。"""
    out = tmp_path / "sample.ts"
    subprocess.run(
        [ffmpeg_binary(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=5",
         "-t", "4", "-c:v", "libx264", "-preset", "veryfast", "-g", "10", "-f", "mpegts", str(out)],
        check=True,
    )
    return out
