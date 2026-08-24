"""Arbol de dispositivos con buscador, agrupado Sitio > Zona > Camara,
para arrastrar una camara a un tile de la grilla de Vista en Vivo (o
asignarla con doble click al tile seleccionado). Zonas marcadas
"critical" se resaltan en rojo. Las camaras sin zona asignada aparecen en
un grupo aparte, solo cuando se estan viendo todos los sitios."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QColor, QDrag
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import SearchLineEdit, TreeWidget

from aurea_vms.models import repository
from aurea_vms.ui import icons

DEVICE_ID_MIME = "application/x-aurea-device-id"
CRITICAL_COLOR = QColor("#e5534b")


class _DraggableTree(TreeWidget):
    def startDrag(self, supportedActions) -> None:  # noqa: N802 - override de Qt
        item = self.currentItem()
        if item is None:
            return
        device_id = item.data(0, Qt.ItemDataRole.UserRole)
        if device_id is None:
            return

        mime = QMimeData()
        mime.setData(DEVICE_ID_MIME, str(device_id).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(icons.icon_live_view("#3b82f6").pixmap(20, 20))
        drag.exec(Qt.DropAction.CopyAction)


class DeviceTreeWidget(QWidget):
    device_double_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._site_filter: int | None = None

        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText("Buscar cámara...")
        self.search_edit.textChanged.connect(self._apply_filter)

        self.tree = _DraggableTree(self)
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.tree)

        self.reload()

    def set_site_filter(self, site_id: int | None) -> None:
        """None = mostrar todos los sitios (y las camaras sin asignar)."""
        self._site_filter = site_id
        self.reload()

    def reload(self) -> None:
        self.tree.clear()

        zones_by_site: dict[int, list] = {}
        for zone in repository.list_zones():
            zones_by_site.setdefault(zone.site_id, []).append(zone)

        devices_by_zone: dict[int, list] = {}
        unassigned = []
        for device in repository.list_devices():
            if device.zone_id is not None:
                devices_by_zone.setdefault(device.zone_id, []).append(device)
            else:
                unassigned.append(device)

        camera_icon = icons.icon_live_view("#9aa3af")

        for site in repository.list_sites():
            if self._site_filter is not None and site.id != self._site_filter:
                continue
            site_zones = zones_by_site.get(site.id, [])
            site_device_count = sum(len(devices_by_zone.get(z.id, [])) for z in site_zones)
            if site_device_count == 0:
                continue

            site_item = QTreeWidgetItem([f"{site.name} ({site_device_count})"])
            site_item.setFlags(site_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            self.tree.addTopLevelItem(site_item)

            for zone in site_zones:
                zone_devices = devices_by_zone.get(zone.id, [])
                if not zone_devices:
                    continue
                zone_item = QTreeWidgetItem([f"{zone.name} ({len(zone_devices)})"])
                zone_item.setFlags(zone_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                if zone.critical:
                    zone_item.setForeground(0, CRITICAL_COLOR)
                site_item.addChild(zone_item)

                for device in zone_devices:
                    item = QTreeWidgetItem([f"{device.name}  ({device.ip})"])
                    item.setIcon(0, camera_icon)
                    item.setData(0, Qt.ItemDataRole.UserRole, device.id)
                    zone_item.addChild(item)

                zone_item.setExpanded(True)

            site_item.setExpanded(True)

        if self._site_filter is None and unassigned:
            root = QTreeWidgetItem([f"Sin asignar ({len(unassigned)})"])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            self.tree.addTopLevelItem(root)
            for device in unassigned:
                item = QTreeWidgetItem([f"{device.name}  ({device.ip})"])
                item.setIcon(0, camera_icon)
                item.setData(0, Qt.ItemDataRole.UserRole, device.id)
                root.addChild(item)
            root.setExpanded(True)

    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        device_id = item.data(0, Qt.ItemDataRole.UserRole)
        if device_id is not None:
            self.device_double_clicked.emit(device_id)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            self._filter_item(self.tree.topLevelItem(i), needle)

    def _filter_item(self, item: QTreeWidgetItem, needle: str) -> bool:
        """Devuelve True si el item (o algun hijo) sigue visible; oculta
        grupos que se quedan sin ningun hijo visible tras filtrar."""
        device_id = item.data(0, Qt.ItemDataRole.UserRole)
        if device_id is not None:
            visible = not needle or needle in item.text(0).lower()
            item.setHidden(not visible)
            return visible

        any_child_visible = False
        for i in range(item.childCount()):
            if self._filter_item(item.child(i), needle):
                any_child_visible = True
        item.setHidden(not any_child_visible)
        return any_child_visible
