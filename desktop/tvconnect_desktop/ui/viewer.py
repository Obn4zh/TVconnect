"""Remote screen viewer: renders decoded frames and turns mouse/keyboard into TV input."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QWidget,
)

from ..input.keymap import KEYCODE, NON_TEXT_KEYS, android_keycode
from ..net.connections import BaseConnection
from ..settings import FPS_CHOICES, INPUT_MODES, RESOLUTIONS, Settings, save_settings

log = logging.getLogger(__name__)

TAP_MAX_MOVE = 0.015  # normalized distance
LONG_PRESS_MS = 600
TEXT_FLUSH_MS = 120


class VideoWidget(QWidget):
    def __init__(self, conn: BaseConnection, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.settings = settings
        self.image: QImage | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(320, 180)
        self._press: tuple[float, float, float] | None = None
        self._last_cursor = 0.0
        self._wheel_acc = 0
        self._text_buf = ""
        self._text_timer = QTimer(self, singleShot=True, interval=TEXT_FLUSH_MS)
        self._text_timer.timeout.connect(self._flush_text)
        self.frames = 0
        self.placeholder = "Ожидание видео…"
        self.on_paste = lambda: None
        self.on_copy = lambda: None

    def set_frame(self, img: QImage) -> None:
        self.image = img
        self.frames += 1
        self.update()

    def _target_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        if self.image is not None and not self.image.isNull():
            iw, ih = self.image.width(), self.image.height()
        else:
            iw, ih = self.conn.screen
        scale = min(w / iw, h / ih)
        tw, th = iw * scale, ih * scale
        return QRectF((w - tw) / 2, (h - th) / 2, tw, th)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.image is None:
            p.setPen(Qt.GlobalColor.lightGray)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self._target_rect(), self.image)

    def _norm(self, pos: QPointF) -> tuple[float, float] | None:
        r = self._target_rect()
        if r.width() <= 0 or not r.contains(pos):
            return None
        return (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()

    # ---- mouse ----

    def mousePressEvent(self, e) -> None:
        self.setFocus()
        n = self._norm(e.position())
        if e.button() == Qt.MouseButton.LeftButton and n:
            self._press = (n[0], n[1], time.monotonic())
        elif e.button() == Qt.MouseButton.RightButton:
            self.conn.router.key(KEYCODE["BACK"])
        elif e.button() == Qt.MouseButton.MiddleButton:
            self.conn.router.key(KEYCODE["HOME"])

    def mouseDoubleClickEvent(self, e) -> None:
        # Qt replaces the second press with a double-click event; treat it as another press.
        self.mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        x0, y0, t0 = self._press
        self._press = None
        n = self._norm(e.position())
        if n is None:
            r = self._target_rect()
            n = (
                min(max((e.position().x() - r.x()) / r.width(), 0.0), 1.0),
                min(max((e.position().y() - r.y()) / r.height(), 0.0), 1.0),
            )
        dt_ms = (time.monotonic() - t0) * 1000
        moved = ((n[0] - x0) ** 2 + (n[1] - y0) ** 2) ** 0.5
        if moved < TAP_MAX_MOVE:
            if dt_ms >= LONG_PRESS_MS:
                self.conn.router.long_press(x0, y0)
            else:
                self.conn.router.tap(x0, y0)
        else:
            self.conn.router.swipe(x0, y0, n[0], n[1], int(min(max(dt_ms, 100), 2000)))

    def mouseMoveEvent(self, e) -> None:
        now = time.monotonic()
        if now - self._last_cursor < 0.033:
            return
        n = self._norm(e.position())
        if n:
            self._last_cursor = now
            self.conn.router.cursor(*n)

    def wheelEvent(self, e) -> None:
        self._wheel_acc += e.angleDelta().y()
        notches = int(self._wheel_acc / 120)
        if notches == 0:
            return
        self._wheel_acc -= notches * 120
        if self.settings.wheel_as_dpad:
            code = KEYCODE["DPAD_UP"] if notches > 0 else KEYCODE["DPAD_DOWN"]
            for _ in range(min(abs(notches), 5)):
                self.conn.router.key(code)
            return
        n = self._norm(e.position()) or (0.5, 0.5)
        self.conn.router.scroll(n[0], n[1], notches)

    # ---- keyboard ----

    def event(self, e) -> bool:
        # Keep Tab/Backtab for the TV instead of Qt focus navigation.
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        key = Qt.Key(e.key())
        mods = e.modifiers()
        text = e.text()
        ctrl = bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
        if e.matches(QKeySequence.StandardKey.Paste) or (key == Qt.Key.Key_Insert and mods & Qt.KeyboardModifier.ShiftModifier):
            self._flush_text()
            self.on_paste()
            return
        if e.matches(QKeySequence.StandardKey.Copy):
            self.on_copy()
            return
        code = android_keycode(e.key())
        if key == Qt.Key.Key_Backtab:
            code = KEYCODE["TAB"]
        if text and text.isprintable() and not ctrl and key not in NON_TEXT_KEYS:
            self._text_buf += text
            self._text_timer.start()
            return
        if code is not None:
            self._flush_text()
            self.conn.router.key(code)
            return
        super().keyPressEvent(e)

    def _flush_text(self) -> None:
        self._text_timer.stop()
        if self._text_buf:
            s, self._text_buf = self._text_buf, ""
            self.conn.router.text(s)


class ViewerWindow(QMainWindow):
    def __init__(self, conn: BaseConnection, settings: Settings) -> None:
        super().__init__()
        self.conn = conn
        self.settings = settings
        self.setWindowTitle(f"TVconnect — {conn.title}")
        self.resize(1280, 760)
        self.view = VideoWidget(conn, settings, self)
        self.setCentralWidget(self.view)
        self._build_toolbar()
        self._build_status()
        self._closing_by_user = False

        conn.decoder.frame_ready.connect(self.view.set_frame)
        conn.rtt.connect(lambda ms: self.lbl_rtt.setText(f"RTT {ms:.0f} мс"))
        conn.notice.connect(lambda m: self.statusBar().showMessage(m, 6000))
        conn.video_status.connect(self._on_video_status)
        conn.adb_status.connect(self.lbl_adb.setText)
        conn.ready.connect(self._on_ready)
        conn.clipboard_from_tv.connect(self._on_clipboard_from_tv)

        # Shared clipboard
        self.view.on_paste = self.paste_from_pc
        self.view.on_copy = self.copy_from_tv
        self._clip = QGuiApplication.clipboard()
        self._last_clip_synced: str | None = None
        self._clip.dataChanged.connect(self._on_pc_clipboard_changed)

        self._fps_timer = QTimer(self, interval=1000)
        self._fps_timer.timeout.connect(self._update_stats)
        self._fps_timer.start()
        self._last_frames = 0

        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)
        self.view.setFocus()

    def _build_toolbar(self) -> None:
        tb = QToolBar("Управление", self)
        tb.setMovable(False)
        self.addToolBar(tb)
        self.toolbar = tb

        def key_action(label: str, code: int, tip: str) -> None:
            a = QAction(label, self)
            a.setToolTip(tip)
            a.triggered.connect(lambda: (self.conn.router.key(code), self.view.setFocus()))
            tb.addAction(a)

        key_action("◀ Назад", KEYCODE["BACK"], "BACK (Esc, правая кнопка мыши)")
        key_action("⌂ Домой", KEYCODE["HOME"], "HOME (средняя кнопка мыши)")
        key_action("☰ Меню", KEYCODE["MENU"], "MENU (клавиша Menu)")
        key_action("OK", KEYCODE["DPAD_CENTER"], "DPAD_CENTER")
        key_action("▣", KEYCODE["APP_SWITCH"], "Недавние приложения")
        key_action("🔉", KEYCODE["VOLUME_DOWN"], "Громкость −")
        key_action("🔊", KEYCODE["VOLUME_UP"], "Громкость +")
        key_action("🔇", KEYCODE["VOLUME_MUTE"], "Без звука")
        tb.addSeparator()

        a = QAction("📋 Вставить с ПК", self)
        a.setToolTip("Вставить текст из буфера обмена ПК в поле на ТВ (Ctrl+V)")
        a.triggered.connect(lambda: (self.paste_from_pc(), self.view.setFocus()))
        tb.addAction(a)
        a = QAction("⧉ Копировать с ТВ", self)
        a.setToolTip("Скопировать выделенный текст / текст поля на ТВ в буфер ПК (Ctrl+C)")
        a.triggered.connect(lambda: (self.copy_from_tv(), self.view.setFocus()))
        tb.addAction(a)
        self.chk_clip = QCheckBox("Синхр. буфер")
        self.chk_clip.setToolTip("Автоматически копировать буфер обмена ПК в буфер ТВ")
        self.chk_clip.setChecked(self.settings.clipboard_sync)
        self.chk_clip.toggled.connect(self._clip_sync_changed)
        self.chk_clip.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        tb.addWidget(self.chk_clip)
        tb.addSeparator()

        self.cb_res = QComboBox()
        self.cb_res.addItems(list(RESOLUTIONS))
        self.cb_res.setCurrentText(self.settings.resolution)
        self.cb_fps = QComboBox()
        self.cb_fps.addItems([str(f) for f in FPS_CHOICES])
        self.cb_fps.setCurrentText(str(self.settings.fps))
        self.cb_br = QComboBox()
        self.cb_br.addItems(["2", "4", "6", "8", "12", "16"])
        self.cb_br.setCurrentText(str(self.settings.bitrate_mbps))
        for label, w in (("Разрешение", self.cb_res), ("FPS", self.cb_fps), ("Мбит/с", self.cb_br)):
            tb.addWidget(QLabel(f" {label}: "))
            tb.addWidget(w)
            w.currentTextChanged.connect(self._quality_changed)
            w.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        tb.addSeparator()
        tb.addWidget(QLabel(" Ввод: "))
        self.cb_input = QComboBox()
        self.cb_input.addItems(list(INPUT_MODES))
        self.cb_input.setCurrentText(self.conn.router.mode)
        self.cb_input.setToolTip("auto: клавиши через ADB (если доступен), мышь/текст через службу на ТВ")
        self.cb_input.currentTextChanged.connect(self._input_mode_changed)
        self.cb_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        tb.addWidget(self.cb_input)
        self.chk_wheel = QCheckBox("Колесо = D-pad")
        self.chk_wheel.setChecked(self.settings.wheel_as_dpad)
        self.chk_wheel.toggled.connect(self._wheel_changed)
        self.chk_wheel.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        tb.addWidget(self.chk_wheel)
        tb.addSeparator()

        a = QAction("⛶ Полный экран", self)
        a.setToolTip("Ctrl+F")
        a.triggered.connect(self.toggle_fullscreen)
        tb.addAction(a)
        a = QAction("Отключить", self)
        a.triggered.connect(self.close)
        tb.addAction(a)

    def _build_status(self) -> None:
        sb = self.statusBar()
        self.lbl_mode = QLabel(f"Режим: {self.conn.mode_name}")
        self.lbl_video = QLabel("Видео: —")
        self.lbl_fps = QLabel("0 FPS")
        self.lbl_rtt = QLabel("RTT —")
        self.lbl_dec = QLabel("")
        self.lbl_adb = QLabel("")
        for w in (self.lbl_mode, self.lbl_video, self.lbl_fps, self.lbl_rtt, self.lbl_dec, self.lbl_adb):
            sb.addPermanentWidget(w)

    # ---- shared clipboard ----

    def paste_from_pc(self) -> None:
        text = self._clip.text()
        if not text:
            self.statusBar().showMessage("Буфер обмена ПК пуст (или в нём не текст)", 4000)
            return
        self._last_clip_synced = text
        self.conn.paste_text(text)

    def copy_from_tv(self) -> None:
        self.conn.request_tv_clipboard()

    def _on_clipboard_from_tv(self, text: str, source: str) -> None:
        self._last_clip_synced = text  # do not echo it back to the TV
        self._clip.setText(text)
        where = {"focused": "из поля/элемента в фокусе", "clipboard": "из буфера ТВ"}.get(source, "")
        self.statusBar().showMessage(f"Скопировано с ТВ {where}: {len(text)} символов", 5000)

    def _on_pc_clipboard_changed(self) -> None:
        if not self.settings.clipboard_sync:
            return
        text = self._clip.text()
        if text and text != self._last_clip_synced:
            self._last_clip_synced = text
            self.conn.push_clipboard(text)

    def _clip_sync_changed(self, on: bool) -> None:
        self.settings.clipboard_sync = on
        save_settings(self.settings)
        if on:
            self._on_pc_clipboard_changed()
        self.view.setFocus()

    def _on_ready(self, info: dict) -> None:
        self.setWindowTitle(f"TVconnect — {self.conn.title}")
        if self.settings.clipboard_sync:
            self._on_pc_clipboard_changed()  # initial sync of whatever is already copied on the PC
        caps = info.get("caps") or {}
        if self.conn.mode_name == "Native" and not caps.get("accessibility"):
            self.statusBar().showMessage(
                "Служба управления TVconnect на ТВ выключена: мышь/текст недоступны без ADB", 10000
            )

    def _on_video_status(self, msg: dict) -> None:
        st = msg.get("state")
        if st == "consent_pending":
            self.view.placeholder = "Подтвердите захват экрана на телевизоре («Начать»)…"
        elif st == "denied":
            self.view.placeholder = "Захват экрана отклонён на телевизоре"
        elif st == "error":
            self.view.placeholder = f"Ошибка видео: {msg.get('message', '')}"
        elif st == "running":
            self.lbl_video.setText(f"Видео: {msg.get('w')}×{msg.get('h')}")
        self.view.update()

    def _update_stats(self) -> None:
        f = self.view.frames
        self.lbl_fps.setText(f"{f - self._last_frames} FPS")
        self._last_frames = f
        w, h = self.conn.decoder.size
        if w:
            self.lbl_video.setText(f"Видео: {w}×{h}")
        self.lbl_dec.setText(
            f"декод {self.conn.decoder.last_decode_ms:.1f} мс, пропущено {self.conn.decoder.frames_dropped}"
        )

    def _quality_changed(self, _=None) -> None:
        self.settings.resolution = self.cb_res.currentText()
        self.settings.fps = int(self.cb_fps.currentText())
        self.settings.bitrate_mbps = int(self.cb_br.currentText())
        save_settings(self.settings)
        self.conn.apply_quality()
        self.view.setFocus()

    def _input_mode_changed(self, mode: str) -> None:
        self.conn.router.mode = mode
        if self.conn.mode_name == "Native":
            self.settings.input_mode = mode
            save_settings(self.settings)
        self.view.setFocus()

    def _wheel_changed(self, on: bool) -> None:
        self.settings.wheel_as_dpad = on
        save_settings(self.settings)
        self.view.setFocus()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.toolbar.show()
            self.statusBar().show()
        else:
            self.toolbar.hide()
            self.statusBar().hide()
            self.showFullScreen()
        self.view.setFocus()

    def on_connection_closed(self, reason: str, error: bool) -> None:
        if self._closing_by_user:
            return
        self._closing_by_user = True
        if error:
            QMessageBox.warning(self, "TVconnect", f"Соединение потеряно: {reason}")
        self.close()

    def closeEvent(self, e) -> None:
        self._closing_by_user = True
        self._fps_timer.stop()
        self.conn.close()
        super().closeEvent(e)
