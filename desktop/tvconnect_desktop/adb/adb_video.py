"""ADB Mode video: `screenrecord --output-format=h264` streamed over `adb exec-out`.

screenrecord stops after 180 s per run, so the stream is restarted transparently.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable

from .adb_client import AdbClient, validate_serial

log = logging.getLogger(__name__)


class AdbScreenStream:
    def __init__(
        self,
        client: AdbClient,
        serial: str,
        size: tuple[int, int],
        bitrate: int,
        on_chunk: Callable[[bytes], None],
        on_restart: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.client = client
        self.serial = validate_serial(serial)
        w, h = size
        self.size = (int(w) // 8 * 8, int(h) // 8 * 8)
        self.bitrate = max(1_000_000, min(int(bitrate), 40_000_000))
        self.on_chunk, self.on_restart, self.on_error = on_chunk, on_restart, on_error
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name="adb-video", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p and p.poll() is None:
            p.kill()

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            args = [
                "-s", self.serial, "exec-out", "screenrecord",
                "--output-format=h264",
                f"--bit-rate={self.bitrate}",
                f"--size={self.size[0]}x{self.size[1]}",
                "--time-limit=180",
                "-",
            ]
            started = time.monotonic()
            try:
                self._proc = self.client.popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            except OSError as e:
                self.on_error(f"не удалось запустить screenrecord: {e}")
                return
            got = 0
            assert self._proc.stdout is not None
            while not self._stop.is_set():
                chunk = self._proc.stdout.read1(65536)
                if not chunk:
                    break
                got += len(chunk)
                self.on_chunk(chunk)
            self._proc.wait()
            if self._stop.is_set():
                break
            ran = time.monotonic() - started
            log.info("screenrecord exited after %.1fs (%d bytes), restarting", ran, got)
            failures = failures + 1 if got == 0 else 0
            if failures >= 3:
                self.on_error("screenrecord не выдаёт видео (возможно, не поддерживается прошивкой)")
                return
            self.on_restart()
            time.sleep(0.3)
        log.info("ADB video stream stopped")
