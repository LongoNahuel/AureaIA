"""Arbol de dispositivos con buscador, agrupado por sitio (multisede),
para arrastrar una camara a un tile de la grilla de Vista en Vivo (o
asignarla con doble click al tile seleccionado). Si no hay sitios
definidos se mantiene el unico grupo "Camaras" de siempre. El
agrupamiento por tipo de dispositivo (NVR, control de acceso, etc.)
queda para cuando la app soporte esos tipos."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import SearchLineEdit, TreeWidget

from aurea_vms.core import app_state
from aurea_vms.models import repository
from aurea_vms.ui import icons

DEVICE_ID_MIME = "application/x-aurea-device-id"

# Sentinela: reload() sin argumento respeta el filtro global de sitio
# (None ya significa "todos", no sirve como default).
_USE_GLOBAL_FILTER = object()


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

    def reload(self, site_id: int | None | object = _USE_GLOBAL_FILTER) -> None:
        """Sin argumento aplica el filtro global de sitio de la topbar;
        con site_id explicito filtra a ese sitio; con None muestra todos,
        agrupados por sitio."""
        if site_id is _USE_GLOBAL_FILTER:
            site_id = app_state.current_site_id
        self.tree.clear()
        sites = repository.list_sites()
        devices = repository.list_devices(site_id=site_id)

        by_site: dict[int | None, list] = {}
        for device in devices:
            by_site.setdefault(device.site_id, []).append(device)

        if not sites:
            # Instalacion sin sitios definidos: mismo arbol plano de siempre.
            self._add_group(f"Cámaras ({len(devices)})", devices)
        else:
            for site in sites:
                if site_id is not None and site.id != site_id:
                    continue
                site_devices = by_site.pop(site.id, [])
                self._add_group(f"{site.name} ({len(site_devices)})", site_devices)
            unassigned = [d for devs in by_site.values() for d in devs]
            if unassigned:
                self._add_group(f"Sin sitio ({len(unassigned)})", unassigned)

    def _add_group(self, label: str, devices: list) -> None:
        group = QTreeWidgetItem([label])
        group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        self.tree.addTopLevelItem(group)

        camera_icon = icons.icon_live_view("#9aa3af")
        for device in devices:
            item = QTreeWidgetItem([f"{device.name}  ({device.ip})"])
            item.setIcon(0, camera_icon)
            item.setData(0, Qt.ItemDataRole.UserRole, device.id)
            group.addChild(item)

        group.setExpanded(True)

    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        device_id = item.data(0, Qt.ItemDataRole.UserRole)
        if device_id is not None:
            self.device_double_clicked.emit(device_id)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            visible_children = 0
            for i in range(group.childCount()):
                item = group.child(i)
                hidden = needle not in item.text(0).lower()
                item.setHidden(hidden)
                visible_children += not hidden
            # Un grupo sin coincidencias se oculta mientras se busca.
            group.setHidden(bool(needle) and visible_children == 0)
