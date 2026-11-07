import pytest

from tvconnect_desktop.adb.adb_client import (
    AdbError,
    AdbInput,
    UnsupportedText,
    quote_input_text,
    validate_host,
    validate_serial,
)
from tvconnect_desktop.input.controller import InputRouter
from tvconnect_desktop.input.keymap import KEYCODE


def test_quote_input_text_escapes_shell():
    assert quote_input_text("hello world") == "'hello%sworld'"
    assert quote_input_text("it's") == "'it'\\''s'"
    assert quote_input_text("$(reboot);`id`") == "'$(reboot);`id`'"  # single quotes: no expansion


def test_quote_input_text_rejects_non_ascii_and_control():
    with pytest.raises(UnsupportedText):
        quote_input_text("привет")
    with pytest.raises(UnsupportedText):
        quote_input_text("a\nreboot")
    with pytest.raises(UnsupportedText):
        quote_input_text("")


def test_validate_host_and_serial():
    assert validate_host(" 192.168.1.5 ") == "192.168.1.5"
    assert validate_host("tv.local") == "tv.local"
    for bad in ("1.2.3.4; reboot", "-s", "a b", "$(x)"):
        with pytest.raises(AdbError):
            validate_host(bad)
    assert validate_serial("192.168.1.5:5555") == "192.168.1.5:5555"
    with pytest.raises(AdbError):
        validate_serial("x;rm -rf /")


class FakeAdb:
    def __init__(self):
        self.calls = []

    def key(self, code):
        self.calls.append(("key", code))

    def tap(self, x, y):
        self.calls.append(("tap", round(x, 2), round(y, 2)))

    def long_press(self, x, y):
        self.calls.append(("long", x, y))

    def swipe(self, *a):
        self.calls.append(("swipe",) + a)

    def text(self, s):
        if not s.isascii():
            raise UnsupportedText("ascii only")
        self.calls.append(("text", s))


def make_router(caps=None, adb=True):
    sent, notes = [], []
    r = InputRouter(notes.append)
    r.native_send = lambda m: sent.append(m) or True
    r.caps = caps if caps is not None else {"accessibility": True, "dpad_fallback": True}
    r.adb = FakeAdb() if adb else None
    return r, sent, notes


def test_auto_routes_keys_to_adb_and_pointer_to_native():
    r, sent, _ = make_router()
    r.key(KEYCODE["DPAD_UP"])
    r.key(KEYCODE["F5"])
    r.tap(0.5, 0.5)
    assert r.adb.calls == [("key", 19), ("key", 135)]
    assert sent == [{"t": "tap", "x": 0.5, "y": 0.5}]


def test_volume_and_back_go_native_without_adb():
    r, sent, notes = make_router(adb=False)
    r.key(KEYCODE["VOLUME_UP"])
    r.key(KEYCODE["BACK"])
    r.key(KEYCODE["F1"])  # impossible natively, no ADB
    assert [m["code"] for m in sent] == [24, 4]
    assert notes


def test_unicode_text_falls_back_to_native():
    r, sent, _ = make_router(caps={"accessibility": True})
    r.mode = "adb"
    r.text("abc")
    assert r.adb.calls == [("text", "abc")]
    r.text("привет")
    assert sent == [{"t": "text", "s": "привет"}]


def test_gesture_cancel_switches_pointer_to_adb_and_replays():
    r, sent, notes = make_router()
    r.tap(0.25, 0.75)
    r.on_gesture_cancelled()
    assert r.adb.calls == [("tap", 0.25, 0.75)]  # replayed
    r.tap(0.1, 0.1)
    assert r.adb.calls[-1] == ("tap", 0.1, 0.1)
    assert len(sent) == 1 and notes


def test_adb_input_rejects_unknown_keycode():
    inp = AdbInput.__new__(AdbInput)  # no subprocess needed for validation
    import queue

    inp._q = queue.Queue()
    with pytest.raises(AdbError):
        inp.key(9999)
    inp.key(4)
    assert inp._q.get_nowait() == "K4"


# ---- shared clipboard ----

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_ctrl_v_and_ctrl_c_in_viewer_trigger_clipboard(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tvconnect_desktop.settings import Settings
    from tvconnect_desktop.ui.viewer import VideoWidget

    class Conn:
        screen = (1920, 1080)
        router = InputRouter(lambda m: None)

    w = VideoWidget(Conn(), Settings())
    calls = []
    w.on_paste = lambda: calls.append("paste")
    w.on_copy = lambda: calls.append("copy")
    QTest.keyClick(w, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(w, Qt.Key.Key_Insert, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(w, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert calls == ["paste", "paste", "copy"]


def test_adb_paste_splits_lines_and_rejects_non_ascii(qapp):
    from tvconnect_desktop.net.connections import AdbConnection
    from tvconnect_desktop.settings import Settings

    c = AdbConnection(Settings(), "127.0.0.1")
    notes = []
    c.notice.connect(notes.append)
    c.router.adb = FakeAdb()
    c.paste_text("ab\r\ncd")
    assert c.router.adb.calls == [("text", "ab"), ("key", 66), ("text", "cd")]
    c.router.adb.calls.clear()
    c.paste_text("кириллица")
    assert c.router.adb.calls == [] and "ASCII" in notes[-1]
    c.decoder.close()


def test_find_adb_uses_bundled_copy_when_nothing_installed(tmp_path, monkeypatch):
    import sys as _sys

    from tvconnect_desktop.adb import adb_client

    exe = "adb.exe" if _sys.platform == "win32" else "adb"
    internal = tmp_path / "_internal"
    (internal / "adb").mkdir(parents=True)
    (internal / "adb" / exe).write_bytes(b"")
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(_sys, "executable", str(tmp_path / "TVconnect.exe"))
    monkeypatch.setattr(adb_client.shutil, "which", lambda name: None)
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "LOCALAPPDATA"):
        monkeypatch.delenv(env, raising=False)
    real_is_file = adb_client.Path.is_file
    # Pretend the well-known system locations are empty on this "fresh" PC.
    monkeypatch.setattr(adb_client.Path, "is_file", lambda p: str(p).startswith(str(tmp_path)) and real_is_file(p))
    assert adb_client.find_adb() == str(internal / "adb" / exe)
    # An adb installed on the PC still wins over the bundled one.
    monkeypatch.setattr(adb_client.shutil, "which", lambda name: str(tmp_path / "sys_adb"))
    (tmp_path / "sys_adb").write_bytes(b"")
    assert adb_client.find_adb() == str(tmp_path / "sys_adb")
