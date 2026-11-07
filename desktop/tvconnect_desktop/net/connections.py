"""Qt-facing connection objects for both modes. Signals are emitted from worker threads and delivered
queued to the GUI thread."""

from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..adb.adb_client import AdbClient, AdbError, AdbInput, find_adb
from ..adb.adb_video import AdbScreenStream
from ..devices.trust_store import TrustStore
from ..input.controller import InputRouter
from ..settings import Settings
from ..video.decoder import H264Decoder
from .session import NativeSession

log = logging.getLogger(__name__)


def fit_size(screen: tuple[int, int], max_size: tuple[int, int]) -> tuple[int, int]:
    sw, sh = screen
    mw, mh = max_size
    scale = min(1.0, mw / sw, mh / sh)
    return int(sw * scale) // 8 * 8, int(sh * scale) // 8 * 8


class BaseConnection(QObject):
    state = Signal(str, str)
    pairing = Signal(str, str)
    ready = Signal(dict)
    closed = Signal(str, bool)
    rtt = Signal(float)
    notice = Signal(str)
    video_status = Signal(dict)
    adb_status = Signal(str)
    clipboard_from_tv = Signal(str, str)  # text, source

    mode_name = ""
    MAX_CLIPBOARD = 65_536
    ADB_TEXT_CHUNK = 400

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.decoder = H264Decoder()
        self.router = InputRouter(self.notice.emit)
        self.router.mode = settings.input_mode
        self.title = ""
        self.screen = (1920, 1080)
        self._adb_input: AdbInput | None = None

    def start(self) -> None:
        raise NotImplementedError

    def apply_quality(self) -> None:
        raise NotImplementedError

    # ---- shared clipboard ----

    def paste_text(self, text: str) -> None:
        """Insert PC clipboard text into the focused field on the TV."""
        self._paste_via_adb(text)

    def push_clipboard(self, text: str) -> None:
        """Put text into the TV clipboard without inserting it (auto-sync)."""

    def request_tv_clipboard(self) -> None:
        self.notice.emit("Копирование с ТВ доступно только с TV-приложением (режим Native)")

    def _paste_via_adb(self, text: str) -> None:
        adb = self.router.adb
        if adb is None:
            self.notice.emit("Вставка недоступна: нет ни службы TVconnect на ТВ, ни ADB")
            return
        if not text.isascii():
            self.notice.emit("Через ADB можно вставить только латиницу/ASCII — для кириллицы нужно TV-приложение со службой")
            return
        text = text.replace("\r\n", "\n").replace("\t", " ")
        try:
            lines = text.split("\n")
            for i, line in enumerate(lines):
                for j in range(0, len(line), self.ADB_TEXT_CHUNK):
                    adb.text(line[j : j + self.ADB_TEXT_CHUNK])
                if i < len(lines) - 1:
                    adb.key(66)  # ENTER between lines
        except AdbError as e:
            self.notice.emit(f"Вставка через ADB: {e}")
            return
        self.notice.emit(f"Вставлено через ADB: {len(text)} символов")

    def close(self) -> None:
        if self._adb_input:
            self._adb_input.close()
            self._adb_input = None
            self.router.adb = None
        self.decoder.close()


