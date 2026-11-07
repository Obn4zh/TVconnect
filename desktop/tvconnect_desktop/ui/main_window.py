"""Main window: discovered / trusted / manual TVs and connection management."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..adb.adb_client import AdbClient, AdbError, find_adb, validate_host
from ..devices.trust_store import TrustStore
from ..discovery.discovery import Discovery, FoundTv
from ..net.connections import AdbConnection, BaseConnection, NativeConnection
from ..protocol.constants import DEFAULT_PORT
from ..settings import Settings, save_settings
from .dialogs import PairingDialog, SettingsDialog
from .viewer import ViewerWindow

log = logging.getLogger(__name__)


@dataclass
class DeviceRow:
    key: str
    name: str
    host: str
    port: int = DEFAULT_PORT
    tv_id: str = ""
    model: str = ""
    sources: set[str] = field(default_factory=set)
    status: str = ""


class _Bridge(QObject):
    found = Signal(object)
    adb_devices = Signal(list)


def parse_host_port(text: str) -> tuple[str, int]:
    text = text.strip()
    host, port = text, DEFAULT_PORT
    if text.count(":") == 1:
        host, p = text.split(":")
        port = int(p)
    host = validate_host(host)
    if not 1 <= port <= 65535:
        raise ValueError("port out of range")
    return host, port


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.trust = TrustStore()
        self.rows: dict[str, DeviceRow] = {}
        self.active: dict[str, tuple[BaseConnection, ViewerWindow | None]] = {}
        self._pairing: dict[str, PairingDialog] = {}

        self.setWindowTitle("TVconnect — LAN Remote для Android TV")
        self.resize(820, 460)
        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.addWidget(QLabel("Телевизоры в локальной сети:"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Имя", "IP-адрес", "Источник", "Статус"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.cellDoubleClicked.connect(lambda *_: self.connect_selected())
        lay.addWidget(self.table)

        btns = QHBoxLayout()
        for label, slot in (
            ("Подключить", self.connect_selected),
            ("Подключить по ADB", self.connect_selected_adb),
            ("Отключить", self.disconnect_selected),
            ("Обновить", self.refresh),
            ("Добавить по IP…", self.add_manual),
            ("Забыть", self.forget_selected),
            ("Настройки…", self.open_settings),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        lay.addLayout(btns)
        self.statusBar().showMessage("Поиск телевизоров…")

        self.bridge = _Bridge()
        self.bridge.found.connect(self._on_found)
        self.bridge.adb_devices.connect(self._on_adb_devices)
        self.discovery = Discovery(self.bridge.found.emit)

        for t in self.trust.all():
            self._upsert(DeviceRow(key=t.tv_id, name=t.name, host=t.host, port=t.port, tv_id=t.tv_id, sources={"доверенный"}))
        for h in settings.manual_hosts:
            try:
                host, port = parse_host_port(h)
                self._upsert(DeviceRow(key=f"{host}:{port}", name=host, host=host, port=port, sources={"вручную"}))
            except ValueError:
                pass
        self.discovery.start()
        self._scan_adb()

    # ---- device list ----

    def _find_key(self, tv_id: str, host: str, port: int) -> str | None:
        if tv_id and tv_id in self.rows:
            return tv_id
        for k, r in self.rows.items():
            if r.host == host and (r.port == port or "ADB" in r.sources):
                return k
        return None

    def _upsert(self, row: DeviceRow) -> None:
        key = self._find_key(row.tv_id, row.host, row.port) or row.key
        cur = self.rows.get(key)
        if cur is None:
            self.rows[key] = row
            row.key = key
        else:
            cur.sources |= row.sources
            if row.tv_id:
                cur.tv_id = row.tv_id
            if row.name and row.name != row.host:
                cur.name = row.name
            if "ADB" not in row.sources:
                cur.host, cur.port = row.host, row.port
            if row.tv_id and key != row.tv_id:
                # Promote host-keyed row to tv_id key
                self.rows.pop(key)
                cur.key = row.tv_id
                self.rows[row.tv_id] = cur
                if key in self.active:
                    self.active[row.tv_id] = self.active.pop(key)
        self._render()

    def _render(self) -> None:
        sel = self._selected_key()
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows.values()):
            status = "подключён" if r.key in self.active else ("доверенный" if self.trust.get(r.tv_id) else "")
            items = [r.name, f"{r.host}:{r.port}", ", ".join(sorted(r.sources)), r.status or status]
            for j, text in enumerate(items):
                it = QTableWidgetItem(text)
                it.setData(Qt.ItemDataRole.UserRole, r.key)
                self.table.setItem(i, j, it)
            if r.key == sel:
                self.table.selectRow(i)

    def _selected_key(self) -> str | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _selected(self) -> DeviceRow | None:
        k = self._selected_key()
        if k is None and self.table.rowCount() == 1:
            k = self.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
        return self.rows.get(k) if k else None

    def _on_found(self, tv: FoundTv) -> None:
        src = "mDNS" if tv.source == "mdns" else "UDP"
        self._upsert(DeviceRow(key=tv.tv_id or f"{tv.host}:{tv.port}", name=tv.name, host=tv.host,
                               port=tv.port, tv_id=tv.tv_id, model=tv.model, sources={src}))
        self.statusBar().showMessage(f"Найдено устройств: {len(self.rows)}", 5000)

    def _scan_adb(self) -> None:
        def work():
            path = find_adb(self.settings.adb_path)
            if not path:
                log.info("adb not found: ADB features disabled (set the path in Settings)")
                return
            log.info("Using adb: %s", path)
            try:
                devs = AdbClient(path).devices()
            except AdbError as e:
                log.info("adb devices failed: %s", e)
                return
            self.bridge.adb_devices.emit([s for s, st in devs if st == "device" and ":" in s])

        threading.Thread(target=work, name="adb-scan", daemon=True).start()

    def _on_adb_devices(self, serials: list) -> None:
        for s in serials:
            host, _, port = s.rpartition(":")
            self._upsert(DeviceRow(key=f"{host}:{DEFAULT_PORT}", name=host, host=host, port=DEFAULT_PORT, sources={"ADB"}))

    def refresh(self) -> None:
        self.statusBar().showMessage("Поиск…", 3000)
        self.discovery.probe()
        self._scan_adb()

    def add_manual(self) -> None:
        text, ok = QInputDialog.getText(self, "Добавить телевизор", "IP-адрес (или IP:порт):")
        if not ok or not text.strip():
            return
        try:
            host, port = parse_host_port(text)
        except (ValueError, AdbError):
            QMessageBox.warning(self, "TVconnect", "Неверный адрес")
            return
        entry = f"{host}:{port}"
        if entry not in self.settings.manual_hosts:
            self.settings.manual_hosts.append(entry)
            save_settings(self.settings)
        self._upsert(DeviceRow(key=entry, name=host, host=host, port=port, sources={"вручную"}))

    def forget_selected(self) -> None:
        r = self._selected()
        if not r:
            return
        if QMessageBox.question(self, "TVconnect", f"Удалить «{r.name}» из списка и доверенных?") != QMessageBox.StandardButton.Yes:
            return
        if r.tv_id:
            self.trust.forget(r.tv_id)
        entry = f"{r.host}:{r.port}"
        if entry in self.settings.manual_hosts:
            self.settings.manual_hosts.remove(entry)
            save_settings(self.settings)
        if r.key not in self.active:
            self.rows.pop(r.key, None)
        self._render()

    def open_settings(self) -> None:
        if SettingsDialog(self.settings, self).exec():
            save_settings(self.settings)

    # ---- connections ----

    def connect_selected(self) -> None:
        r = self._selected()
        if not r:
            QMessageBox.information(self, "TVconnect", "Выберите телевизор в списке или добавьте его по IP.")
            return
        if r.sources == {"ADB"}:
            self.connect_selected_adb()
            return
        self._start(r, NativeConnection(self.settings, self.trust, r.host, r.port))

    def connect_selected_adb(self) -> None:
        r = self._selected()
        if not r:
            return
        self._start(r, AdbConnection(self.settings, r.host))

    def _start(self, r: DeviceRow, conn: BaseConnection) -> None:
        if r.key in self.active:
            viewer = self.active[r.key][1]
            if viewer:
                viewer.raise_()
                viewer.activateWindow()
            return
        key = r.key
        viewer = ViewerWindow(conn, self.settings)
        self.active[key] = (conn, viewer)
        conn.state.connect(lambda st, d: self._set_status(key, {"connecting": "подключение…", "pairing": "ожидание подтверждения на ТВ",
                                                                  "authenticating": "авторизация…", "connected": "подключён"}.get(st, st)))
        conn.pairing.connect(lambda sas, fp: self._show_pairing(key, r.name, sas, fp))
        conn.ready.connect(lambda info: self._on_ready(key, info))
        conn.closed.connect(lambda reason, err: self._on_closed(key, reason, err))
        conn.start()

    def _set_status(self, key: str, text: str) -> None:
        if key in self.rows:
            self.rows[key].status = text
            self._render()

    def _show_pairing(self, key: str, name: str, sas: str, fp: str) -> None:
        dlg = PairingDialog(name, sas, fp, self)
        dlg.rejected.connect(lambda: self._cancel(key))
        self._pairing[key] = dlg
        dlg.show()

    def _close_pairing(self, key: str) -> None:
        dlg = self._pairing.pop(key, None)
        if dlg:
            dlg.blockSignals(True)
            dlg.close()

    def _cancel(self, key: str) -> None:
        self._pairing.pop(key, None)
        conn, viewer = self.active.get(key, (None, None))
        if viewer:
            viewer.close()
        elif conn:
            conn.close()

    def _on_ready(self, key: str, info: dict) -> None:
        self._close_pairing(key)
        conn, viewer = self.active.get(key, (None, None))
        if key in self.rows and info.get("name"):
            self.rows[key].name = info["name"]
        self._set_status(key, "подключён")
        if viewer:
            viewer.show()
            viewer.raise_()

    def _on_closed(self, key: str, reason: str, error: bool) -> None:
        self._close_pairing(key)
        conn, viewer = self.active.pop(key, (None, None))
        if viewer and viewer.isVisible():
            viewer.on_connection_closed(reason, error)
        elif error:
            QMessageBox.warning(self, "TVconnect", f"Не удалось подключиться: {reason}")
        self._set_status(key, "")
        self.statusBar().showMessage(f"Отключено: {reason}", 8000)

    def disconnect_selected(self) -> None:
        r = self._selected()
        if r and r.key in self.active:
            conn, viewer = self.active[r.key]
            if viewer:
                viewer.close()
            else:
                conn.close()

    def closeEvent(self, e) -> None:
        for conn, viewer in list(self.active.values()):
            if viewer:
                viewer.close()
            conn.close()
        self.discovery.stop()
        super().closeEvent(e)
