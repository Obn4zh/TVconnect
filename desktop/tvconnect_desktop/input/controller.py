"""Routes high-level input actions to the Native agent (Accessibility) or the ADB backend."""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..adb.adb_client import AdbError, AdbInput, UnsupportedText
from .keymap import KEYCODE

log = logging.getLogger(__name__)

# Keys the native agent can always handle (AudioManager / global actions).
NATIVE_ALWAYS = {KEYCODE["BACK"], KEYCODE["HOME"], KEYCODE["APP_SWITCH"], KEYCODE["VOLUME_UP"],
                 KEYCODE["VOLUME_DOWN"], KEYCODE["VOLUME_MUTE"]}
NATIVE_DPAD = {KEYCODE["DPAD_UP"], KEYCODE["DPAD_DOWN"], KEYCODE["DPAD_LEFT"], KEYCODE["DPAD_RIGHT"],
               KEYCODE["DPAD_CENTER"], KEYCODE["ENTER"], KEYCODE["DEL"]}


class InputRouter:
    def __init__(self, notify: Callable[[str], None]) -> None:
        self.mode = "auto"  # auto | native | adb
        self.native_send: Callable[[dict[str, Any]], bool] | None = None
        self.caps: dict[str, Any] = {}
        self.adb: AdbInput | None = None
        self.notify = notify
        # Some firmwares cancel every accessibility gesture; then pointer input moves to ADB.
        self.native_gestures_ok = True
        self._last_pointer_adb: Callable[[AdbInput], None] | None = None

    def on_gesture_cancelled(self) -> None:
        if not self.native_gestures_ok:
            return
        self.native_gestures_ok = False
        if self.adb is not None:
            self.notify("ТВ отклоняет жесты Accessibility — мышь переключена на ADB")
            replay = self._last_pointer_adb
            if replay is not None:
                try:
                    replay(self.adb)
                except AdbError as e:
                    self.notify(str(e))
        else:
            self.notify("ТВ отклоняет жесты Accessibility — для мыши включите ADB (Отладка по сети)")

    # ---- backend selection ----

    def _native_ok(self, what: str) -> bool:
        if self.native_send is None:
            return False
        if what == "volume":
            return True
        if not self.caps.get("accessibility"):
            return False
        if what == "dpad":
            return bool(self.caps.get("dpad_native") or self.caps.get("dpad_fallback"))
        if what == "pointer":
            return self.native_gestures_ok
        return True

    def _pick(self, what: str) -> str | None:
        if self.mode == "native":
            return "native" if self.native_send else None
        if self.mode == "adb":
            return "adb" if self.adb else None
        if what in ("key", "dpad") and self.adb:
            return "adb"  # ADB delivers real KeyEvents, preferred for keys in auto mode
        if self._native_ok(what):
            return "native"
        return "adb" if self.adb else None

    def _unavailable(self, what: str) -> None:
        if self.native_send and not self.caps.get("accessibility") and not self.adb:
            self.notify("Включите службу «TVconnect» в Спец. возможностях ТВ или подключите ADB")
        else:
            self.notify(f"Нет доступного способа отправить: {what}")

    # ---- actions ----

    def key(self, code: int) -> None:
        what = "volume" if code in (24, 25, 164) else ("dpad" if code in NATIVE_DPAD else "key")
        if code in NATIVE_ALWAYS and what != "volume":
            what = "global"
        backend = self._pick(what)
        if backend == "native" and what == "key" and code not in NATIVE_ALWAYS and code not in NATIVE_DPAD:
            backend = "adb" if self.adb else None
        if backend == "native":
            self.native_send({"t": "key", "code": int(code)})
        elif backend == "adb":
            try:
                self.adb.key(code)
            except AdbError as e:
                self.notify(str(e))
        else:
            self._unavailable(f"клавиша {code}")

    def text(self, s: str) -> None:
        if not s:
            return
        backend = self._pick("text")
        if backend == "native":
            self.native_send({"t": "text", "s": s})
            return
        if backend == "adb":
            try:
                self.adb.text(s)
            except UnsupportedText:
                if self._native_ok("text"):
                    self.native_send({"t": "text", "s": s})
                else:
                    self.notify("Ввод не-ASCII текста через ADB невозможен — включите службу TVconnect на ТВ")
            except AdbError as e:
                self.notify(str(e))
            return
        self._unavailable("текст")

    def tap(self, x: float, y: float) -> None:
        self._pointer({"t": "tap", "x": x, "y": y}, lambda a: a.tap(x, y))

    def long_press(self, x: float, y: float) -> None:
        self._pointer({"t": "long_press", "x": x, "y": y}, lambda a: a.long_press(x, y))

    def swipe(self, x1: float, y1: float, x2: float, y2: float, ms: int) -> None:
        self._pointer(
            {"t": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "ms": int(ms)},
            lambda a: a.swipe(x1, y1, x2, y2, ms),
        )

    def scroll(self, x: float, y: float, notches: int) -> None:
        if notches == 0:
            return
        dist = max(-0.6, min(0.6, 0.2 * notches))  # wheel up (+) -> finger moves down
        y1 = min(max(y - dist / 2, 0.05), 0.95)
        y2 = min(max(y + dist / 2, 0.05), 0.95)
        self._pointer(
            {"t": "scroll", "x": x, "y": y, "dy": int(notches)},
            lambda a: a.swipe(x, y1, x, y2, 250),
        )

    def cursor(self, x: float, y: float) -> None:
        if self._native_ok("pointer") and self.mode != "adb":
            self.native_send({"t": "cursor", "x": round(x, 4), "y": round(y, 4)})

    def _pointer(self, msg: dict[str, Any], adb_call: Callable[[AdbInput], None]) -> None:
        backend = self._pick("pointer")
        if backend == "native" and self.mode == "native" and not self.native_gestures_ok:
            self.notify("Жесты на этом ТВ не работают через Accessibility — выберите режим ввода auto или adb")
        if backend == "native":
            self._last_pointer_adb = adb_call
            self.native_send(msg)
        elif backend == "adb":
            try:
                adb_call(self.adb)
            except AdbError as e:
                self.notify(str(e))
        else:
            self._unavailable("действие мышью")
