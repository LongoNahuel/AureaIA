"""Paleta de comandos (Ctrl+K): buscador rapido de modulos y camaras.
Escribir filtra la lista; Enter (o doble click) ejecuta la accion
resaltada y cierra el dialogo."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, ListWidget, SearchLineEdit

from aurea_vms.models import repository

ACTION_OPEN_MODULE = "module"
ACTION_QUICK_VIEW = "quick_view"


class CommandPaletteDialog(QDialog):
    def __init__(
        self, modules: list[tuple[str, object, type]], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Buscador rápido")
        self.setFixedSize(420, 380)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)

        self._modules = modules
        self.action: tuple[str, int] | None = None  # (tipo, indice/id)

        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText("Buscar módulo o cámara...")
        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.returnPressed.connect(self._activate_current)

        self.list_widget = ListWidget(self)
        self.list_widget.itemActivated.connect(self._on_item_activated)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.list_widget)

        self._reload_items("")
        self.search_edit.setFocus()

    def _reload_items(self, needle: str) -> None:
        self.list_widget.clear()
        needle = needle.strip().lower()

        for index, (label, _icon_factory, _cls) in enumerate(self._modules):
            if needle and needle not in label.lower():
                continue
            item = QListWidgetItem(FluentIcon.ROBOT.icon(), f"Módulo: {label}")
            item.setData(Qt.ItemDataRole.UserRole, (ACTION_OPEN_MODULE, index))
            self.list_widget.addItem(item)

        for device in repository.list_devices():
            if needle and needle not in device.name.lower() and needle not in device.ip.lower():
                continue
            item = QListWidgetItem(FluentIcon.VIDEO.icon(), f"Cámara: {device.name} ({device.ip})")
            item.setData(Qt.ItemDataRole.UserRole, (ACTION_QUICK_VIEW, device.id))
            self.list_widget.addItem(item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _apply_filter(self, text: str) -> None:
        self._reload_items(text)

    def _activate_current(self) -> None:
        item = self.list_widget.currentItem()
        if item is not None:
            self._on_item_activated(item)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        self.action = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.list_widget.setFocus()
        super().keyPressEvent(event)