class NativeConnection(BaseConnection):
    mode_name = "Native"

    def __init__(self, settings: Settings, trust: TrustStore, host: str, port: int) -> None:
        super().__init__(settings)
        self.host, self.port = host, port
        self.title = host
        self.session = NativeSession(host, port, settings.client_id, settings.client_name, trust, self)
        self.router.native_send = self.session.send_input
        self._pending_paste: str | None = None
        self.decoder.need_keyframe.connect(self.session.request_keyframe)

    def start(self) -> None:
        self.session.start()

    def apply_quality(self) -> None:
        if not self.session.ready:
            return
        w, h = self.settings.max_size()
        try:
            self.session.start_video(w, h, self.settings.fps, self.settings.bitrate_mbps * 1_000_000)
        except OSError as e:
            self.notice.emit(f"Не удалось отправить настройки: {e}")

    def _clipboard_native(self) -> bool:
        return self.session.ready and bool(self.router.caps.get("clipboard")) and self.router.mode != "adb"

    def paste_text(self, text: str) -> None:
        if not text:
            return
        if len(text) > self.MAX_CLIPBOARD:
            text = text[: self.MAX_CLIPBOARD]
            self.notice.emit("Текст обрезан до 64 КБ")
        if self._clipboard_native():
            try:
                self.session.send_control({"type": "clipboard_set", "text": text, "paste": True})
                self._pending_paste = text
            except OSError as e:
                self.notice.emit(f"Не удалось отправить буфер: {e}")
        else:
            self._paste_via_adb(text)

    def push_clipboard(self, text: str) -> None:
        if not text or len(text) > self.MAX_CLIPBOARD or not self._clipboard_native():
            return
        try:
            self.session.send_control({"type": "clipboard_set", "text": text, "paste": False})
        except OSError as e:
            log.warning("clipboard sync failed: %s", e)

    def request_tv_clipboard(self) -> None:
        if not self.session.ready:
            return
        try:
            self.session.send_control({"type": "clipboard_get"})
        except OSError as e:
            self.notice.emit(f"Не удалось запросить буфер ТВ: {e}")

    def _on_clipboard_result(self, msg: dict[str, Any]) -> None:
        if not msg.get("paste"):
            return
        pending, self._pending_paste = self._pending_paste, None
        if msg.get("pasted"):
            self.notice.emit("Вставлено на ТВ")
            return
        reason = msg.get("reason")
        if reason == "accessibility_disabled" or (reason == "failed" and self.router.adb and pending and pending.isascii()):
            # Fall back to typing via ADB when the field could not be filled natively.
            if pending is not None and self.router.adb is not None:
                self._paste_via_adb(pending)
                return
        hint = {
            "no_focus": "На ТВ нет поля ввода в фокусе — выберите поле",
            "accessibility_disabled": "Включите службу TVconnect на ТВ",
            "failed": "Поле на ТВ не принимает вставку",
        }.get(reason or "", str(reason))
        stored = " Текст уже в буфере ТВ — можно вставить через меню поля." if msg.get("clipboard") else ""
        self.notice.emit(hint + "." + stored)

    def close(self) -> None:
        self.session.close()
        super().close()

    # ---- SessionListener (worker thread) ----

    def on_state(self, state: str, detail: str) -> None:
        self.state.emit(state, detail)

    def on_pairing_code(self, sas: str, fp: str) -> None:
        self.pairing.emit(sas, fp)

    def on_ready(self, info: dict[str, Any]) -> None:
        self.title = info.get("name") or self.host
        self.screen = tuple(info.get("screen", self.screen))
        self.router.caps = dict(info.get("caps") or {})
        self.ready.emit(info)
        self.apply_quality()
        if self.settings.auto_adb_for_keys and self.settings.input_mode != "native":
            threading.Thread(target=self._attach_adb, name="adb-attach", daemon=True).start()

    def on_video(self, flags: int, pts_us: int, data: bytes) -> None:
        self.decoder.submit_packet(flags, data)

    def on_control(self, msg: dict[str, Any]) -> None:
        t = msg.get("type")
        if t == "video_status":
            if msg.get("state") == "running":
                self.decoder.reset()
            self.video_status.emit(msg)
        elif t == "caps":
            self.router.caps.update({k: v for k, v in msg.items() if k != "type"})
            self.notice.emit(
                "Служба управления TVconnect на ТВ: " + ("включена" if msg.get("accessibility") else "выключена")
            )
        elif t == "clipboard_result":
            self._on_clipboard_result(msg)
        elif t == "clipboard":
            text = msg.get("text")
            if isinstance(text, str) and text:
                self.clipboard_from_tv.emit(text, str(msg.get("source", "")))
            else:
                self.notice.emit("На ТВ нет текста для копирования: выделите текст или выберите поле")
        elif t == "input_result" and msg.get("reason") == "gesture_cancelled":
            self.router.on_gesture_cancelled()
        elif t == "input_result" and not msg.get("ok"):
            reason = msg.get("reason", "")
            hint = {
                "accessibility_disabled": "Включите службу «TVconnect» в Настройках ТВ → Спец. возможности",
                "unsupported": "Это действие не поддерживается без ADB",
                "no_focus": "На ТВ нет поля ввода в фокусе",
            }.get(reason, reason)
            self.notice.emit(f"ТВ: {hint}")
        elif t == "error":
            self.notice.emit(f"Ошибка ТВ: {msg.get('message', msg.get('code'))}")

    def on_rtt(self, ms: float) -> None:
        self.rtt.emit(ms)

    def on_closed(self, reason: str, error: bool) -> None:
        self.closed.emit(reason, error)

    def _attach_adb(self) -> None:
        path = find_adb(self.settings.adb_path)
        if not path:
            self.adb_status.emit("ADB: не найден")
            return
        try:
            client = AdbClient(path)
            serial = client.connect(self.host)
            try:
                screen = client.screen_size(serial)
            except AdbError:
                screen = self.screen
            self._adb_input = AdbInput(client, serial, screen)
            self.router.adb = self._adb_input
            self.adb_status.emit("ADB: подключён (клавиши)")
            log.info("ADB attached for input: %s", serial)
        except AdbError as e:
            log.info("ADB not available for %s: %s", self.host, e)
            self.adb_status.emit("ADB: недоступен")


