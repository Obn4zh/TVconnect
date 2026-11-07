"""Controlled ADB API. Only fixed command templates with validated arguments are ever executed.

No user-provided string reaches a shell unvalidated: keycodes come from an allow-list, coordinates are
integers clamped to the screen, text is restricted to printable ASCII and single-quoted.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from ..input.keymap import ALLOWED_KEYCODES
from ..protocol.constants import ADB_DEFAULT_PORT

log = logging.getLogger(__name__)

SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class AdbError(Exception):
    pass


class UnsupportedText(AdbError):
    pass


def bundled_dirs() -> list[Path]:
    """Folders of a PyInstaller build (_internal and the exe folder); empty when run from source."""
    if not getattr(sys, "frozen", False):
        return []
    dirs = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.insert(0, Path(meipass))
    return dirs


def find_adb(custom: str = "") -> str | None:
    candidates: list[str] = []
    if custom:
        candidates.append(custom)
    w = shutil.which("adb")
    if w:
        candidates.append(w)
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env):
            candidates.append(str(Path(os.environ[env]) / "platform-tools" / exe))
    if sys.platform == "win32":
        candidates += [
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / exe),
            r"C:\AndroidSDK\platform-tools\adb.exe",
            r"C:\platform-tools\adb.exe",
        ]
    else:
        candidates += [str(Path.home() / "Android" / "Sdk" / "platform-tools" / exe), "/usr/bin/adb"]
    # adb shipped inside the packaged app — last, so an adb already installed on the PC wins
    # (two different adb versions on one machine keep killing each other's server).
    candidates += [str(d / "adb" / exe) for d in bundled_dirs()]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def validate_host(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host.strip()))
    except ValueError:
        pass
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,252}", host.strip()):
        return host.strip()
    raise AdbError(f"invalid host: {host!r}")


def validate_serial(serial: str) -> str:
    if not SERIAL_RE.fullmatch(serial):
        raise AdbError(f"invalid adb serial: {serial!r}")
    return serial


def quote_input_text(text: str) -> str:
    """Builds the argument for `input text`. Raises UnsupportedText for non-ASCII input."""
    if not text:
        raise UnsupportedText("empty text")
    for ch in text:
        if not (0x20 <= ord(ch) <= 0x7E):
            raise UnsupportedText("ADB `input text` supports only printable ASCII")
    # `input text` treats %s as space; real spaces are encoded that way for compatibility.
    escaped = text.replace("'", "'\\''").replace(" ", "%s")
    return f"'{escaped}'"


class AdbClient:
    def __init__(self, adb_path: str) -> None:
        if not adb_path or not Path(adb_path).is_file():
            raise AdbError("adb не найден. Укажите путь к adb в настройках (Android platform-tools).")
        self.adb = adb_path

    def run(self, args: list[str], timeout: float = 10.0) -> str:
        cmd = [self.adb, *args]
        log.debug("adb %s", " ".join(args))
        try:
            p = subprocess.run(
                cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL
            )
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"adb {args[0]}: timeout") from e
        except OSError as e:
            raise AdbError(f"cannot run adb: {e}") from e
        out = (p.stdout or b"").decode(errors="replace") + (p.stderr or b"").decode(errors="replace")
        if p.returncode != 0:
            raise AdbError(out.strip() or f"adb exited with {p.returncode}")
        return out

    def popen(self, args: list[str], **kw) -> subprocess.Popen:
        return subprocess.Popen([self.adb, *args], creationflags=_NO_WINDOW, **kw)

    def devices(self) -> list[tuple[str, str]]:
        out = self.run(["devices"])
        res = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and SERIAL_RE.fullmatch(parts[0]):
                res.append((parts[0], parts[1]))
        return res

    def connect(self, host: str, port: int = ADB_DEFAULT_PORT) -> str:
        host = validate_host(host)
        if not 1 <= int(port) <= 65535:
            raise AdbError("invalid port")
        serial = f"{host}:{int(port)}"
        state = dict(self.devices()).get(serial)
        if state == "device":
            return serial
        out = self.run(["connect", serial], timeout=15)
        log.info("adb connect %s: %s", serial, out.strip())
        state = dict(self.devices()).get(serial)
        if state == "unauthorized":
            raise AdbError("ТВ не авторизовал этот компьютер: подтвердите запрос «Разрешить отладку» на экране ТВ")
        if state != "device":
            raise AdbError(f"не удалось подключиться по ADB к {serial}: {out.strip()}")
        return serial

    def screen_size(self, serial: str) -> tuple[int, int]:
        out = self.run(["-s", validate_serial(serial), "shell", "wm", "size"])
        m = re.findall(r"(\d+)x(\d+)", out)
        if not m:
            raise AdbError(f"cannot parse screen size: {out!r}")
        w, h = m[-1]  # "Override size" is printed last when present
        return int(w), int(h)

    def getprop(self, serial: str, prop: str) -> str:
        if not re.fullmatch(r"[a-z0-9._]{1,64}", prop):
            raise AdbError("invalid property name")
        return self.run(["-s", validate_serial(serial), "shell", "getprop", prop]).strip()


class AdbInput:
    """Input injection over one persistent `adb shell`. Commands are queued and coalesced."""

    def __init__(self, client: AdbClient, serial: str, screen: tuple[int, int]) -> None:
        self.client = client
        self.serial = validate_serial(serial)
        self.screen = screen
        self._q: queue.Queue[str | None] = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name="adb-input", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._q.put(None)

    def _px(self, xn: float, yn: float) -> tuple[int, int]:
        w, h = self.screen
        x = int(round(min(max(float(xn), 0.0), 1.0) * (w - 1)))
        y = int(round(min(max(float(yn), 0.0), 1.0) * (h - 1)))
        return x, y

    def key(self, code: int) -> None:
        code = int(code)
        if code not in ALLOWED_KEYCODES:
            raise AdbError(f"keycode {code} is not allowed")
        self._q.put(f"K{code}")

    def tap(self, xn: float, yn: float) -> None:
        x, y = self._px(xn, yn)
        self._q.put(f"input tap {x} {y}")

    def long_press(self, xn: float, yn: float) -> None:
        x, y = self._px(xn, yn)
        self._q.put(f"input swipe {x} {y} {x} {y} 700")

    def swipe(self, x1: float, y1: float, x2: float, y2: float, ms: int) -> None:
        a, b = self._px(x1, y1)
        c, d = self._px(x2, y2)
        ms = max(50, min(int(ms), 3000))
        self._q.put(f"input swipe {a} {b} {c} {d} {ms}")

    def text(self, s: str) -> None:
        self._q.put(f"input text {quote_input_text(s)}")

    def _ensure_proc(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            log.info("Starting persistent adb shell for %s", self.serial)
            self._proc = self.client.popen(
                ["-s", self.serial, "shell"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return self._proc

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            lines: list[str] = []
            keys: list[str] = []
            # Coalesce everything already queued; consecutive keys become one `input keyevent a b c`.
            batch = [item]
            while True:
                try:
                    nxt = self._q.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    self._q.put(None)
                    break
                batch.append(nxt)
            for cmd in batch:
                if cmd.startswith("K"):
                    keys.append(cmd[1:])
                    continue
                if keys:
                    lines.append("input keyevent " + " ".join(keys))
                    keys = []
                lines.append(cmd)
            if keys:
                lines.append("input keyevent " + " ".join(keys))
            for attempt in range(2):
                try:
                    p = self._ensure_proc()
                    assert p.stdin is not None
                    p.stdin.write(("\n".join(lines) + "\n").encode("ascii"))
                    p.stdin.flush()
                    break
                except (OSError, ValueError) as e:
                    log.warning("adb shell write failed (%s), restarting", e)
                    self._proc = None
        if self._proc and self._proc.poll() is None:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(b"exit\n")
                self._proc.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        log.info("adb input closed")
