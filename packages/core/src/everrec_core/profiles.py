"""码率档位（设计文档 4.4）。只能在管理网站切换，不开放自定义码率。"""

from dataclasses import dataclass
from enum import StrEnum


class ProfileName(StrEnum):
    XLOW = "xlow"
    LOW = "low"
    MID = "mid"


@dataclass(frozen=True)
class Profile:
    name: ProfileName
    width: int
    height: int
    fps: int
    maxrate_kbps: int
    crf: int = 30

    @property
    def bufsize_kbps(self) -> int:
        return self.maxrate_kbps * 2


PROFILES: dict[ProfileName, Profile] = {
    ProfileName.XLOW: Profile(ProfileName.XLOW, 640, 360, 5, 64),
    ProfileName.LOW: Profile(ProfileName.LOW, 854, 480, 8, 150),
    ProfileName.MID: Profile(ProfileName.MID, 1280, 720, 10, 500),
}


def get_profile(name: str | ProfileName) -> Profile:
    return PROFILES[ProfileName(name)]
