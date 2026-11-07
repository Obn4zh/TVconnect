"""Trusted TVs. Metadata + identity-key pins in devices.json, trust tokens in the OS keyring only."""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass

from ..paths import data_dir

log = logging.getLogger(__name__)
KEYRING_SERVICE = "TVconnect"


@dataclass
class TrustedTv:
    tv_id: str
    name: str
    host: str
    port: int
    fingerprint: str
    paired_at: float


class TrustStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path = data_dir() / "devices.json"
        self._devices: dict[str, TrustedTv] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for tv_id, d in raw.get("devices", {}).items():
                self._devices[tv_id] = TrustedTv(**d)
        except (OSError, ValueError, TypeError) as e:
            log.error("Cannot read %s: %s", self._path, e)

    def _save(self) -> None:
        data = {"devices": {k: asdict(v) for k, v in self._devices.items()}}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def all(self) -> list[TrustedTv]:
        with self._lock:
            return list(self._devices.values())

    def get(self, tv_id: str) -> TrustedTv | None:
        with self._lock:
            return self._devices.get(tv_id)

    def remember(self, tv_id: str, name: str, host: str, port: int, fingerprint: str, token: bytes) -> bool:
        """Stores pin + token. Returns False if the token could not be stored securely."""
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, tv_id, base64.b64encode(token).decode())
        except Exception as e:  # keyring backends raise many different types
            log.error("OS keyring unavailable, the TV will ask for confirmation next time: %s", e)
            return False
        with self._lock:
            self._devices[tv_id] = TrustedTv(tv_id, name, host, port, fingerprint, time.time())
            self._save()
        return True

    def update_address(self, tv_id: str, host: str, port: int, name: str) -> None:
        with self._lock:
            d = self._devices.get(tv_id)
            if d and (d.host, d.port, d.name) != (host, port, name):
                d.host, d.port, d.name = host, port, name
                self._save()

    def token(self, tv_id: str) -> bytes | None:
        try:
            import keyring

            v = keyring.get_password(KEYRING_SERVICE, tv_id)
        except Exception as e:
            log.warning("OS keyring read failed: %s", e)
            return None
        if not v:
            return None
        try:
            return base64.b64decode(v)
        except ValueError:
            return None

    def forget(self, tv_id: str) -> None:
        try:
            import keyring

            keyring.delete_password(KEYRING_SERVICE, tv_id)
        except Exception as e:
            log.debug("keyring delete: %s", e)
        with self._lock:
            if self._devices.pop(tv_id, None) is not None:
                self._save()
