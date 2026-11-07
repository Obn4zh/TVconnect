from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..paths import data_dir, log_dir
from ..settings import FPS_CHOICES, INPUT_MODES, RESOLUTIONS, Settings


class PairingDialog(QDialog):
    def __init__(self, tv_name: str, sas: str, fp: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Подтверждение подключения")
        self.setModal(False)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Подтвердите подключение на телевизоре <b>{tv_name}</b>.<br>"
                             "Убедитесь, что код на ТВ совпадает с кодом ниже:"))
        code = QLabel(f"{sas[:3]} {sas[3:]}")
        f = QFont()
        f.setPointSize(32)
        f.setBold(True)
        code.setFont(f)
        code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(code)
        lay.addWidget(QLabel(f"Отпечаток ключа ТВ: <code>{fp}</code>"))
        lay.addWidget(QLabel("Если коды не совпадают — нажмите «Отмена»: кто-то может перехватывать соединение."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Настройки")
        form = QFormLayout(self)
        self.ed_name = QLineEdit(s.client_name)
        self.ed_name.setMaxLength(64)
        form.addRow("Имя этого компьютера:", self.ed_name)

        row = QHBoxLayout()
        self.ed_adb = QLineEdit(s.adb_path)
        self.ed_adb.setPlaceholderText("автопоиск (PATH, Android SDK)")
        btn = QPushButton("…")
        btn.clicked.connect(self._browse_adb)
        row.addWidget(self.ed_adb)
        row.addWidget(btn)
        form.addRow("Путь к adb:", row)

        self.cb_res = QComboBox()
        self.cb_res.addItems(list(RESOLUTIONS))
        self.cb_res.setCurrentText(s.resolution)
        form.addRow("Макс. разрешение:", self.cb_res)
        self.cb_fps = QComboBox()
        self.cb_fps.addItems([str(f) for f in FPS_CHOICES])
        self.cb_fps.setCurrentText(str(s.fps))
        form.addRow("FPS:", self.cb_fps)
        self.sp_br = QSpinBox()
        self.sp_br.setRange(1, 40)
        self.sp_br.setValue(s.bitrate_mbps)
        self.sp_br.setSuffix(" Мбит/с")
        form.addRow("Битрейт:", self.sp_br)
        self.cb_input = QComboBox()
        self.cb_input.addItems(list(INPUT_MODES))
        self.cb_input.setCurrentText(s.input_mode)
        form.addRow("Ввод (Native):", self.cb_input)
        self.chk_adb = QCheckBox("Подключать ADB для клавиш автоматически")
        self.chk_adb.setChecked(s.auto_adb_for_keys)
        form.addRow(self.chk_adb)
        self.chk_wheel = QCheckBox("Колесо мыши = D-pad вверх/вниз")
        self.chk_wheel.setChecked(s.wheel_as_dpad)
        form.addRow(self.chk_wheel)
        self.chk_clip = QCheckBox("Синхронизировать буфер обмена ПК → ТВ")
        self.chk_clip.setChecked(s.clipboard_sync)
        form.addRow(self.chk_clip)

        info = QHBoxLayout()
        info.addWidget(QLabel(f"Данные: {data_dir()}"))
        logs = QPushButton("Открыть логи")
        logs.clicked.connect(lambda: open_path(str(log_dir())))
        info.addWidget(logs)
        form.addRow(info)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _browse_adb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "adb", "", "adb (adb adb.exe);;Все файлы (*)")
        if path:
            self.ed_adb.setText(path)

    def _accept(self) -> None:
        s = self.s
        s.client_name = self.ed_name.text()
        s.adb_path = self.ed_adb.text().strip()
        s.resolution = self.cb_res.currentText()
        s.fps = int(self.cb_fps.currentText())
        s.bitrate_mbps = self.sp_br.value()
        s.input_mode = self.cb_input.currentText()
        s.auto_adb_for_keys = self.chk_adb.isChecked()
        s.wheel_as_dpad = self.chk_wheel.isChecked()
        s.clipboard_sync = self.chk_clip.isChecked()
        s.validate()
        self.accept()


def open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — opens a local folder we own
    else:
        subprocess.Popen(["xdg-open", path])
