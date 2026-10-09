"""Las camaras del sitio con su estado, en Inicio (Fase 2 de la interfaz,
2026-10-08): las desconectadas primero, para que salten a la vista. Un clic
abre la camara en Vista en Vivo."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aurea_vms.core import app_state
from aurea_vms.core.event_bus import event_bus
from aurea_vms.models import repository
from aurea_vms.ui.theme import STATUS_COLORS

REFRESH_MS = 5000
STATUS_TEXT = {"online": "En línea", "offline": "Desconectada"}
# Orden: lo que pide atencion arriba.
STATUS_ORDER = {"offline": 0, "unknown": 1, "online": 2}
_MUTED = "#8a93a0"


class _CameraRow(QFrame):
    clicked = Signal(int)

    def __init__(self, device, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device_id = device.id
        self.status = device.status if device.status in STATUS_TEXT else "unknown"
        self.setObjectName("cameraRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Ver en vivo")
        self.setStyleSheet(
            "#cameraRow { border-radius: 8px; }"
            "#cameraRow:hover { background: #212a37; }"
            "QLabel { background: transparent; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(10)
        color = STATUS_COLORS[self.status]
        dot = QLabel("●", self)
        dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        row.addWidget(dot)
        name = QLabel(device.name, self)
        name.setStyleSheet("color: #e5e7eb; font-size: 13px;")
        name.setToolTip(device.name)
        row.addWidget(name, stretch=1)
        state = QLabel(STATUS_TEXT.get(self.status, "Sin probar"), self)
        state.setStyleSheet(
            f"color: {color if self.status == 'offline' else _MUTED}; font-size: 11px;"
        )
        row.addWidget(state)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.device_id)
        super().mouseReleaseEvent(event)


class CameraStatusList(QWidget):
    camera_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.rows: list[_CameraRow] = []
        event_bus.site_filter_changed.connect(self._on_site_filter_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._timer.stop()
        super().hideEvent(event)

    def _on_site_filter_changed(self, _site_id: object) -> None:
        self.refresh()

    def refresh(self) -> None:
        devices = sorted(
            repository.list_devices(site_id=app_state.current_site_id),
            key=lambda d: (STATUS_ORDER.get(d.status, 1), d.name.lower()),
        )
        shown = [(row.device_id, row.status) for row in self.rows]
        wanted = [(d.id, d.status if d.status in STATUS_TEXT else "unknown") for d in devices]
        if shown == wanted and self.rows:
            return
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.rows = []
        if not devices:
            empty = QLabel("No hay cámaras en este sitio.", self)
            empty.setStyleSheet(f"color: {_MUTED}; font-size: 12px; padding: 8px;")
            self._layout.addWidget(empty)
            return
        for device in devices:
            row = _CameraRow(device, self)
            row.clicked.connect(self.camera_requested)
            self._layout.addWidget(row)
            self.rows.append(row)
        self._layout.addStretch(1)