class AdbConnection(BaseConnection):
    """ADB Mode: video via screenrecord, input via `adb shell input`. Does not need the TV app."""

    mode_name = "ADB"

    def __init__(self, settings: Settings, host: str, port: int = 5555) -> None:
        super().__init__(settings)
        self.host, self.port = host, port
        self.title = f"{host} (ADB)"
        self.router.mode = "adb"
        self._client: AdbClient | None = None
        self._serial = ""
        self._stream: AdbScreenStream | None = None
        self._closing = False

    def start(self) -> None:
        threading.Thread(target=self._run, name="adb-connect", daemon=True).start()

    def _run(self) -> None:
        self.state.emit("connecting", f"ADB {self.host}:{self.port}")
        try:
            path = find_adb(self.settings.adb_path)
            if not path:
                raise AdbError("adb не найден. Установите Android platform-tools или укажите путь в настройках.")
            self._client = AdbClient(path)
            self._serial = self._client.connect(self.host, self.port)
            self.screen = self._client.screen_size(self._serial)
            model = ""
            try:
                model = self._client.getprop(self._serial, "ro.product.model")
            except AdbError:
                pass
            self.title = f"{model or self.host} (ADB)"
            self._adb_input = AdbInput(self._client, self._serial, self.screen)
            self.router.adb = self._adb_input
        except AdbError as e:
            log.error("ADB connect failed: %s", e)
            self.closed.emit(str(e), True)
            return
        self.ready.emit({"name": self.title, "screen": self.screen, "caps": {}, "sdk": 0})
        self.state.emit("connected", self.title)
        self.adb_status.emit("ADB: подключён")
        self.apply_quality()

    def apply_quality(self) -> None:
        if self._client is None or self._closing:
            return
        if self._stream:
            self._stream.stop()
        size = fit_size(self.screen, self.settings.max_size())
        self.decoder.reset()
        self._stream = AdbScreenStream(
            self._client,
            self._serial,
            size,
            self.settings.bitrate_mbps * 1_000_000,
            on_chunk=self.decoder.submit_stream,
            on_restart=self.decoder.reset,
            on_error=lambda msg: self.notice.emit(msg),
        )
        self._stream.start()
        self.video_status.emit({"state": "running", "w": size[0], "h": size[1], "fps": 0})

    def close(self) -> None:
        self._closing = True
        if self._stream:
            self._stream.stop()
        super().close()
        self.closed.emit("отключено", False)
