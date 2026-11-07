"""H.264 decoding (FFmpeg via PyAV) on a worker thread; emits QImage frames for the viewer."""

from __future__ import annotations

import logging
import queue
import threading
import time

import av
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ..protocol.constants import VF_CONFIG, VF_KEY

log = logging.getLogger(__name__)

MAX_BACKLOG = 6  # queued packets before we drop to the next keyframe
RAW_FLUSH_S = 0.03  # silence on a raw ADB stream after which the parser is flushed


class H264Decoder(QObject):
    frame_ready = Signal(QImage)
    need_keyframe = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._q: queue.Queue[tuple[str, int, bytes] | None] = queue.Queue()
        self._config = b""
        self._waiting_key = True
        self._running = True
        self.frames_decoded = 0
        self.frames_dropped = 0
        self.last_decode_ms = 0.0
        self.size = (0, 0)
        self._thread = threading.Thread(target=self._run, name="h264-decoder", daemon=True)
        self._thread.start()

    # Called from network threads.
    def submit_packet(self, flags: int, data: bytes) -> None:
        if flags & VF_CONFIG:
            self._config = data
            return
        self._q.put(("pkt", flags, data))

    def submit_stream(self, chunk: bytes) -> None:
        """Raw Annex-B byte stream (ADB screenrecord); split into packets by the FFmpeg parser."""
        self._q.put(("raw", 0, chunk))

    def reset(self) -> None:
        self._q.put(("reset", 0, b""))

    def close(self) -> None:
        self._running = False
        self._q.put(None)

    def _new_ctx(self):
        ctx = av.CodecContext.create("h264", "r")
        ctx.options = {"flags": "low_delay"}
        ctx.thread_type = "SLICE"
        ctx.thread_count = 2
        return ctx

    def _run(self) -> None:
        ctx = self._new_ctx()
        last_key_req = 0.0
        raw_pending = False
        while self._running:
            try:
                # The Annex-B parser only emits a frame when the next one starts. screenrecord sends
                # nothing while the screen is static, so flush the parser after a short silence.
                item = self._q.get(timeout=RAW_FLUSH_S) if raw_pending else self._q.get()
            except queue.Empty:
                raw_pending = False
                item = ("flush", 0, b"")
            if item is None:
                break
            kind, flags, data = item
            if kind == "flush":
                try:
                    for pkt in ctx.parse(None):
                        for frame in ctx.decode(pkt):
                            self._emit(frame)
                except av.error.FFmpegError as e:
                    log.debug("flush decode error: %s", e)
                continue
            raw_pending = kind == "raw"
            if kind == "reset":
                ctx = self._new_ctx()
                self._waiting_key = True
                continue
            try:
                if kind == "raw":
                    packets = ctx.parse(data)
                else:
                    if self._q.qsize() > MAX_BACKLOG and not (flags & VF_KEY):
                        # Decoder is behind: drop until the next keyframe to cut latency.
                        self.frames_dropped += 1
                        self._waiting_key = True
                        if time.monotonic() - last_key_req > 1.0:
                            last_key_req = time.monotonic()
                            self.need_keyframe.emit()
                        continue
                    if self._waiting_key and not (flags & VF_KEY):
                        self.frames_dropped += 1
                        if time.monotonic() - last_key_req > 1.0:
                            last_key_req = time.monotonic()
                            self.need_keyframe.emit()
                        continue
                    if flags & VF_KEY:
                        self._waiting_key = False
                        data = self._config + data
                    packets = [av.Packet(data)]
                for pkt in packets:
                    t0 = time.perf_counter()
                    for frame in ctx.decode(pkt):
                        self._emit(frame)
                    self.last_decode_ms = (time.perf_counter() - t0) * 1000.0
            except av.error.FFmpegError as e:
                log.warning("Decode error: %s", e)
                self._waiting_key = True
                if time.monotonic() - last_key_req > 1.0:
                    last_key_req = time.monotonic()
                    self.need_keyframe.emit()
            except Exception:
                log.exception("Decoder failure")
        log.info("Decoder stopped")

    def _emit(self, frame) -> None:
        arr = frame.to_ndarray(format="rgb24")
        h, w = arr.shape[0], arr.shape[1]
        img = QImage(arr.data, w, h, arr.strides[0], QImage.Format.Format_RGB888).copy()
        self.size = (w, h)
        self.frames_decoded += 1
        self.frame_ready.emit(img)
