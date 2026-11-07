"""Qt key codes -> Android KeyEvent codes (KEYCODE_*). One table for Windows/Linux/macOS."""

from PySide6.QtCore import Qt

# Android KeyEvent constants used by the client.
KEYCODE = {
    "HOME": 3,
    "BACK": 4,
    "DPAD_UP": 19,
    "DPAD_DOWN": 20,
    "DPAD_LEFT": 21,
    "DPAD_RIGHT": 22,
    "DPAD_CENTER": 23,
    "VOLUME_UP": 24,
    "VOLUME_DOWN": 25,
    "POWER": 26,
    "TAB": 61,
    "SPACE": 62,
    "ENTER": 66,
    "DEL": 67,
    "MENU": 82,
    "MEDIA_PLAY_PAUSE": 85,
    "MEDIA_STOP": 86,
    "MEDIA_NEXT": 87,
    "MEDIA_PREVIOUS": 88,
    "MEDIA_REWIND": 89,
    "MEDIA_FAST_FORWARD": 90,
    "PAGE_UP": 92,
    "PAGE_DOWN": 93,
    "ESCAPE": 111,
    "FORWARD_DEL": 112,
    "MOVE_HOME": 122,
    "MOVE_END": 123,
    "INSERT": 124,
    "F1": 131, "F2": 132, "F3": 133, "F4": 134, "F5": 135, "F6": 136,
    "F7": 137, "F8": 138, "F9": 139, "F10": 140, "F11": 141, "F12": 142,
    "VOLUME_MUTE": 164,
    "INFO": 165,
    "CHANNEL_UP": 166,
    "CHANNEL_DOWN": 167,
    "SETTINGS": 176,
    "TV_INPUT": 178,
    "APP_SWITCH": 187,
    "SEARCH": 84,
}

# Every keycode the ADB backend is allowed to send.
ALLOWED_KEYCODES = frozenset(KEYCODE.values())

K = Qt.Key
QT_TO_ANDROID = {
    K.Key_Up: KEYCODE["DPAD_UP"],
    K.Key_Down: KEYCODE["DPAD_DOWN"],
    K.Key_Left: KEYCODE["DPAD_LEFT"],
    K.Key_Right: KEYCODE["DPAD_RIGHT"],
    K.Key_Return: KEYCODE["ENTER"],
    K.Key_Enter: KEYCODE["ENTER"],
    K.Key_Escape: KEYCODE["BACK"],  # Esc acts as BACK on TV — what users expect
    K.Key_Backspace: KEYCODE["DEL"],
    K.Key_Delete: KEYCODE["FORWARD_DEL"],
    K.Key_Tab: KEYCODE["TAB"],
    K.Key_Home: KEYCODE["MOVE_HOME"],
    K.Key_End: KEYCODE["MOVE_END"],
    K.Key_PageUp: KEYCODE["PAGE_UP"],
    K.Key_PageDown: KEYCODE["PAGE_DOWN"],
    K.Key_Insert: KEYCODE["INSERT"],
    K.Key_Menu: KEYCODE["MENU"],
    K.Key_VolumeUp: KEYCODE["VOLUME_UP"],
    K.Key_VolumeDown: KEYCODE["VOLUME_DOWN"],
    K.Key_VolumeMute: KEYCODE["VOLUME_MUTE"],
    K.Key_MediaPlay: KEYCODE["MEDIA_PLAY_PAUSE"],
    K.Key_MediaPause: KEYCODE["MEDIA_PLAY_PAUSE"],
    K.Key_MediaTogglePlayPause: KEYCODE["MEDIA_PLAY_PAUSE"],
    K.Key_MediaStop: KEYCODE["MEDIA_STOP"],
    K.Key_MediaNext: KEYCODE["MEDIA_NEXT"],
    K.Key_MediaPrevious: KEYCODE["MEDIA_PREVIOUS"],
    K.Key_Back: KEYCODE["BACK"],
    K.Key_HomePage: KEYCODE["HOME"],
    K.Key_Search: KEYCODE["SEARCH"],
}
for _i in range(12):
    QT_TO_ANDROID[getattr(K, f"Key_F{_i + 1}")] = KEYCODE[f"F{_i + 1}"]

# Keys that always produce a keyevent even though Qt reports text for them.
NON_TEXT_KEYS = {K.Key_Return, K.Key_Enter, K.Key_Escape, K.Key_Backspace, K.Key_Delete, K.Key_Tab}


def android_keycode(qt_key: int) -> int | None:
    try:
        return QT_TO_ANDROID.get(K(qt_key))
    except ValueError:
        return None
