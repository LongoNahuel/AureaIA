"""Gestion de Sitios y Zonas: primer y segundo nivel de la jerarquia
Sitio > Zona > Camara. Un sitio es un local/sucursal; una zona es un area
dentro de ese sitio (ej. "Bóveda", "Sala de Máquinas A") que puede
marcarse como critica para resaltarla en el árbol de cámaras."""

from __future__ import annotations

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
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

from aurea_vms.models import repository
from aurea_vms.models.site import Site
from aurea_vms.models.zone import Zone
from aurea_vms.ui.notify import confirm, warn
from aurea_vms.ui.widgets.row_icon_button import row_icon_button

SITE_COLUMNS = ["Sitio", "Zonas", "Cámaras", "Operación"]
ZONE_COLUMNS = ["Zona", "Sitio", "Crítica", "Cámaras", "Operación"]


class _SiteDialog(QDialog):
    def __init__(self, site: Site | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sitio")
        self.resize(320, 0)

        self.name_edit = LineEdit()
        if site is not None:
            self.name_edit.setText(site.name)

        form = QFormLayout()
        form.addRow("Nombre:", self.name_edit)

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

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            warn(self, "Datos incompletos", "El nombre es obligatorio.")
            return
        self.accept()

    def values(self) -> dict:
        return {"name": self.name_edit.text().strip()}


class _ZoneDialog(QDialog):
    def __init__(self, zone: Zone | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Zona")
        self.resize(340, 0)

        self.site_combo = ComboBox()
        for site in repository.list_sites():
            self.site_combo.addItem(site.name, userData=site.id)
        self.name_edit = LineEdit()
        self.critical_check = CheckBox("Zona crítica (se resalta en el árbol de cámaras)")

        if zone is not None:
            index = self.site_combo.findData(zone.site_id)
            if index >= 0:
                self.site_combo.setCurrentIndex(index)
            self.name_edit.setText(zone.name)
            self.critical_check.setChecked(zone.critical)

        form = QFormLayout()
        form.addRow("Sitio:", self.site_combo)
        form.addRow("Nombre:", self.name_edit)
        form.addRow(self.critical_check)

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

    def _on_accept(self) -> None:
        if self.site_combo.currentData() is None:
            warn(self, "Datos incompletos", "Creá un sitio primero.")
            return
        if not self.name_edit.text().strip():
            warn(self, "Datos incompletos", "El nombre es obligatorio.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "site_id": self.site_combo.currentData(),
            "name": self.name_edit.text().strip(),
            "critical": self.critical_check.isChecked(),
        }


class SitesZonesModule(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sites: list[Site] = []
        self._zones: list[Zone] = []

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_sites_section())
        layout.addWidget(self._build_zones_section())

        self._reload()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._reload()
        super().showEvent(event)

    # --- sitios -------------------------------------------------

    def _build_sites_section(self) -> QWidget:
        section = QWidget(self)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        header_row.addWidget(StrongBodyLabel("Sitios"))
        header_row.addStretch(1)
        add_button = PrimaryPushButton(FluentIcon.ADD, "Agregar sitio")
        add_button.clicked.connect(self._on_add_site)
        header_row.addWidget(add_button)
        section_layout.addLayout(header_row)

        self.sites_table = TableWidget(section)
        self.sites_table.setColumnCount(len(SITE_COLUMNS))
        self.sites_table.setHorizontalHeaderLabels(SITE_COLUMNS)
        header = self.sites_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(SITE_COLUMNS) - 1, QHeaderView.ResizeMode.ResizeToContents)
        self.sites_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.sites_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sites_table.setBorderVisible(True)
        self.sites_table.setBorderRadius(6)
        section_layout.addWidget(self.sites_table)
        return section

    def _build_zones_section(self) -> QWidget:
        section = QWidget(self)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        header_row.addWidget(StrongBodyLabel("Zonas"))
        header_row.addStretch(1)
        add_button = PrimaryPushButton(FluentIcon.ADD, "Agregar zona")
        add_button.clicked.connect(self._on_add_zone)
        header_row.addWidget(add_button)
        section_layout.addLayout(header_row)

        self.zones_table = TableWidget(section)
        self.zones_table.setColumnCount(len(ZONE_COLUMNS))
        self.zones_table.setHorizontalHeaderLabels(ZONE_COLUMNS)
        header = self.zones_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(ZONE_COLUMNS) - 1, QHeaderView.ResizeMode.ResizeToContents)
        self.zones_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.zones_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.zones_table.setBorderVisible(True)
        self.zones_table.setBorderRadius(6)
        section_layout.addWidget(self.zones_table)
        return section

    def _reload(self) -> None:
        self._sites = repository.list_sites()
        self._zones = repository.list_zones()
        devices = repository.list_devices()

        zones_by_site: dict[int, list[Zone]] = {}
        for zone in self._zones:
            zones_by_site.setdefault(zone.site_id, []).append(zone)
        devices_by_zone: dict[int, int] = {}
        for device in devices:
            if device.zone_id is not None:
                devices_by_zone[device.zone_id] = devices_by_zone.get(device.zone_id, 0) + 1

        self.sites_table.setRowCount(len(self._sites))
        for row, site in enumerate(self._sites):
            site_zones = zones_by_site.get(site.id, [])
            camera_count = sum(devices_by_zone.get(z.id, 0) for z in site_zones)
            self.sites_table.setItem(row, 0, QTableWidgetItem(site.name))
            self.sites_table.setItem(row, 1, QTableWidgetItem(str(len(site_zones))))
            self.sites_table.setItem(row, 2, QTableWidgetItem(str(camera_count)))
            self.sites_table.setCellWidget(row, 3, self._site_operation_widget(site))

        site_names = {site.id: site.name for site in self._sites}
        self.zones_table.setRowCount(len(self._zones))
        for row, zone in enumerate(self._zones):
            self.zones_table.setItem(row, 0, QTableWidgetItem(zone.name))
            self.zones_table.setItem(row, 1, QTableWidgetItem(site_names.get(zone.site_id, "?")))
            self.zones_table.setItem(row, 2, QTableWidgetItem("Sí" if zone.critical else "No"))
            self.zones_table.setItem(row, 3, QTableWidgetItem(str(devices_by_zone.get(zone.id, 0))))
            self.zones_table.setCellWidget(row, 4, self._zone_operation_widget(zone))

    def _site_operation_widget(self, site: Site) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(2)

        edit_button = row_icon_button(FluentIcon.EDIT, "Editar")
        edit_button.clicked.connect(lambda _checked=False, s=site: self._on_edit_site(s))
        row.addWidget(edit_button)

        delete_button = row_icon_button(FluentIcon.DELETE, "Eliminar")
        delete_button.clicked.connect(lambda _checked=False, s=site: self._on_delete_site(s))
        row.addWidget(delete_button)
        return widget

    def _zone_operation_widget(self, zone: Zone) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(2)

        edit_button = row_icon_button(FluentIcon.EDIT, "Editar")
        edit_button.clicked.connect(lambda _checked=False, z=zone: self._on_edit_zone(z))
        row.addWidget(edit_button)

        delete_button = row_icon_button(FluentIcon.DELETE, "Eliminar")
        delete_button.clicked.connect(lambda _checked=False, z=zone: self._on_delete_zone(z))
        row.addWidget(delete_button)
        return widget

    def _on_add_site(self) -> None:
        dialog = _SiteDialog(parent=self)
        if dialog.exec():
            repository.add_site(**dialog.values())
            self._reload()

    def _on_edit_site(self, site: Site) -> None:
        dialog = _SiteDialog(site, parent=self)
        if dialog.exec():
            repository.update_site(site.id, **dialog.values())
            self._reload()

    def _on_delete_site(self, site: Site) -> None:
        if confirm(
            self, "Eliminar sitio", f'¿Eliminar "{site.name}"? También se eliminan sus zonas.'
        ):
            for zone in repository.list_zones(site.id):
                repository.delete_zone(zone.id)
            repository.delete_site(site.id)
            self._reload()

    def _on_add_zone(self) -> None:
        if not self._sites:
            warn(self, "Agregar zona", "Creá un sitio primero.")
            return
        dialog = _ZoneDialog(parent=self)
        if dialog.exec():
            repository.add_zone(**dialog.values())
            self._reload()

    def _on_edit_zone(self, zone: Zone) -> None:
        dialog = _ZoneDialog(zone, parent=self)
        if dialog.exec():
            repository.update_zone(zone.id, **dialog.values())
            self._reload()

    def _on_delete_zone(self, zone: Zone) -> None:
        if confirm(
            self, "Eliminar zona", f'¿Eliminar "{zone.name}"? Las cámaras quedan sin asignar.'
        ):
            repository.delete_zone(zone.id)
            self._reload()
