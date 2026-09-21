from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, HeaderCardWidget

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent


class DoorStatePanel(HeaderCardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Puerta Abierta/Cerrada")
        self._device_id: int | None = None
        content = QWidget(self)
        self.viewLayout.addWidget(content)
        self.state_label = CaptionLabel("—", content)
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setStyleSheet("font-size: 30px; font-weight: 700; color: #22c55e;")
        self.score_label = CaptionLabel("Sin lectura", content)
        self.score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(content)
        layout.addWidget(self.state_label)
        layout.addWidget(self.score_label)
        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

    def set_device(self, device_id: int | None) -> None:
        self._device_id = device_id
        self.state_label.setText("—")
        self.score_label.setText("Sin lectura")

    def _on_detection(self, event: DetectionEvent) -> None:
        if event.device_id != self._device_id or event.analyzer_name != "door_state":
            return
        state = event.metrics.get("estado", "—")
        self.state_label.setText(str(state).upper())
        self.score_label.setText(f"Cambio: {event.metrics.get('cambio', 0):.3f}")
