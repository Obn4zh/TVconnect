"""Desktop settings persisted as JSON in the user data dir."""

from __future__ import annotations

import json
import logging
import platform
import uuid
from dataclasses import asdict, dataclass, field, fields

from .paths import data_dir

log = logging.getLogger(__name__)

RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080), "540p": (960, 540)}
FPS_CHOICES = (15, 30, 60)
INPUT_MODES = ("auto", "native", "adb")


@dataclass
class Settings:
    client_id: str = ""
    client_name: str = ""
    adb_path: str = ""
    resolution: str = "720p"
    fps: int = 30
    bitrate_mbps: int = 6
    input_mode: str = "auto"
    wheel_as_dpad: bool = False
    auto_adb_for_keys: bool = True
    clipboard_sync: bool = True  # push the PC clipboard to the TV clipboard automatically
    manual_hosts: list[str] = field(default_factory=list)

    def max_size(self) -> tuple[int, int]:
        return RESOLUTIONS.get(self.resolution, RESOLUTIONS["720p"])

    def validate(self) -> None:
        if self.resolution not in RESOLUTIONS:
            self.resolution = "720p"
        if self.fps not in FPS_CHOICES:
            self.fps = 30
        self.bitrate_mbps = max(1, min(40, int(self.bitrate_mbps)))
        if self.input_mode not in INPUT_MODES:
            self.input_mode = "auto"
        if len(self.client_id) != 32:
            self.client_id = uuid.uuid4().hex
        if not self.client_name.strip():
            self.client_name = platform.node() or "TVconnect PC"
        self.client_name = self.client_name.strip()[:64]
        self.manual_hosts = [h for h in self.manual_hosts if isinstance(h, str)][:50]


def _path():
    return data_dir() / "settings.json"


def load_settings() -> Settings:
    s = Settings()
    p = _path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            known = {f.name for f in fields(Settings)}
            for k, v in raw.items():
                if k in known:
                    setattr(s, k, v)
        except (OSError, ValueError) as e:
            log.warning("Cannot read settings (%s), using defaults", e)
    s.validate()
    save_settings(s)
    return s


def save_settings(s: Settings) -> None:
    try:
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_path())
    except OSError as e:
        log.error("Cannot save settings: %s", e)
