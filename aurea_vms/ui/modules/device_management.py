"""Gestion de Dispositivos: dos tablas.

- "Dispositivos administrados": lo que ya esta en la base, con estado real
  (probado por RTSP), y acciones rapidas por fila (Editar, Ajustes
  avanzados -> Analizadores, Vista rapida -> Vista en Vivo, Reiniciar via
  ONVIF).
- "Dispositivo conectado": lo que aparece ahora mismo en un escaneo
  ONVIF/WS-Discovery de la LAN, indicando si ya esta agregado o no, con un
  botón por fila para darlo de alta (pide credenciales, consulta perfiles
  de medios, precarga el formulario).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    DoubleSpinBox,
    FluentIcon,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    StrongBodyLabel,
    TableWidget,
)

from aurea_vms.core import device_manager
from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.device_manager import OnvifDiscoveryResult, OnvifProfileInfo
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DeviceStatusEvent
from aurea_vms.core.rtsp_templates import DEVICE_TYPE_LABELS
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.models.device import Device
from aurea_vms.ui import icons
from aurea_vms.ui.dialogs.device_dialog import DeviceDialog
from aurea_vms.ui.labels import display_status
from aurea_vms.ui.notify import confirm, notify, warn
from aurea_vms.ui.widgets.row_icon_button import row_icon_button as _row_icon_button
from aurea_vms.ui.workers import FunctionWorker

MANAGED_COLUMNS = ["", "Nombre", "IP", "Estado", "Modelo", "Configuración", "Versión", "Operación"]
DISCOVERED_COLUMNS = ["IP", "Modelo", "Fabricante", "N° de serie", "Versión", "Agregado", ""]

STATUS_COLORS = {"online": "#3fb950", "offline": "#e5534b", "unknown": "#6e7681"}


class _CredentialsDialog(QDialog):
    """Pide usuario/contraseña para consultar los perfiles ONVIF de un
    dispositivo recien descubierto, antes de darlo de alta."""

    def __init__(self, ip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Credenciales — {ip}")
        self.resize(320, 0)

        self.username_edit = LineEdit()
        self.password_edit = PasswordLineEdit()

        form = QFormLayout()
        form.addRow("Usuario:", self.username_edit)
        form.addRow("Contraseña:", self.password_edit)

        cancel_button = PushButton("Cancelar")
        ok_button = PrimaryPushButton("Conectar")
        cancel_button.clicked.connect(self.reject)
        ok_button.clicked.connect(self.accept)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)
        buttons_row.addWidget(ok_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons_row)

    def username(self) -> str:
        return self.username_edit.text()

    def password(self) -> str:
        return self.password_edit.text()


class _DiscoverySettingsDialog(QDialog):
    """Configuración de búsqueda: tiempo de espera del escaneo WS-Discovery."""

    def __init__(self, timeout: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuración de búsqueda")
        self.resize(300, 0)

        self.timeout_spin = DoubleSpinBox()
        self.timeout_spin.setRange(1.0, 15.0)
        self.timeout_spin.setSingleStep(0.5)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(timeout)

        form = QFormLayout()
        form.addRow("Tiempo de espera del escaneo:", self.timeout_spin)

        cancel_button = PushButton("Cancelar")
        ok_button = PrimaryPushButton("Guardar")
        cancel_button.clicked.connect(self.reject)
        ok_button.clicked.connect(self.accept)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)
        buttons_row.addWidget(ok_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons_row)

    def timeout(self) -> float:
        return self.timeout_spin.value()


class DeviceManagementModule(QWidget):
    """Alta/edicion de dispositivos, prueba de conexion y descubrimiento ONVIF."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._devices: list[Device] = []
        self._discovered: list[OnvifDiscoveryResult] = []
        self._workers: list[FunctionWorker] = []
        self._discovery_timeout = 3.0

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_managed_section(), stretch=1)
        layout.addWidget(self._build_discovered_section(), stretch=1)

        self.status_label = CaptionLabel("")
        layout.addWidget(self.status_label)

        event_bus.device_status.connect(self._on_device_status, Qt.ConnectionType.QueuedConnection)

        self._reload_managed()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._reload_managed()
        super().showEvent(event)

    # --- seccion "Dispositivos administrados" -------------------------------------------------

    def _build_managed_section(self) -> QWidget:
        section = QWidget(self)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        self.managed_title = StrongBodyLabel("Dispositivos administrados (0)")
        header_row.addWidget(self.managed_title)
        header_row.addStretch(1)

        self.managed_search = SearchLineEdit()
        self.managed_search.setPlaceholderText("Buscar por nombre o IP...")
        self.managed_search.setFixedWidth(220)
        self.managed_search.textChanged.connect(self._apply_managed_filter)
        header_row.addWidget(self.managed_search)

        add_button = PrimaryPushButton(FluentIcon.ADD, "Agregar dispositivo")
        add_button.clicked.connect(self._on_add_manual)
        header_row.addWidget(add_button)

        delete_button = PushButton(FluentIcon.DELETE, "Eliminar seleccionados")
        delete_button.clicked.connect(self._on_delete_selected)
        header_row.addWidget(delete_button)
        section_layout.addLayout(header_row)

        self.managed_table = TableWidget(section)
        self.managed_table.setColumnCount(len(MANAGED_COLUMNS))
        self.managed_table.setHorizontalHeaderLabels(MANAGED_COLUMNS)
        header = self.managed_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            len(MANAGED_COLUMNS) - 1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.managed_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.managed_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.managed_table.setBorderVisible(True)
        self.managed_table.setBorderRadius(6)
        section_layout.addWidget(self.managed_table)
        return section

    def _reload_managed(self) -> None:
        self._devices = repository.list_devices()
        self.managed_title.setText(f"Dispositivos administrados ({len(self._devices)})")
        self.managed_table.setRowCount(len(self._devices))
        for row, device in enumerate(self._devices):
            self._set_managed_row(row, device)
        self._apply_managed_filter(self.managed_search.text())

    def _set_managed_row(self, row: int, device: Device) -> None:
        check_item = QTableWidgetItem()
        check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        check_item.setCheckState(Qt.CheckState.Unchecked)
        self.managed_table.setItem(row, 0, check_item)

        name_item = QTableWidgetItem(device.name)
        name_item.setData(Qt.ItemDataRole.UserRole, device.id)
        self.managed_table.setItem(row, 1, name_item)
        self.managed_table.setItem(row, 2, QTableWidgetItem(device.ip))
        self.managed_table.setCellWidget(row, 3, self._status_widget(device.status))
        self.managed_table.setItem(row, 4, QTableWidgetItem(device.model or "—"))

        config_bits = DEVICE_TYPE_LABELS.get(device.device_type, device.device_type)
        if device.device_type != "ipc":
            config_bits = f"{config_bits} · Canal {device.channel}"
        self.managed_table.setItem(row, 5, QTableWidgetItem(config_bits))
        self.managed_table.setItem(row, 6, QTableWidgetItem(device.firmware_version or "—"))
        self.managed_table.setCellWidget(row, 7, self._operation_widget(device))

    def _status_widget(self, status: str) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(6)
        dot = BodyLabel()
        dot.setPixmap(icons.status_dot_pixmap(STATUS_COLORS.get(status, STATUS_COLORS["unknown"])))
        row.addWidget(dot)
        row.addWidget(BodyLabel(display_status(status)))
        row.addStretch(1)
        return widget

    def _operation_widget(self, device: Device) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(2)

        edit_button = _row_icon_button(FluentIcon.EDIT, "Editar")
        edit_button.clicked.connect(lambda _checked=False, d=device: self._on_edit(d))

        advanced_button = _row_icon_button(FluentIcon.SETTING, "Ajustes avanzados (Analizadores)")
        advanced_button.clicked.connect(
            lambda _checked=False, d=device: event_bus.open_analytics_config_requested.emit(d.id)
        )

        quick_view_button = _row_icon_button(FluentIcon.VIDEO, "Vista rápida")
        quick_view_button.clicked.connect(
            lambda _checked=False, d=device: event_bus.open_live_view_requested.emit(d.id)
        )

        reboot_button = _row_icon_button(FluentIcon.POWER_BUTTON, "Reiniciar")
        reboot_button.clicked.connect(lambda _checked=False, d=device: self._on_reboot(d))

        for button in (edit_button, advanced_button, quick_view_button, reboot_button):
            row.addWidget(button)
        return widget

    def _apply_managed_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row, device in enumerate(self._devices):
            visible = not needle or needle in device.name.lower() or needle in device.ip.lower()
            self.managed_table.setRowHidden(row, not visible)

    def _checked_managed_device_ids(self) -> list[int]:
        ids = []
        for row in range(self.managed_table.rowCount()):
            check_item = self.managed_table.item(row, 0)
            if check_item is not None and check_item.checkState() == Qt.CheckState.Checked:
                ids.append(self.managed_table.item(row, 1).data(Qt.ItemDataRole.UserRole))
        return ids

    def _run(self, func, on_success=None, on_error=None) -> None:
        worker = FunctionWorker(func, self)
        if on_success:
            worker.succeeded.connect(on_success)
        if on_error:
            worker.failed.connect(on_error)
        worker.finished.connect(
            lambda: self._workers.remove(worker) if worker in self._workers else None
        )
        self._workers.append(worker)
        worker.start()

    def _on_add_manual(self) -> None:
        dialog = DeviceDialog(self)
        if dialog.exec():
            repository.add_device(**dialog.values())
            self._reload_managed()

    def _on_edit(self, device: Device) -> None:
        initial = {
            "device_type": device.device_type,
            "name": device.name,
            "channel": device.channel,
            "ip": device.ip,
            "port": device.port,
            "username": device.username,
            "password": device.password,
            "rtsp_main_url": device.rtsp_main_url,
            "rtsp_sub_url": device.rtsp_sub_url,
            "onvif_port": device.onvif_port,
            "has_ptz": device.has_ptz,
        }
        dialog = DeviceDialog(self, initial=initial)
        if dialog.exec():
            repository.update_device(device.id, **dialog.values())
            self._reload_managed()

    def _on_reboot(self, device: Device) -> None:
        if not device.onvif_port:
            warn(self, "Reiniciar", "Este dispositivo no tiene puerto ONVIF configurado.")
            return
        if not confirm(self, "Reiniciar dispositivo", f'¿Reiniciar "{device.name}" ({device.ip})?'):
            return
        self.status_label.setText(f"Reiniciando {device.name}...")
        self._run(
            lambda: device_manager.reboot_device(
                device.ip, device.onvif_port, device.username, device.password
            ),
            lambda _r: notify(self, "Reiniciar", f"Reinicio solicitado a {device.name}."),
            lambda msg: warn(self, "Reiniciar", f"No se pudo reiniciar: {msg}"),
        )

    def _on_delete_selected(self) -> None:
        device_ids = self._checked_managed_device_ids()
        if not device_ids:
            warn(self, "Eliminar dispositivos", "Marcá la casilla de los dispositivos a eliminar.")
            return
        plural = "s" if len(device_ids) > 1 else ""
        if not confirm(
            self, "Eliminar dispositivos", f"¿Eliminar {len(device_ids)} dispositivo{plural}?"
        ):
            return
        for device_id in device_ids:
            # Primero se apagan los consumidores vivos (analiticas y streams);
            # recien despues se borra la fila, y la cascada de la DB se lleva
            # configs/reglas/eventos asociados.
            for config in repository.list_analytics_configs(device_id):
                analytics_engine.stop(config.id)
            stream_manager.stop_device(device_id)
            repository.delete_device(device_id)
        self._reload_managed()

    def _on_device_status(self, event: DeviceStatusEvent) -> None:
        for row, device in enumerate(self._devices):
            if device.id == event.device_id:
                self.managed_table.setCellWidget(
                    row, 3, self._status_widget("online" if event.online else "offline")
                )
                break

    # --- seccion "Dispositivo conectado" -------------------------------------------------

    def _build_discovered_section(self) -> QWidget:
        section = QWidget(self)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        self.discovered_title = StrongBodyLabel("Dispositivo conectado (0)")
        header_row.addWidget(self.discovered_title)
        header_row.addStretch(1)

        self.discovered_search = SearchLineEdit()
        self.discovered_search.setPlaceholderText("Buscar por IP o modelo...")
        self.discovered_search.setFixedWidth(220)
        self.discovered_search.textChanged.connect(self._apply_discovered_filter)
        header_row.addWidget(self.discovered_search)

        refresh_button = PushButton(FluentIcon.SYNC, "Actualizar")
        refresh_button.clicked.connect(self._start_discovery)
        header_row.addWidget(refresh_button)

        settings_button = PushButton(FluentIcon.SETTING, "Configuración de búsqueda")
        settings_button.clicked.connect(self._on_discovery_settings)
        header_row.addWidget(settings_button)
        section_layout.addLayout(header_row)

        self.discovered_table = TableWidget(section)
        self.discovered_table.setColumnCount(len(DISCOVERED_COLUMNS))
        self.discovered_table.setHorizontalHeaderLabels(DISCOVERED_COLUMNS)
        header = self.discovered_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            len(DISCOVERED_COLUMNS) - 1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.discovered_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.discovered_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.discovered_table.setBorderVisible(True)
        self.discovered_table.setBorderRadius(6)
        section_layout.addWidget(self.discovered_table)
        return section

    def _managed_ips(self) -> set[str]:
        return {device.ip for device in self._devices}

    def _start_discovery(self) -> None:
        self.status_label.setText("Buscando dispositivos ONVIF en la red...")
        self._run(
            lambda: device_manager.discover_onvif(timeout=self._discovery_timeout),
            self._on_discovery_done,
            self._on_discovery_failed,
        )

    def _on_discovery_done(self, results: list[OnvifDiscoveryResult]) -> None:
        self._discovered = results
        self.status_label.setText(f"{len(results)} dispositivo(s) encontrados en la red.")
        self._reload_discovered_table()

    def _on_discovery_failed(self, message: str) -> None:
        self.status_label.setText(f"Error al buscar dispositivos: {message}")

    def _reload_discovered_table(self) -> None:
        managed_ips = self._managed_ips()
        self.discovered_title.setText(f"Dispositivo conectado ({len(self._discovered)})")
        self.discovered_table.setRowCount(len(self._discovered))
        for row, result in enumerate(self._discovered):
            already_added = result.ip in managed_ips
            self.discovered_table.setItem(row, 0, QTableWidgetItem(f"{result.ip}:{result.port}"))
            self.discovered_table.setItem(row, 1, QTableWidgetItem(result.model or "—"))
            self.discovered_table.setItem(row, 2, QTableWidgetItem(result.manufacturer or "—"))
            self.discovered_table.setItem(row, 3, QTableWidgetItem(result.serial_number or "—"))
            self.discovered_table.setItem(row, 4, QTableWidgetItem(result.firmware_version or "—"))
            self.discovered_table.setItem(row, 5, QTableWidgetItem("Sí" if already_added else "No"))

            if already_added:
                added_label = CaptionLabel("Ya agregado")
                self.discovered_table.setCellWidget(row, 6, added_label)
            else:
                add_button = _row_icon_button(FluentIcon.ADD, "Agregar")
                add_button.clicked.connect(
                    lambda _checked=False, r=result: self._on_add_discovered(r)
                )
                self.discovered_table.setCellWidget(row, 6, add_button)
        self._apply_discovered_filter(self.discovered_search.text())

    def _apply_discovered_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row, result in enumerate(self._discovered):
            haystack = f"{result.ip} {result.model or ''} {result.manufacturer or ''}".lower()
            self.discovered_table.setRowHidden(row, bool(needle) and needle not in haystack)

    def _on_discovery_settings(self) -> None:
        dialog = _DiscoverySettingsDialog(self._discovery_timeout, self)
        if dialog.exec():
            self._discovery_timeout = dialog.timeout()

    def _on_add_discovered(self, result: OnvifDiscoveryResult) -> None:
        creds = _CredentialsDialog(result.ip, self)
        if not creds.exec():
            return
        username, password = creds.username(), creds.password()

        self.status_label.setText(f"Consultando perfiles de medios de {result.ip}...")
        self._run(
            lambda: device_manager.fetch_onvif_profiles(result.ip, result.port, username, password),
            lambda info: self._open_prefilled_dialog(result, username, password, info),
            lambda msg: warn(
                self, "Descubrimiento ONVIF", f"No se pudieron obtener los perfiles: {msg}"
            ),
        )

    def _open_prefilled_dialog(
        self, result: OnvifDiscoveryResult, username: str, password: str, info: OnvifProfileInfo
    ) -> None:
        name_bits = " ".join(part for part in (result.manufacturer, result.model) if part)
        initial = {
            "device_type": "ipc",
            "name": name_bits or result.ip,
            "channel": 1,
            "ip": result.ip,
            "port": 554,
            "username": username,
            "password": password,
            "rtsp_main_url": info.rtsp_main_url,
            "rtsp_sub_url": info.rtsp_sub_url,
            "onvif_port": result.port,
            "has_ptz": info.has_ptz,
        }
        dialog = DeviceDialog(self, initial=initial)
        if not dialog.exec():
            return
        values = dialog.values()
        values.update(
            manufacturer=result.manufacturer,
            model=result.model,
            firmware_version=result.firmware_version,
            serial_number=result.serial_number,
        )
        repository.add_device(**values)
        notify(self, "Dispositivo agregado", f'"{values["name"]}" se agregó correctamente.')
        self._reload_managed()
        self._reload_discovered_table()
