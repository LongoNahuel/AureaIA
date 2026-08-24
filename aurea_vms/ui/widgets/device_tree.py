"""Arbol de dispositivos con buscador, para arrastrar una camara a un tile
de la grilla de Vista en Vivo (o asignarla con doble click al tile
seleccionado). Por ahora todos los dispositivos son camaras IP -- el
agrupamiento por tipo (NVR, control de acceso, etc.) queda para cuando la
app soporte esos tipos de dispositivo."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import SearchLineEdit, TreeWidget

from aurea_vms.models import repository
from aurea_vms.ui import icons

DEVICE_ID_MIME = "application/x-aurea-device-id"


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

    def reload(self) -> None:
        self.tree.clear()
        devices = repository.list_devices()

        root = QTreeWidgetItem([f"Cámaras ({len(devices)})"])
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        self.tree.addTopLevelItem(root)

        camera_icon = icons.icon_live_view("#9aa3af")
        for device in devices:
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
        root = self.tree.topLevelItem(0)
        if root is None:
            return
        for i in range(root.childCount()):
            item = root.child(i)
            item.setHidden(needle not in item.text(0).lower())
