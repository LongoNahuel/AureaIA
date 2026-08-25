"""Dialogo de descubrimiento ONVIF: escanea la LAN, y una vez elegido un
dispositivo, pide credenciales y consulta sus perfiles de medios (URLs RTSP
y soporte PTZ) para precargar el formulario de alta de dispositivo."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidgetItem, QVBoxLayout
from qfluentwidgets import BodyLabel, FluentIcon, LineEdit, ListWidget, PasswordLineEdit, PushButton

from aurea_vms.core import device_manager
from aurea_vms.core.device_manager import OnvifDiscoveryResult, OnvifProfileInfo
from aurea_vms.ui.workers import FunctionWorker


class OnvifDiscoveryDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Descubrimiento ONVIF")
        self.resize(420, 380)

        self.result_info: OnvifProfileInfo | None = None
        self.selected_ip = ""
        self.selected_port = 0
        self.selected_manufacturer: str | None = None
        self.selected_model: str | None = None
        self.username = ""
        self.password = ""

        self._worker: FunctionWorker | None = None

        self.scan_button = PushButton(FluentIcon.SEARCH, "Escanear LAN")
        self.list_widget = ListWidget()
        self.username_edit = LineEdit()
        self.password_edit = PasswordLineEdit()
        self.fetch_button = PushButton(FluentIcon.DOWNLOAD, "Obtener perfiles")
        self.fetch_button.setEnabled(False)
        self.status_label = BodyLabel('Presioná "Escanear LAN" para buscar cámaras.')
        self.status_label.setWordWrap(True)

        cred_row = QHBoxLayout()
        cred_row.addWidget(BodyLabel("Usuario:"))
        cred_row.addWidget(self.username_edit)
        cred_row.addWidget(BodyLabel("Contraseña:"))
        cred_row.addWidget(self.password_edit)

        cancel_button = PushButton("Cancelar")
        cancel_button.clicked.connect(self.reject)
        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.scan_button)
        layout.addWidget(self.list_widget)
        layout.addLayout(cred_row)
        layout.addWidget(self.fetch_button)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons_row)

        self.scan_button.clicked.connect(self._start_scan)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.fetch_button.clicked.connect(self._start_fetch)

    def _run(self, func, on_success, on_error) -> None:
        self._worker = FunctionWorker(func, self)
        self._worker.succeeded.connect(on_success)
        self._worker.failed.connect(on_error)
        self._worker.start()

    def _start_scan(self) -> None:
        self.status_label.setText("Buscando dispositivos ONVIF en la red (unos segundos)...")
        self.scan_button.setEnabled(False)
        self.list_widget.clear()
        self._run(
            lambda: device_manager.discover_onvif(timeout=3.0),
            self._on_scan_done,
            self._on_scan_failed,
        )

    def _on_scan_done(self, results: list[OnvifDiscoveryResult]) -> None:
        self.scan_button.setEnabled(True)
        if not results:
            self.status_label.setText(
                "No se encontraron dispositivos ONVIF (puede pasar en redes que "
                "bloquean multicast). Podés cargar el dispositivo a mano."
            )
            return
        self.status_label.setText(f"{len(results)} dispositivo(s) encontrados.")
        for result in results:
            label = f"{result.ip}:{result.port}"
            model_bits = " ".join(part for part in (result.manufacturer, result.model) if part)
            if model_bits:
                label = f"{label} — {model_bits}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.list_widget.addItem(item)

    def _on_scan_failed(self, message: str) -> None:
        self.scan_button.setEnabled(True)
        self.status_label.setText(f"Error al escanear: {message}")

    def _on_selection_changed(self) -> None:
        self.fetch_button.setEnabled(bool(self.list_widget.selectedItems()))

    def _start_fetch(self) -> None:
        item = self.list_widget.selectedItems()[0]
        result: OnvifDiscoveryResult = item.data(Qt.ItemDataRole.UserRole)
        username = self.username_edit.text()
        password = self.password_edit.text()

        self.status_label.setText("Consultando perfiles de medios...")
        self.fetch_button.setEnabled(False)
        self._run(
            lambda: device_manager.fetch_onvif_profiles(result.ip, result.port, username, password),
            lambda info: self._on_fetch_done(result, username, password, info),
            self._on_fetch_failed,
        )

    def _on_fetch_done(
        self,
        result: OnvifDiscoveryResult,
        username: str,
        password: str,
        info: OnvifProfileInfo,
    ) -> None:
        self.result_info = info
        self.selected_ip = result.ip
        self.selected_port = result.port
        self.selected_manufacturer = result.manufacturer
        self.selected_model = result.model
        self.username = username
        self.password = password
        self.accept()

    def _on_fetch_failed(self, message: str) -> None:
        self.fetch_button.setEnabled(True)
        self.status_label.setText(f"Error al obtener perfiles: {message}")
