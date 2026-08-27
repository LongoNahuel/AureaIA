"""Formulario de alta/edicion de un dispositivo. Puede llenarse a mano
(con plantilla de URL RTSP segun tipo de dispositivo) o precargarse desde
el resultado de OnvifDiscoveryDialog."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QInputDialog, QVBoxLayout
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SpinBox,
)
from sqlalchemy.exc import IntegrityError

from aurea_vms.core.rtsp_templates import DEVICE_TYPE_LABELS, DEVICE_TYPES, build_rtsp_urls
from aurea_vms.models import repository
from aurea_vms.ui.dialogs.onvif_discovery_dialog import OnvifDiscoveryDialog
from aurea_vms.ui.notify import warn


class DeviceDialog(QDialog):
    def __init__(self, parent=None, initial: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dispositivo")
        self.resize(440, 0)

        # Ultima URL aplicada por la plantilla, para no pisar una edicion
        # manual del usuario (solo se re-aplica si el campo esta vacio o
        # todavia tiene el valor de la plantilla anterior).
        self._auto_main = ""
        self._auto_sub = ""

        self.onvif_discovery_button = PushButton(FluentIcon.WIFI, "Buscar por ONVIF...")

        self.device_type_combo = ComboBox()
        for device_type in DEVICE_TYPES:
            self.device_type_combo.addItem(DEVICE_TYPE_LABELS[device_type], userData=device_type)
        self.device_type_combo.currentIndexChanged.connect(self._apply_template)

        self.name_edit = LineEdit()

        self.site_combo = ComboBox()
        self.zone_combo = ComboBox()
        self.site_combo.currentIndexChanged.connect(self._rebuild_zone_combo)
        self._reload_sites()
        new_site_button = PushButton(FluentIcon.ADD, "Nuevo")
        new_site_button.setToolTip("Crear un sitio nuevo")
        new_site_button.clicked.connect(self._on_new_site)
        site_row = QHBoxLayout()
        site_row.addWidget(self.site_combo, stretch=1)
        site_row.addWidget(new_site_button)

        self.ip_edit = LineEdit()
        self.ip_edit.textChanged.connect(self._apply_template)
        self.port_spin = SpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(554)
        self.port_spin.valueChanged.connect(self._apply_template)

        self.channel_spin = SpinBox()
        self.channel_spin.setRange(1, 128)
        self.channel_spin.setValue(1)
        self.channel_spin.valueChanged.connect(self._apply_template)

        self.username_edit = LineEdit()
        self.password_edit = PasswordLineEdit()
        self.rtsp_main_edit = LineEdit()
        self.rtsp_main_edit.setPlaceholderText("rtsp://192.168.1.50:554/media/video1")
        self.rtsp_sub_edit = LineEdit()
        self.rtsp_sub_edit.setPlaceholderText("(opcional)")
        self.onvif_port_spin = SpinBox()
        self.onvif_port_spin.setRange(0, 65535)
        self.onvif_port_spin.setValue(80)
        self.onvif_port_spin.setSpecialValueText("(sin ONVIF)")
        self.has_ptz_check = CheckBox("Tiene PTZ")

        form = QFormLayout()
        form.addRow(self.onvif_discovery_button)
        form.addRow("Tipo de dispositivo:", self.device_type_combo)
        form.addRow("Nombre:", self.name_edit)
        form.addRow("Sitio:", site_row)
        form.addRow("Zona:", self.zone_combo)
        form.addRow("IP:", self.ip_edit)
        form.addRow("Puerto RTSP:", self.port_spin)
        form.addRow("Canal (NVR/XVR):", self.channel_spin)
        form.addRow("Usuario:", self.username_edit)
        form.addRow("Contraseña:", self.password_edit)
        form.addRow("URL RTSP principal:", self.rtsp_main_edit)
        form.addRow("URL RTSP sub (opcional):", self.rtsp_sub_edit)
        form.addRow("Puerto ONVIF:", self.onvif_port_spin)
        form.addRow(self.has_ptz_check)

        cancel_button = PushButton("Cancelar")
        save_button = PrimaryPushButton(FluentIcon.SAVE, "Guardar")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._on_accept)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)
        buttons_row.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons_row)

        self.onvif_discovery_button.clicked.connect(self._open_onvif_discovery)

        self._rebuild_zone_combo()
        if initial:
            self._load(initial)

    def _reload_sites(self, select_id: int | None = None) -> None:
        current = select_id if select_id is not None else self.site_combo.currentData()
        self.site_combo.clear()
        self.site_combo.addItem("(sin asignar)", userData=None)
        for site in repository.list_sites():
            self.site_combo.addItem(site.name, userData=site.id)
        index = self.site_combo.findData(current)
        self.site_combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_new_site(self) -> None:
        name, ok = QInputDialog.getText(self, "Nuevo sitio", "Nombre del sitio:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            site = repository.add_site(name=name)
        except IntegrityError:
            warn(self, "Nuevo sitio", f'Ya existe un sitio llamado "{name}".')
            return
        self._reload_sites(select_id=site.id)

    def _rebuild_zone_combo(self, *_args) -> None:
        current_zone_id = self.zone_combo.currentData() if self.zone_combo.count() else None
        self.zone_combo.clear()
        self.zone_combo.addItem("(sin asignar)", userData=None)
        site_id = self.site_combo.currentData()
        if site_id is not None:
            for zone in repository.list_zones(site_id):
                self.zone_combo.addItem(zone.name, userData=zone.id)
        index = self.zone_combo.findData(current_zone_id)
        self.zone_combo.setCurrentIndex(index if index >= 0 else 0)

    def _apply_template(self, *_args) -> None:
        ip = self.ip_edit.text().strip()
        if not ip:
            return
        device_type = self.device_type_combo.currentData() or "ipc"
        main_url, sub_url = build_rtsp_urls(
            device_type, ip, self.port_spin.value(), self.channel_spin.value()
        )

        if not self.rtsp_main_edit.text().strip() or self.rtsp_main_edit.text() == self._auto_main:
            self.rtsp_main_edit.setText(main_url)
            self._auto_main = main_url
        if not self.rtsp_sub_edit.text().strip() or self.rtsp_sub_edit.text() == self._auto_sub:
            self.rtsp_sub_edit.setText(sub_url)
            self._auto_sub = sub_url

    def _load(self, data: dict) -> None:
        index = self.device_type_combo.findData(data.get("device_type", "ipc"))
        if index >= 0:
            self.device_type_combo.setCurrentIndex(index)
        self.name_edit.setText(data.get("name", ""))
        self.channel_spin.setValue(data.get("channel", 1))
        self.ip_edit.setText(data.get("ip", ""))
        self.port_spin.setValue(data.get("port", 554))
        self.username_edit.setText(data.get("username", ""))
        self.password_edit.setText(data.get("password", ""))
        self.rtsp_main_edit.setText(data.get("rtsp_main_url", ""))
        self.rtsp_sub_edit.setText(data.get("rtsp_sub_url") or "")
        self.onvif_port_spin.setValue(data.get("onvif_port") or 0)
        self.has_ptz_check.setChecked(bool(data.get("has_ptz", False)))

        zone_id = data.get("zone_id")
        if zone_id is not None:
            zone = repository.get_zone(zone_id)
            if zone is not None:
                site_index = self.site_combo.findData(zone.site_id)
                if site_index >= 0:
                    self.site_combo.setCurrentIndex(site_index)
                self._rebuild_zone_combo()
                zone_index = self.zone_combo.findData(zone_id)
                if zone_index >= 0:
                    self.zone_combo.setCurrentIndex(zone_index)

    def _open_onvif_discovery(self) -> None:
        dialog = OnvifDiscoveryDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_info:
            info = dialog.result_info
            self.ip_edit.setText(dialog.selected_ip)
            self.onvif_port_spin.setValue(dialog.selected_port)
            self.username_edit.setText(dialog.username)
            self.password_edit.setText(dialog.password)
            self.rtsp_main_edit.setText(info.rtsp_main_url)
            self.rtsp_sub_edit.setText(info.rtsp_sub_url or "")
            self.has_ptz_check.setChecked(info.has_ptz)
            if not self.name_edit.text():
                name_bits = " ".join(
                    part for part in (dialog.selected_manufacturer, dialog.selected_model) if part
                )
                self.name_edit.setText(name_bits or dialog.selected_ip)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            warn(self, "Datos incompletos", "El nombre es obligatorio.")
            return
        if not self.ip_edit.text().strip():
            warn(self, "Datos incompletos", "La IP es obligatoria.")
            return
        if not self.rtsp_main_edit.text().strip():
            warn(self, "Datos incompletos", "La URL RTSP principal es obligatoria.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "zone_id": self.zone_combo.currentData(),
            "device_type": self.device_type_combo.currentData() or "ipc",
            "channel": self.channel_spin.value(),
            "ip": self.ip_edit.text().strip(),
            "port": self.port_spin.value(),
            "username": self.username_edit.text(),
            "password": self.password_edit.text(),
            "rtsp_main_url": self.rtsp_main_edit.text().strip(),
            "rtsp_sub_url": self.rtsp_sub_edit.text().strip() or None,
            "onvif_port": self.onvif_port_spin.value() or None,
            "has_ptz": self.has_ptz_check.isChecked(),
        }
